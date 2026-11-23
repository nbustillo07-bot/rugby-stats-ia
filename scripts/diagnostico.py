#!/usr/bin/env python3
"""Diagnóstico contra la API real de Gemini.

Corre cada tipo de pedido del sistema una vez, sobre un tramo corto del video,
y deja anotado todo: la respuesta cruda, los tokens, el tiempo y los errores.
No analiza un partido: sirve para saber **qué anda y qué no** antes de gastar
cuota en una corrida entera, y para poder arreglar lo que falle mirando el
archivo que genera.

    python scripts/diagnostico.py
    python scripts/diagnostico.py --video "https://www.youtube.com/watch?v=..."
    python scripts/diagnostico.py --partido 1

Gasta poco: una docena de pedidos sobre unos minutos de video en total.

El resultado queda en ``salidas/diagnostico/``. La clave de la API nunca se
escribe en esos archivos.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

for _flujo in (sys.stdout, sys.stderr):
    try:
        _flujo.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from rugby_stats.analisis import reglas  # noqa: E402
from rugby_stats.config import cargar_config  # noqa: E402
from rugby_stats.db import BaseDatos, ruta_por_defecto  # noqa: E402
from rugby_stats.gemini import prompts  # noqa: E402
from rugby_stats.gemini import video as video_mod  # noqa: E402
from rugby_stats.gemini.cliente import ClienteGemini, Pedido  # noqa: E402
from rugby_stats.modelos import Equipo, Jugador  # noqa: E402

ESQUEMA_RELOJ = {
    "type": "object",
    "properties": {
        "hay_cartel": {"type": "boolean"},
        "reloj": {"type": "string"},
        "t_segundos_del_video": {"type": "number"},
        "marcador": {"type": "string"},
        "hay_juego": {"type": "boolean"},
        "que_se_ve": {"type": "string"},
    },
    "required": ["hay_cartel", "reloj", "que_se_ve"],
}


class Diagnostico:
    def __init__(self, cliente: ClienteGemini, uri: str, clave: str):
        self.cliente = cliente
        self.uri = uri
        self._clave = clave
        self.pasos: list[dict] = []
        self.al_terminar_paso = None
        self.modelo_elegido = ""
        self.modelos_que_responden: list[str] = []
        cliente.al_avisar = lambda texto: print(f"      ({self._limpio(texto)})", flush=True)

    def _limpio(self, texto: str) -> str:
        """Por las dudas: la clave no sale en ningún archivo."""
        return texto.replace(self._clave, "<CLAVE>") if self._clave else texto

    def paso(self, nombre: str, pedido: Pedido) -> dict:
        print(f"  · {nombre}…", flush=True)
        registro: dict = {
            "paso": nombre,
            "inicio_s": pedido.inicio_s,
            "fin_s": pedido.fin_s,
            "fps": pedido.fps,
            "resolucion": pedido.resolucion if pedido.video_uri else None,
            "largo_prompt": len(pedido.prompt),
            "nivel": pedido.nivel,
        }
        t0 = time.monotonic()
        reintentos_antes = self.cliente.contadores.reintentos
        errores_antes = len(self.cliente.errores_vistos)

        def cerrar() -> None:
            registro["reintentos"] = self.cliente.contadores.reintentos - reintentos_antes
            registro["errores_de_google"] = [
                self._limpio(e) for e in self.cliente.errores_vistos[errores_antes:]
            ]
            self.pasos.append(registro)
            if self.al_terminar_paso:
                self.al_terminar_paso()

        try:
            r = self.cliente.pedir(pedido)
        except Exception as exc:  # noqa: BLE001 - queremos anotar cualquier cosa
            registro["ok"] = False
            registro["error_tipo"] = type(exc).__name__
            registro["error"] = self._limpio(str(exc))[:3000]
            registro["traza"] = self._limpio(traceback.format_exc())[-3000:]
            registro["segundos"] = round(time.monotonic() - t0, 1)
            print(f"    FALLÓ: {registro['error'][:220]}")
            cerrar()
            return registro

        registro["segundos"] = round(time.monotonic() - t0, 1)
        registro["modelo"] = r.modelo
        registro["tokens_entrada"] = r.tokens_entrada
        registro["tokens_salida"] = r.tokens_salida
        registro["respuesta_cruda"] = self._limpio(r.texto)[:20000]
        if pedido.inicio_s is not None and pedido.fin_s is not None and r.tokens_entrada:
            largo = max(1.0, pedido.fin_s - pedido.inicio_s)
            registro["tokens_por_segundo_de_video"] = round(r.tokens_entrada / largo, 1)
        try:
            registro["json"] = r.json()
            registro["ok"] = True
            print(
                f"    ok ({registro['segundos']} s, {r.tokens_entrada or '?'} tokens, "
                f"modelo {r.modelo})"
            )
        except Exception as exc:  # noqa: BLE001
            registro["ok"] = False
            registro["error_tipo"] = "RespuestaNoEsJSON"
            registro["error"] = str(exc)[:500]
            print("    contestó, pero no es JSON válido")
        cerrar()
        return registro


def _plantel_generico(equipo: str) -> list[Jugador]:
    return [
        Jugador(numero=n, nombre=f"Jugador {n}", equipo=equipo, titular=n <= 15)
        for n in range(1, 24)
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="Diagnóstico contra la API real.")
    ap.add_argument("--video", help="URL de YouTube. Si falta, usa la del último partido.")
    ap.add_argument("--partido", type=int, help="Usar este partido de la base.")
    ap.add_argument(
        "--desde", help="mm:ss del VIDEO donde probar. Si falta, lo busca solo."
    )
    ap.add_argument("--config")
    args = ap.parse_args()

    cfg = cargar_config(args.config)
    if cfg.proveedor == "claude":
        # Con Claude no hay cupos que averiguar: la prueba útil es medir
        # cuánto cuesta de verdad y qué contesta.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from prueba_claude import main as prueba_claude

        return prueba_claude()
    print("=" * 60)
    print("  rugby-stats-ia — diagnóstico contra la API real")
    print("=" * 60)

    if not cfg.hay_api_key:
        print("\n✗ No hay GEMINI_API_KEY en el archivo .env.")
        print("  Hacé doble clic en 2_poner_clave.bat y volvé a probar.")
        return 1

    # -- de dónde sale el video y el plantel ------------------------------
    base = BaseDatos(ruta_por_defecto())
    partido = None
    if args.partido:
        partido = base.partido(args.partido)
    elif not args.video:
        partidos = base.partidos()
        partido = partidos[0] if partidos else None

    if args.video:
        url = args.video
    elif partido is not None:
        url = partido.video.referencia
    else:
        print("\n✗ No hay ningún partido cargado y no me pasaste un video.")
        print('  Probá con: python scripts/diagnostico.py --video "<link>"')
        return 1

    if not video_mod.id_youtube(url):
        print("\n✗ Este diagnóstico está pensado para un link de YouTube.")
        return 1

    if partido is not None:
        propio, rival = partido.equipo_propio, partido.equipo_rival
        jug_propios = base.plantel(partido.id, propio.nombre) or _plantel_generico(
            propio.nombre
        )
        jug_rivales = base.plantel(partido.id, rival.nombre)
        origen_plantel = (
            "del partido cargado"
            if base.plantel(partido.id, propio.nombre)
            else "genérico (el partido no tiene plantel)"
        )
    else:
        propio = Equipo(nombre="Equipo propio", kit="", numero_legible="media")
        rival = Equipo(nombre="Equipo rival", kit="", numero_legible="media")
        jug_propios = _plantel_generico(propio.nombre)
        jug_rivales = []
        origen_plantel = "genérico (no hay partido cargado)"

    print(f"\nVideo   : {url}")
    print(f"Equipos : {propio.nombre} vs {rival.nombre}")
    print(f"Plantel : {len(jug_propios)} jugadores, {origen_plantel}")
    print(f"Modelos : fuertes {', '.join((cfg.niveles or {}).get('fuerte', [cfg.modelo]))}")
    print(f"          livianos {', '.join((cfg.niveles or {}).get('liviano', []))}\n")

    niveles = cfg.niveles or {
        "fuerte": [cfg.modelo] + cfg.modelos_alternativos,
        "liviano": [cfg.modelo] + cfg.modelos_alternativos,
    }
    cliente = ClienteGemini(
        api_key=cfg.api_key,
        modelo=cfg.modelo,
        alternativas=cfg.modelos_alternativos,
        rpm=8,
        # Esperas cortas: acá interesa saber rápido qué anda, no insistir.
        esperas_429=[5, 15, 30],
        max_reintentos=2,
        dir_cache=cfg.dir_cache,
        usar_cache=False,  # el diagnóstico siempre va de verdad a la API
        temperatura=cfg.temperatura,
        niveles=niveles,
        rpm_por_nivel=cfg.rpm_por_nivel,
        respaldo=cfg.respaldo_de_niveles,
        archivo_cupos=cfg.archivo_cupos,
    )
    d = Diagnostico(cliente, url, cfg.api_key or "")
    sello = datetime.now().strftime("%Y%m%d_%H%M%S")
    d.al_terminar_paso = lambda: _guardar(
        d, cfg, url, propio, rival, origen_plantel, sello=sello, parcial=True
    )

    # -- 1. qué modelos están respondiendo --------------------------------
    print("1. La clave, y qué modelos gratis responden ahora")
    todos = list(dict.fromkeys(niveles["fuerte"] + niveles["liviano"]))
    responden: list[str] = []
    clave_mal = False
    for modelo in todos:
        if cliente.cupos.agotado(modelo):
            falta = cliente.cupos.proxima_renovacion([modelo])
            print(f"  · {modelo}: sin cupo por hoy"
                  + (f" (vuelve en {falta / 3600:.0f} h)" if falta else "")
                  + ". No lo pruebo.")
            d.pasos.append({"paso": f"texto simple con {modelo}", "ok": False,
                            "salteado": True, "error": "sin cupo por hoy (ya se sabía)"})
            continue
        cliente.niveles["_prueba"] = [modelo]
        r = d.paso(
            f"texto simple con {modelo}",
            Pedido(
                prompt='Respondé únicamente con este JSON: {"ok": true}',
                esquema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
                etiqueta="diagnóstico texto",
                nivel="_prueba",
            ),
        )
        if r["ok"]:
            responden.append(modelo)
        elif "api key" in str(r.get("error", "")).lower():
            print("\n  Google rechazó la clave. Revisala con 2_poner_clave.bat.")
            clave_mal = True
            break  # con la clave mal no tiene sentido probar otros modelos
    cliente.niveles.pop("_prueba", None)
    d.modelos_que_responden = responden

    fuertes = [m for m in niveles["fuerte"] if m in responden]
    livianos = [m for m in niveles["liviano"] if m in responden]
    if clave_mal or not responden:
        print("\n  Ningún modelo contestó. No sigo con el video.")
        return _guardar(
            d, cfg, url, propio, rival, origen_plantel, sello=sello, cortado=True
        )
    print(f"\n  Fuertes que responden : {', '.join(fuertes) or 'ninguno'}")
    print(f"  Livianos que responden: {', '.join(livianos) or 'ninguno'}")
    if not fuertes:
        print("  (Los pasos que piden un modelo fuerte van a fallar hoy: es el "
              "cupo diario, no un error.)")
    d.modelo_elegido = (fuertes or livianos)[0]

    # -- 2. encontrar el reloj --------------------------------------------
    print("\n2. ¿Puede mirar el video? ¿Dónde está el reloj del partido?")
    lecturas: list[tuple[float, str]] = []
    puntos = (
        [reglas.segundos_desde_reloj(args.desde) or 0.0]
        if args.desde
        else [900.0, 2100.0]
    )
    video_anda = False
    for t in puntos:
        r = d.paso(
            f"reloj en {reglas.reloj_desde_video(t)} del video",
            Pedido(
                prompt=(
                    "Mirá este fragmento de la transmisión de un partido de rugby. "
                    'En "hay_cartel" decí si hay un cartel en pantalla con el reloj '
                    'del partido. En "reloj" poné lo que marca ese reloj en el '
                    'ÚLTIMO cuadro que ves, en formato mm:ss (vacío si no hay). En '
                    '"t_segundos_del_video" poné a qué segundo del video completo '
                    'corresponde ese cuadro. En "marcador" el tanteador tal como se '
                    've. En "hay_juego" si la pelota está en juego. En "que_se_ve" '
                    "una frase corta."
                ),
                video_uri=url,
                inicio_s=t,
                fin_s=t + 12,
                fps=0.5,
                resolucion="MEDIA_RESOLUTION_MEDIUM",
                esquema=ESQUEMA_RELOJ,
                etiqueta="diagnóstico reloj", nivel=cfg.nivel_de("gruesa"),
            ),
        )
        if r["ok"]:
            video_anda = True
            datos = r.get("json") or {}
            reloj = str(datos.get("reloj") or "").strip()
            if datos.get("hay_cartel") and reglas.segundos_desde_reloj(reloj) is not None:
                lecturas.append((t + 12, reloj))

    if not video_anda:
        print("\n  Ningún pedido de video funcionó. Mirá el archivo de diagnóstico.")
        return _guardar(
            d, cfg, url, propio, rival, origen_plantel, sello=sello, cortado=True
        )

    # Elegir dónde probar: si se leyó el reloj, un rato de juego conocido.
    desfase = None
    if args.desde:
        t_prueba = puntos[0]
    elif lecturas:
        # La lectura más temprana con reloj es la más confiable (primer tiempo).
        t_ref, reloj_ref = lecturas[0]
        desfase = reglas.calcular_desfase(reloj_ref, t_ref)
        t_prueba = t_ref
        print(f"\n  Reloj leído: {reloj_ref} a los {reglas.reloj_desde_video(t_ref)} "
              f"del video (desfase {desfase:+.0f} s).")
    else:
        t_prueba = 1500.0
        print("\n  No pude leer ningún reloj en pantalla. Pruebo desde el minuto 25.")

    # -- 3. los pedidos de verdad -----------------------------------------
    print(f"\n3. Los pedidos del sistema, desde {reglas.reloj_desde_video(t_prueba)} del video")
    v_gruesa = cfg.velocidad("gruesa")
    v_juego = cfg.velocidad("juego")
    v_fichas = cfg.velocidad("fichas")
    v_contacto = cfg.velocidad("contacto")

    d.paso(
        "pasada gruesa (2 min)",
        Pedido(
            prompt=prompts.prompt_gruesa(propio, rival, t_prueba, t_prueba + 120),
            video_uri=url, inicio_s=t_prueba, fin_s=t_prueba + 120,
            fps=v_gruesa.fps, resolucion=v_gruesa.resolucion,
            esquema=prompts.ESQUEMA_GRUESA, etiqueta="diagnóstico gruesa", nivel=cfg.nivel_de("gruesa"),
        ),
    )
    d.paso(
        "relato (2 min)",
        Pedido(
            prompt=prompts.prompt_relato(jug_propios, jug_rivales, t_prueba, t_prueba + 120),
            video_uri=url, inicio_s=t_prueba, fin_s=t_prueba + 120,
            fps=0.1, resolucion="MEDIA_RESOLUTION_LOW",
            esquema=prompts.ESQUEMA_RELATO, etiqueta="diagnóstico relato", nivel=cfg.nivel_de("relato"),
        ),
    )
    d.paso(
        "fichas (1 min)",
        Pedido(
            prompt=prompts.prompt_fichas(propio, jug_propios, t_prueba, t_prueba + 60),
            video_uri=url, inicio_s=t_prueba, fin_s=t_prueba + 60,
            fps=v_fichas.fps, resolucion=v_fichas.resolucion,
            esquema=prompts.ESQUEMA_FICHAS, etiqueta="diagnóstico fichas", nivel=cfg.nivel_de("fichas"),
        ),
    )
    r_ev = d.paso(
        "eventos (1 min)",
        Pedido(
            prompt=prompts.prompt_eventos(
                propio, rival, jug_propios, jug_rivales, {}, t_prueba, t_prueba + 60
            ),
            video_uri=url, inicio_s=t_prueba, fin_s=t_prueba + 60,
            fps=v_juego.fps, resolucion=v_juego.resolucion,
            esquema=prompts.ESQUEMA_EVENTOS, etiqueta="diagnóstico eventos", nivel=cfg.nivel_de("eventos"),
        ),
    )

    # El contacto: sobre el primer tackle o carry que haya salido, si salió.
    t_contacto = t_prueba + 20
    if r_ev.get("ok"):
        for e in (r_ev.get("json") or {}).get("eventos", []):
            if e.get("accion") in ("Tackle", "Carry") and isinstance(
                e.get("t_segundos"), (int, float)
            ):
                t_contacto = float(e["t_segundos"])
                break
    d.paso(
        f"contacto a {v_contacto.fps:.0f} cuadros/s (4 s)",
        Pedido(
            prompt=prompts.prompt_contacto(
                propio, rival, jug_propios, jug_rivales, {},
                t_contacto - 1.5, t_contacto + 2.5,
            ),
            video_uri=url, inicio_s=max(0.0, t_contacto - 1.5), fin_s=t_contacto + 2.5,
            fps=v_contacto.fps, resolucion=v_contacto.resolucion,
            esquema=prompts.ESQUEMA_CONTACTO, etiqueta="diagnóstico contacto", nivel=cfg.nivel_de("contactos"),
        ),
    )
    d.paso(
        "cambios (2 min)",
        Pedido(
            prompt=prompts.prompt_cambios(
                propio, rival, jug_propios, jug_rivales, t_prueba, t_prueba + 120
            ),
            video_uri=url, inicio_s=t_prueba, fin_s=t_prueba + 120,
            fps=0.5, resolucion="MEDIA_RESOLUTION_LOW",
            esquema=prompts.ESQUEMA_CAMBIOS, etiqueta="diagnóstico cambios", nivel=cfg.nivel_de("cambios"),
        ),
    )

    return _guardar(
        d, cfg, url, propio, rival, origen_plantel, sello=sello, desfase=desfase
    )


def _guardar(
    d: Diagnostico, cfg, url, propio, rival, origen_plantel,
    cortado: bool = False, desfase: float | None = None,
    sello: str | None = None, parcial: bool = False,
) -> int:
    """Escribe el informe. Con ``parcial=True`` lo hace calladito, después de
    cada paso: si la corrida se corta o se cierra la ventana, lo hecho queda."""
    carpeta = cfg.dir_salidas / "diagnostico"
    carpeta.mkdir(parents=True, exist_ok=True)
    sello = sello or datetime.now().strftime("%Y%m%d_%H%M%S")

    try:
        import google.genai as genai_mod

        version_sdk = getattr(genai_mod, "__version__", "?")
    except Exception:  # noqa: BLE001
        version_sdk = "?"

    bien = [p for p in d.pasos if p.get("ok")]
    mal = [p for p in d.pasos if not p.get("ok") and not p.get("salteado")]
    contadores = d.cliente.contadores
    informe = {
        "cuando": datetime.now().isoformat(timespec="seconds"),
        "video": url,
        "equipos": [propio.nombre, rival.nombre],
        "plantel": origen_plantel,
        "modelo_configurado": cfg.modelo,
        "sdk_google_genai": version_sdk,
        "python": platform.python_version(),
        "sistema": platform.platform(),
        "desfase_reloj_s": desfase,
        "modelo_elegido": getattr(d, "modelo_elegido", ""),
        "modelos_que_responden": getattr(d, "modelos_que_responden", []),
        "uso_de_hoy": dict(d.cliente.cupos.uso),
        "agotados_hoy": sorted(d.cliente.cupos.agotados),
        "termino": not parcial,
        "cortado_antes_de_terminar": cortado,
        "pasos_bien": len(bien),
        "pasos_mal": len(mal),
        "errores_429": contadores.errores_429,
        "saturaciones": contadores.saturaciones,
        "reintentos": contadores.reintentos,
        "pasos": d.pasos,
    }
    ruta_json = carpeta / f"diagnostico_{sello}.json"
    ruta_json.write_text(
        json.dumps(informe, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    if parcial:
        return 0

    lineas = [
        "rugby-stats-ia — diagnóstico contra la API real",
        f"{informe['cuando']}  ·  modelo {cfg.modelo}  ·  SDK {version_sdk}",
        f"Video: {url}",
        "",
        f"RESULTADO: {len(bien)} pasos bien, {len(mal)} mal"
        + ("  (se cortó antes de terminar)" if cortado else ""),
        "",
    ]
    for p in d.pasos:
        marca = "OK  " if p.get("ok") else ("--  " if p.get("salteado") else "MAL ")
        extra = ""
        if p.get("tokens_entrada"):
            extra += f"  {p['tokens_entrada']:,} tokens"
        if p.get("tokens_por_segundo_de_video"):
            extra += f"  ({p['tokens_por_segundo_de_video']:.0f}/s de video)"
        if p.get("reintentos"):
            extra += f"  [{p['reintentos']} reintentos]"
        lineas.append(f"{marca} {p['paso']:42} {p.get('segundos', '?'):>6} s{extra}")
        if not p.get("ok"):
            lineas.append(f"      -> {p.get('error_tipo')}: {str(p.get('error'))[:400]}")
    lineas.append("")

    caros = [
        p for p in d.pasos
        if p.get("tokens_por_segundo_de_video", 0) > 1000 and p.get("fps", 0) <= 2
    ]
    if caros:
        lineas += [
            "ATENCIÓN: hay pedidos que gastan muchísimos tokens por segundo de video.",
            "Es el audio del video entero viajando en cada pedido de YouTube.",
            "Para un partido completo conviene bajar el video (yt-dlp) y subirlo.",
            "",
        ]
    if contadores.saturaciones:
        lineas += [
            f"Google contestó 'saturado' {contadores.saturaciones} veces. No es tu",
            "cuenta ni tu compu: es el servicio gratis en hora pico. Conviene",
            "analizar en otro horario (a la mañana suele andar mejor).",
            "",
        ]
    lineas.append(f"Detalle completo: {ruta_json.name}")
    ruta_txt = carpeta / f"diagnostico_{sello}.txt"
    ruta_txt.write_text("\n".join(lineas), encoding="utf-8")

    print("\n" + "=" * 60)
    print("\n".join(lineas[4:]))
    print("=" * 60)
    print(f"\nQuedó guardado en:\n  {ruta_txt}\n  {ruta_json}")
    return 0 if not mal else 2


if __name__ == "__main__":
    raise SystemExit(main())
