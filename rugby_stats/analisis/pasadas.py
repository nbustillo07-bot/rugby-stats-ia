"""Las pasadas de análisis, una función por paso.

Cada pasada recibe lo que necesita, hace sus pedidos y devuelve objetos del
dominio. Ninguna escribe en la base: eso lo hace el pipeline, para que se
pueda cortar y retomar.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from ..config import Config
from ..gemini import prompts
from ..gemini.cliente import Pedido
from ..gemini.video import ventanas
from ..modelos import Equipo, Evento, Ficha, Jugador, Partido, Tramo
from . import reglas

log = logging.getLogger(__name__)


def _alinear_tiempos(
    filas: list[dict[str, Any]], campos: tuple[str, ...], ini: float, fin: float,
    que: str,
) -> list[dict[str, Any]]:
    """Si los tiempos vinieron relativos al fragmento, los pasa al video.

    ``campos`` son las claves que guardan tiempos ("t_segundos", o
    "inicio_s" y "fin_s").
    """
    filas = [f for f in filas if isinstance(f, dict)]
    tiempos = [f.get(c) for f in filas for c in campos]
    corrimiento, relativos = reglas.corregir_tiempos(tiempos, ini, fin)
    if relativos:
        log.warning(
            "%s: el modelo devolvió tiempos relativos al fragmento; les sumo %.0f s.",
            que, corrimiento,
        )
        for f in filas:
            for c in campos:
                if isinstance(f.get(c), (int, float)):
                    f[c] = float(f[c]) + corrimiento
    return filas


def _lista(datos: Any, clave: str) -> list[Any]:
    """La lista pedida, aunque el modelo la devuelva suelta o venga otra cosa."""
    if isinstance(datos, list):
        return datos
    if isinstance(datos, dict):
        valor = datos.get(clave)
        return valor if isinstance(valor, list) else []
    return []


def _json(respuesta: Any, etiqueta: str) -> Any:
    """El JSON de una respuesta, o None si no se pudo leer.

    Una ventana cuya respuesta vino cortada o vacía no tiene que tirar abajo
    el análisis de todo el partido: se anota y se sigue con la siguiente.
    """
    try:
        return respuesta.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("No pude leer la respuesta de %s: %s", etiqueta, exc)
        return None


def referencias_visuales(
    fichas: dict[int, Ficha],
    jugadores: list[Jugador],
    maximo: int = 23,
    fotos: list[Ficha] | None = None,
    por_jugador: int = 1,
    carpeta: Path | None = None,
    por_hoja: int = 4,
    album: dict[int, list[str]] | None = None,
    del_album: int = 2,
) -> list[tuple[str, str]]:
    """Las fotos de cada jugador, como (etiqueta, ruta), para el modelo.

    Con ``por_jugador`` = 1 va una foto por jugador: primero las confirmadas
    por una persona, después las de más confianza.

    Con más de una (y una ``carpeta`` donde armar las hojas) se eligen hasta
    ``por_jugador`` fotos de momentos distintos de cada uno —el mismo jugador
    visto de frente, de espalda y de costado se reconoce mucho mejor— y se
    juntan de a ``por_hoja`` jugadores por imagen, con el nombre escrito
    arriba de cada fila, para no pasar el límite de imágenes por pedido.
    """
    por_numero = {j.numero: j for j in jugadores}
    todas = list(fotos) if fotos else list(fichas.values())
    con_imagen = [f for f in todas if f.imagen and Path(f.imagen).exists()]
    con_imagen.sort(key=lambda f: (not f.confirmada, -f.confianza, f.t_segundos))

    elegidas: dict[int, list[Ficha]] = {}
    for f in con_imagen:
        lista = elegidas.setdefault(f.jugador_numero, [])
        if len(lista) >= max(1, por_jugador):
            continue
        # Momentos distintos: dos fotos del mismo segundo no suman nada.
        if any(abs(f.t_segundos - o.t_segundos) < 15 for o in lista):
            continue
        lista.append(f)
    # Fotos de partidos anteriores y del álbum del club: ayudan desde el
    # primer pedido, aunque hoy tenga otro número o botines.
    viejas: dict[int, list[str]] = {
        n: [r for r in rutas if Path(r).exists()][:del_album]
        for n, rutas in (album or {}).items()
        if n in por_numero
    }
    viejas = {n: r for n, r in viejas.items() if r}
    numeros = sorted(set(elegidas) | set(viejas))[:maximo]

    def etiqueta(n: int) -> str:
        j = por_numero.get(n)
        return j.etiqueta if j else str(n)

    if por_jugador <= 1 or carpeta is None:
        salida = [
            (etiqueta(n), str(elegidas[n][0].imagen) if elegidas.get(n) else viejas[n][0])
            for n in numeros
        ]
        salida.sort(key=lambda r: r[0])
        return salida

    from .. import cuadros as cuadros_mod

    salida = []
    for k in range(0, len(numeros), por_hoja):
        grupo = numeros[k:k + por_hoja]
        marcar = bool(viejas)
        filas = [
            (
                etiqueta(n),
                [(str(f.imagen), "HOY" if marcar else "") for f in elegidas.get(n, [])]
                + [(r, "ANTES") for r in viejas.get(n, [])],
            )
            for n in grupo
        ]
        firma = hashlib.sha256(
            json.dumps([[e, [list(r) for r in rs]] for e, rs in filas]).encode()
        ).hexdigest()[:16]
        destino = Path(carpeta) / f"hoja_{firma}.jpg"
        if not destino.exists():
            try:
                cuadros_mod.hoja_referencias(filas, destino)
            except Exception as exc:  # noqa: BLE001 - sin hoja se sigue igual
                log.warning("No pude armar la hoja de fotos: %s", exc)
                continue
        if destino.exists():
            salida.append((" · ".join(etiqueta(n) for n in grupo), str(destino)))
    return salida


def _referencias(partido: Partido, fichas, jugadores, cfg: Config, fotos=None, album=None):
    """Las referencias que van en los pedidos de eventos y contactos."""
    return referencias_visuales(
        fichas, jugadores, fotos=fotos, por_jugador=cfg.fotos_por_jugador,
        carpeta=cfg.dir_video.parent / "fichas" / "hojas", album=album,
    )


def recortar_ficha(
    partido: Partido, ficha: Ficha, caja: Any, cfg: Config
) -> str:
    """Guarda el recorte del jugador en el cuadro de la ficha. "" si no se pudo."""
    video = partido.video.ruta_local
    if not video or not Path(video).exists() or not isinstance(caja, (list, tuple)):
        return ""
    if len(caja) != 4:
        return ""
    from .. import cuadros as cuadros_mod

    carpeta = cfg.dir_video.parent / "fichas"
    base = f"p{partido.id}_{ficha.equipo}_{ficha.jugador_numero}_{int(ficha.t_segundos * 10)}"
    base = "".join(c if c.isalnum() or c in "_-" else "_" for c in base)
    try:
        cuadro = cuadros_mod.un_cuadro(
            video, ficha.t_segundos, carpeta / "cuadros" / f"{base}.jpg",
            1280, partido.video.recorte_desde_s or 0.0,
        )
        recorte = cuadros_mod.recortar(cuadro, caja, carpeta / f"{base}.jpg")
    except Exception as exc:  # noqa: BLE001 - sin recorte se sigue igual
        log.warning("No pude recortar la ficha del %s: %s", ficha.jugador_numero, exc)
        return ""
    return str(recorte) if recorte else ""


def _uri(partido: Partido) -> str:
    uri = partido.video.uri_subida or partido.video.referencia
    if not uri:
        raise RuntimeError("El partido no tiene video preparado.")
    return uri


def _tramo_a_analizar(partido: Partido) -> tuple[float, float]:
    desde = partido.analizar_desde_s or 0.0
    hasta = partido.analizar_hasta_s
    if hasta is None:
        hasta = partido.video.duracion_s
    if hasta is None:
        raise RuntimeError(
            "No sé cuánto dura el video. Instalá ffmpeg/yt-dlp o poné el "
            "tramo a analizar a mano en la pantalla del partido."
        )
    return float(desde), float(hasta)


# ---------------------------------------------------------------------------
# Paso 1 — pasada gruesa
# ---------------------------------------------------------------------------


def pasada_gruesa(
    cliente: Any, partido: Partido, cfg: Config, progreso=None
) -> list[Tramo]:
    """Separa juego de tiempo muerto y lee el marcador.

    Es la pasada que hace barato todo lo demás: en el tramo de prueba, menos
    de un tercio del video fue juego real, así que las pasadas finas se
    corren sobre mucho menos material.
    """
    v = cfg.velocidad("gruesa")
    desde, hasta = _tramo_a_analizar(partido)
    ventanas_ = ventanas(desde, hasta, v.ventana_s, cfg.solape_s)
    tramos: list[Tramo] = []

    for i, (ini, fin) in enumerate(ventanas_, 1):
        if progreso:
            progreso(f"Pasada gruesa {i}/{len(ventanas_)}", i / len(ventanas_))
        r = cliente.pedir(
            Pedido(
                prompt=prompts.prompt_gruesa(
                    partido.equipo_propio, partido.equipo_rival, ini, fin
                ),
                video_uri=_uri(partido),
                inicio_s=ini,
                fin_s=fin,
                fps=v.fps,
                resolucion=v.resolucion,
                esquema=prompts.ESQUEMA_GRUESA,
                etiqueta=f"gruesa {ini:.0f}-{fin:.0f}",
                ancho=v.ancho,
                nivel=cfg.nivel_de("gruesa"),
            )
        )
        crudos = _alinear_tiempos(
            _lista(_json(r, f"gruesa {ini:.0f}"), "tramos"),
            ("inicio_s", "fin_s"), ini, fin, "Pasada gruesa",
        )
        for d in crudos:
            try:
                t = Tramo(
                    inicio_s=float(d["inicio_s"]),
                    fin_s=float(d["fin_s"]),
                    tipo="juego" if d.get("tipo") == "juego" else "muerto",
                    reloj_inicio=str(d.get("reloj") or ""),
                    marcador_propio=_entero(d.get("marcador_local")),
                    marcador_rival=_entero(d.get("marcador_visitante")),
                    confianza=reglas.normalizar_confianza(d.get("confianza")),
                )
            except (KeyError, TypeError, ValueError):
                log.warning("Tramo mal formado, lo salto: %s", d)
                continue
            # Se recorta a la ventana pedida: el modelo a veces se pasa.
            t.inicio_s = max(ini, t.inicio_s)
            t.fin_s = min(fin, t.fin_s)
            if t.fin_s > t.inicio_s:
                tramos.append(t)

    tramos = _unir_tramos(tramos)

    # Si se leyó el reloj en algún tramo, se calcula el desfase una vez.
    if partido.video.desfase_s == 0.0:
        for t in tramos:
            d = reglas.calcular_desfase(t.reloj_inicio, t.inicio_s)
            if d is not None:
                partido.video.desfase_s = d
                log.info("Desfase entre reloj y video: %.0f s", d)
                break
    return tramos


def _entero(valor: Any) -> int | None:
    if valor is None or isinstance(valor, bool):
        return None
    try:
        return int(float(valor))
    except (TypeError, ValueError):
        return None


def _unir_tramos(tramos: list[Tramo]) -> list[Tramo]:
    """Ordena y pega los tramos del mismo tipo que se solapan o se tocan."""
    if not tramos:
        return []
    tramos = sorted(tramos, key=lambda t: (t.inicio_s, t.fin_s))
    salida = [tramos[0]]
    for t in tramos[1:]:
        ultimo = salida[-1]
        if t.tipo == ultimo.tipo and t.inicio_s <= ultimo.fin_s + 1.0:
            ultimo.fin_s = max(ultimo.fin_s, t.fin_s)
            ultimo.confianza = min(ultimo.confianza, t.confianza)
            if not ultimo.reloj_inicio:
                ultimo.reloj_inicio = t.reloj_inicio
            if ultimo.marcador_propio is None:
                ultimo.marcador_propio = t.marcador_propio
                ultimo.marcador_rival = t.marcador_rival
        elif t.inicio_s < ultimo.fin_s:
            t.inicio_s = ultimo.fin_s
            if t.fin_s > t.inicio_s:
                salida.append(t)
        else:
            salida.append(t)
    return salida


# ---------------------------------------------------------------------------
# Paso 2 — fichas
# ---------------------------------------------------------------------------


def pasada_fichas(
    cliente: Any,
    partido: Partido,
    jugadores: list[Jugador],
    equipo: Equipo,
    cfg: Config,
    tramos_muertos: list[Tramo] | None = None,
    fichas_previas: dict[int, Ficha] | None = None,
    progreso=None,
    fotos_previas: list[Ficha] | None = None,
) -> list[Ficha]:
    """Arma las fichas de apariencia leyendo el número en la espalda.

    Mira primero los tiempos muertos: ahí están las pausas (line, scrum,
    penal, la salida) donde el número se lee. Si no hay tramos cargados,
    recorre todo el video.
    """
    v = cfg.velocidad("fichas")
    desde, hasta = _tramo_a_analizar(partido)
    previas = dict(fichas_previas or {})

    if tramos_muertos:
        # Las pausas más largas primero: son las que mejor sirven.
        candidatas = sorted(
            [t for t in tramos_muertos if t.duracion_s >= 5 and t.fin_s > desde],
            key=lambda t: t.duracion_s,
            reverse=True,
        )
        rangos = [
            (max(desde, t.inicio_s), min(hasta, t.inicio_s + v.ventana_s))
            for t in candidatas
        ]
    else:
        rangos = ventanas(desde, hasta, v.ventana_s, 0)

    rangos = [(a, b) for a, b in rangos if b > a]
    objetivo = {j.numero for j in jugadores}
    fichas: list[Ficha] = []
    # Con Claude se juntan varias fotos de cada jugador, de momentos
    # distintos. Con Gemini (que no recibe fotos) alcanza con una ficha.
    con_fotos = bool(getattr(cliente, "necesita_video_local", False))
    por_jugador = cfg.fotos_por_jugador if con_fotos else 1
    tope_ventanas = int(cfg.get("claude.max_ventanas_fichas", 10) or 10) if con_fotos else len(rangos)
    momentos: dict[int, list[float]] = {}
    for f in list(fotos_previas or []) + list(previas.values()):
        if not con_fotos or (f.imagen and Path(f.imagen).exists()):
            momentos.setdefault(f.jugador_numero, []).append(f.t_segundos)

    def cuantas(n: int) -> int:
        ts = sorted(set(momentos.get(n, [])))
        distintas: list[float] = []
        for x in ts:
            if all(abs(x - y) >= 15 for y in distintas):
                distintas.append(x)
        return len(distintas)

    rangos = rangos[:tope_ventanas]
    for i, (ini, fin) in enumerate(rangos, 1):
        faltantes = sorted(n for n in objetivo if cuantas(n) < por_jugador)
        if not faltantes:
            log.info("Ya tengo %d foto(s) de los %d jugadores. Corto.",
                     por_jugador, len(objetivo))
            break
        if progreso:
            progreso(
                f"Fichas {i}/{len(rangos)} — faltan {len(faltantes)}",
                i / max(1, len(rangos)),
            )
        r = cliente.pedir(
            Pedido(
                prompt=prompts.prompt_fichas(equipo, jugadores, ini, fin, faltantes),
                video_uri=_uri(partido),
                inicio_s=ini,
                fin_s=fin,
                fps=v.fps,
                resolucion=v.resolucion,
                esquema=prompts.ESQUEMA_FICHAS,
                etiqueta=f"fichas {ini:.0f}-{fin:.0f}",
                ancho=v.ancho,
                nivel=cfg.nivel_de("fichas"),
            )
        )
        crudas = _alinear_tiempos(
            _lista(_json(r, f"fichas {ini:.0f}"), "lecturas"),
            ("t_segundos",), ini, fin, "Fichas",
        )
        for d in crudas:
            numero = reglas.limpiar_numero(d.get("numero"))
            if numero is None or numero not in objetivo:
                continue
            crudos_rasgos = d.get("rasgos")
            rasgos = {
                str(k): str(val).strip()
                for k, val in (
                    crudos_rasgos.items() if isinstance(crudos_rasgos, dict) else []
                )
                if val is not None
                and str(val).strip()
                and str(val).strip().lower() not in ("no se ve", "null", "none", "n/a")
            }
            try:
                t_ficha = float(d.get("t_segundos"))
            except (TypeError, ValueError):
                t_ficha = ini
            ficha = Ficha(
                jugador_numero=numero,
                equipo=equipo.nombre,
                t_segundos=min(max(t_ficha, ini), fin),
                rasgos=rasgos,
                confianza=reglas.normalizar_confianza(d.get("confianza")),
                nota=str(d.get("nota") or ""),
            )
            ficha.imagen = recortar_ficha(partido, ficha, d.get("caja"), cfg)
            fichas.append(ficha)
            if not con_fotos or ficha.imagen:
                momentos.setdefault(numero, []).append(ficha.t_segundos)
            anterior = previas.get(numero)
            if anterior is None or ficha.confianza >= anterior.confianza:
                previas[numero] = ficha

    return fichas


# ---------------------------------------------------------------------------
# Paso 3 — eventos
# ---------------------------------------------------------------------------


def pasada_eventos(
    cliente: Any,
    partido: Partido,
    jugadores_propios: list[Jugador],
    jugadores_rivales: list[Jugador],
    fichas: dict[int, Ficha],
    cfg: Config,
    tramos_juego: list[Tramo] | None = None,
    transcripcion: list[dict[str, Any]] | None = None,
    progreso=None,
    fotos: list[Ficha] | None = None,
    album: dict[int, list[str]] | None = None,
) -> tuple[list[Evento], list[dict[str, Any]]]:
    """Detecta eventos a velocidad media.

    Devuelve los eventos y la lista de contactos a re-examinar a velocidad
    alta: a 2 cuadros por segundo se ve el contacto pero no siempre quién
    entró, que es justamente el punto débil del sistema.
    """
    v = cfg.velocidad("juego")
    desde, hasta = _tramo_a_analizar(partido)

    if tramos_juego:
        rangos: list[tuple[float, float]] = []
        for t in tramos_juego:
            a, b = max(desde, t.inicio_s), min(hasta, t.fin_s)
            if b - a < 2:
                continue
            rangos.extend(ventanas(a, b, v.ventana_s, cfg.solape_s))
    else:
        rangos = ventanas(desde, hasta, v.ventana_s, cfg.solape_s)

    eventos: list[Evento] = []
    a_revisar: list[dict[str, Any]] = []
    propio = partido.equipo_propio.nombre
    rival = partido.equipo_rival.nombre
    referencias = _referencias(partido, fichas, jugadores_propios, cfg, fotos, album)
    solo_tackles = cfg.solo_tackles

    for i, (ini, fin) in enumerate(rangos, 1):
        if progreso:
            progreso(f"Eventos {i}/{len(rangos)}", i / max(1, len(rangos)))
        relato = _relato_de(transcripcion, ini, fin)
        r = cliente.pedir(
            Pedido(
                prompt=prompts.prompt_eventos(
                    partido.equipo_propio,
                    partido.equipo_rival,
                    jugadores_propios,
                    jugadores_rivales,
                    fichas,
                    ini,
                    fin,
                    relato,
                    solo_propio=cfg.solo_equipo_propio,
                    solo_tackles=solo_tackles,
                ),
                video_uri=_uri(partido),
                inicio_s=ini,
                fin_s=fin,
                fps=v.fps,
                resolucion=v.resolucion,
                esquema=prompts.ESQUEMA_EVENTOS,
                etiqueta=f"eventos {ini:.0f}-{fin:.0f}",
                ancho=v.ancho,
                referencias=referencias,
                nivel=cfg.nivel_de("eventos"),
            )
        )
        crudos = _alinear_tiempos(
            _lista(_json(r, f"eventos {ini:.0f}"), "eventos"),
            ("t_segundos",), ini, fin, "Eventos",
        )
        for d in crudos:
            e = _dict_a_evento(d, partido, cfg)
            if e is None:
                continue
            # Dentro de la ventana, descontando el solape para no duplicar.
            if not (ini - 0.5 <= e.t_segundos <= fin + 0.5):
                continue
            if cfg.solo_equipo_propio and e.equipo == rival:
                # Del rival no se guarda nada. Pero un carry o un tackle rival
                # es un contacto en el que participa alguien del equipo
                # propio: se manda a re-examinar para atribuirlo.
                if e.accion in ACCIONES_DE_CONTACTO:
                    a_revisar.append(
                        {
                            "t_segundos": e.t_segundos,
                            "pista": f"{e.accion} del rival: falta saber qué "
                            "jugador propio participó",
                        }
                    )
                continue
            if solo_tackles and e.accion not in ("Tackle", "Tackle errado"):
                continue
            eventos.append(e)
            # En modo tackles se re-examinan TODOS: es lo único que se mide y
            # el re-examen con zoom es donde se decide quién fue.
            if (solo_tackles and e.accion in ACCIONES_DE_CONTACTO) or _es_contacto_dudoso(e, cfg) or (
                d.get("revisar_contacto") and e.accion in ACCIONES_DE_CONTACTO
            ):
                a_revisar.append(
                    {
                        "t_segundos": e.t_segundos,
                        "pista": f"{e.accion}"
                        + (f" de {e.jugador_numero}" if e.jugador_numero else "")
                        + f" ({e.equipo})",
                        "evento_tmp": e,
                    }
                )

    eventos = _quitar_duplicados(eventos)
    return eventos, a_revisar


#: Acciones que el re-examen a velocidad alta sabe resolver. Solo estas: el
#: re-examen devuelve un portador y sus tackleadores, así que aplicarlo a un
#: ruck o a una pelota perdida inventaría un carry y un tackle que no hubo.
ACCIONES_DE_CONTACTO = ("Tackle", "Tackle errado", "Carry")


def _es_contacto_dudoso(e: Evento, cfg: Config) -> bool:
    return e.accion in ACCIONES_DE_CONTACTO and (
        e.jugador_numero is None
        or not e.resultado
        or e.confianza < cfg.umbral_confianza_revision
    )


def agrupar_contactos(
    a_revisar: list[dict[str, Any]], ventana_s: float = 2.0
) -> list[dict[str, Any]]:
    """Junta los avisos que son el mismo contacto.

    A velocidad media, un mismo choque suele salir como dos eventos (el carry
    del portador y el tackle del defensor), los dos marcados para revisar. Es
    un solo contacto y se re-examina una sola vez.
    """
    grupos: list[dict[str, Any]] = []
    for item in sorted(a_revisar, key=lambda x: float(x["t_segundos"])):
        t = float(item["t_segundos"])
        if grupos and t - grupos[-1]["_ultimo"] <= ventana_s:
            g = grupos[-1]
            g["_tiempos"].append(t)
            g["_ultimo"] = t
            if item.get("pista"):
                g["_pistas"].append(str(item["pista"]))
        else:
            grupos.append(
                {
                    "_tiempos": [t],
                    "_ultimo": t,
                    "_pistas": [str(item["pista"])] if item.get("pista") else [],
                }
            )
    return [
        {
            "t_segundos": sum(g["_tiempos"]) / len(g["_tiempos"]),
            "desde_s": min(g["_tiempos"]),
            "hasta_s": max(g["_tiempos"]),
            "pista": "; ".join(dict.fromkeys(g["_pistas"])),
        }
        for g in grupos
    ]


def _dict_a_evento(d: dict[str, Any], partido: Partido, cfg: Config) -> Evento | None:
    try:
        t = float(d["t_segundos"])
        accion = str(d["accion"])
    except (KeyError, TypeError, ValueError):
        return None
    propio = partido.equipo_propio.nombre
    rival = partido.equipo_rival.nombre
    secundarios = [
        n
        for n in (reglas.limpiar_numero(x) for x in (d.get("numeros_secundarios") or []))
        if n is not None
    ]
    metros = d.get("metros")
    return Evento(
        t_segundos=t,
        accion=accion,
        resultado=reglas.normalizar_resultado(accion, d.get("resultado")),
        equipo=reglas.nombre_equipo(str(d.get("equipo") or ""), propio, rival),
        jugador_numero=reglas.limpiar_numero(d.get("numero")),
        jugadores_secundarios=secundarios,
        metros=float(metros)
        if isinstance(metros, (int, float)) and not isinstance(metros, bool)
        else None,
        confianza=reglas.normalizar_confianza(d.get("confianza")),
        reloj=reglas.reloj_desde_video(t, partido.video.desfase_s),
        nota=str(d.get("nota") or ""),
        fuente="video",
        propuesta_original=dict(d),
    )


def _relato_de(
    transcripcion: list[dict[str, Any]] | None, ini: float, fin: float
) -> str:
    if not transcripcion:
        return ""
    trozos = [
        f.get("texto", "")
        for f in transcripcion
        if f.get("fin_s", 0) >= ini - 2 and f.get("inicio_s", 0) <= fin + 2
    ]
    return " ".join(t for t in trozos if t)[:4000]


def _quitar_duplicados(eventos: list[Evento], ventana_s: float = 1.5) -> list[Evento]:
    """El solape entre ventanas hace que algún evento salga dos veces."""
    salida: list[Evento] = []
    for e in sorted(eventos, key=lambda x: (x.t_segundos, x.accion)):
        dup = next(
            (
                o
                for o in salida
                if o.accion == e.accion
                and o.equipo == e.equipo
                and o.jugador_numero == e.jugador_numero
                and abs(o.t_segundos - e.t_segundos) <= ventana_s
            ),
            None,
        )
        if dup is None:
            salida.append(e)
        elif e.confianza > dup.confianza:
            salida[salida.index(dup)] = e
    return salida


# ---------------------------------------------------------------------------
# Paso 4 — el contacto, a velocidad alta
# ---------------------------------------------------------------------------


def revisar_contactos(
    cliente: Any,
    partido: Partido,
    jugadores_propios: list[Jugador],
    jugadores_rivales: list[Jugador],
    fichas: dict[int, Ficha],
    cfg: Config,
    a_revisar: list[dict[str, Any]],
    progreso=None,
    fotos: list[Ficha] | None = None,
    album: dict[int, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Vuelve a mirar cada contacto con muchos más cuadros por segundo.

    Acá se resuelve quién tackleó y si el portador avanzó, que son los dos
    datos que a 2 cuadros por segundo no salen. El criterio de positivo o
    negativo lo aplica ``reglas.clasificar_tackle`` sobre la observación.

    Con Claude, cada contacto se mira así:

    1. El modelo liviano marca DÓNDE está el contacto en cada cuadro.
    2. Al fuerte se le manda esa zona recortada y ampliada (sacada del video
       en alta), desde 1,5 s antes hasta 6 s después: cuando el tackleador se
       levanta y vuelve a la línea se le suele ver el número.
    3. Si el fuerte duda de quién fue, se le pregunta al experto. Si
       coinciden, sube la confianza; si no, va a revisión con los candidatos
       de los dos.

    Después se aplica la lógica del juego: nadie tacklea si no está en
    cancha, y nadie hace dos tackles en lugares distintos en el mismo segundo.

    Devuelve un contacto por elemento: ``{"t_segundos", "desde_s", "hasta_s",
    "eventos", "descartado"}``. Quien llama decide cómo se combinan con lo que
    ya había.
    """
    v = cfg.velocidad("contacto")
    a_revisar = agrupar_contactos(a_revisar)
    resueltos: list[dict[str, Any]] = []
    referencias = _referencias(partido, fichas, jugadores_propios, cfg, fotos, album)
    numeros_propios = reglas.numeros_del_plantel(jugadores_propios)
    numeros_rivales = reglas.numeros_del_plantel(jugadores_rivales)
    local = bool(getattr(cliente, "necesita_video_local", False))
    zoom_cfg = cfg.zoom_contacto if local else {}
    niveles = getattr(cliente, "niveles", None) or {}
    hay_experto = local and "experto" in niveles
    umbral_experto = float(cfg.get("claude.umbral_segunda_opinion", 0.6) or 0.6)

    for i, item in enumerate(a_revisar, 1):
        t = float(item["t_segundos"])
        ini = max(0.0, t - v.margen_antes_s)
        fin = t + v.margen_despues_s + v.ventana_s / 2
        pista_txt = str(item.get("pista") or "")
        if progreso:
            progreso(f"Contactos {i}/{len(a_revisar)}", i / max(1, len(a_revisar)))
        minuto = (t + (partido.video.desfase_s or 0.0)) / 60
        en_cancha = reglas.en_cancha(jugadores_propios, minuto)

        zoom = None
        if zoom_cfg:
            pista = _localizar(cliente, partido, cfg, t, ini, fin, pista_txt)
            if pista:
                zoom = {**zoom_cfg, "pista": pista}

        def pedir_contacto(ini_: float, fin_: float, fps_: float, nivel: str, etiqueta: str):
            r = cliente.pedir(
                Pedido(
                    prompt=prompts.prompt_contacto(
                        partido.equipo_propio,
                        partido.equipo_rival,
                        jugadores_propios,
                        jugadores_rivales,
                        fichas,
                        ini_,
                        fin_,
                        pista_txt,
                        solo_propio=cfg.solo_equipo_propio,
                        en_cancha=en_cancha,
                        t_contacto=t,
                    ),
                    video_uri=_uri(partido),
                    inicio_s=ini_,
                    fin_s=fin_,
                    fps=min(24.0, fps_),
                    resolucion=v.resolucion,
                    esquema=prompts.ESQUEMA_CONTACTO,
                    etiqueta=etiqueta,
                    ancho=v.ancho,
                    referencias=referencias,
                    nivel=nivel,
                    zoom=zoom,
                )
            )
            leido = _json(r, etiqueta)
            return leido if isinstance(leido, dict) else {}

        intentos = 0
        datos: dict[str, Any] = {}
        fps = v.fps
        # Con zoom no se repite con más cuadros: para eso está la segunda
        # opinión, que sale más a cuenta.
        maximo_intentos = 0 if zoom else cfg.reintentos_sin_clasificar
        while True:
            datos = pedir_contacto(ini, fin, fps, cfg.nivel_de("contactos"),
                                   f"contacto {t:.1f} intento {intentos}")
            # "Sin clasificar no vale": si no salió ni el tackleador ni el
            # avance, se amplía la ventana y se suben los cuadros por segundo.
            falta = not datos.get("tackleadores") or datos.get(
                "avance_portador"
            ) in (None, "", "no_se_ve")
            if not falta or intentos >= maximo_intentos:
                break
            intentos += 1
            ini = max(0.0, ini - 1.0)
            fin = fin + 1.0
            fps = min(24.0, fps * 1.5)

        if hay_experto and datos.get("hay_tackle_propio", True) is not False and (
            reglas.normalizar_confianza(datos.get("confianza_identificacion", datos.get("confianza")))
            < umbral_experto
            or not datos.get("tackleadores")
        ):
            otra = pedir_contacto(ini, fin, fps, "experto", f"contacto {t:.1f} experto")
            if otra:
                datos = _juntar_opiniones(datos, otra)

        datos["candidatos"] = reglas.unir_candidatos(datos.get("candidatos"), datos.get("tackleadores"))
        if en_cancha is not None:
            datos["en_cancha"] = en_cancha
        descartado = (
            cfg.solo_tackles
            and datos.get("hay_tackle_propio") is False
            and reglas.normalizar_confianza(datos.get("confianza")) >= 0.6
        )
        eventos = [] if descartado else _contacto_a_eventos(
            datos, t, partido, cfg, numeros_propios, numeros_rivales
        )
        if datos.get("coinciden") is False:
            # Las dos miradas no coinciden: no se cuenta hasta que una persona
            # elija entre los candidatos.
            for e in eventos:
                if e.accion in ("Tackle", "Tackle errado"):
                    e.pendiente = True
        if en_cancha is not None:
            for e in eventos:
                if e.equipo == partido.equipo_propio.nombre and e.jugador_numero is not None \
                        and e.jugador_numero not in en_cancha:
                    e.pendiente = True
                    e.nota = (e.nota + f" | el {e.jugador_numero} no estaba en cancha").strip(" |")
        resueltos.append(
            {
                "t_segundos": t,
                "desde_s": float(item.get("desde_s", t)),
                "hasta_s": float(item.get("hasta_s", t)),
                "eventos": eventos,
                "descartado": descartado,
            }
        )

    _marcar_imposibles(resueltos, partido.equipo_propio.nombre)
    return resueltos


def _localizar(
    cliente: Any, partido: Partido, cfg: Config, t: float, ini: float, fin: float,
    pista_txt: str,
) -> list[list[Any]]:
    """Le pide al liviano dónde está el contacto en cada cuadro."""
    try:
        vl = cfg.velocidad("localizar")
    except KeyError:
        return []
    r = cliente.pedir(
        Pedido(
            prompt=prompts.prompt_localizar(
                partido.equipo_propio, partido.equipo_rival, t, ini, fin, pista_txt
            ),
            video_uri=_uri(partido),
            inicio_s=ini,
            fin_s=fin,
            fps=vl.fps,
            esquema=prompts.ESQUEMA_LOCALIZAR,
            etiqueta=f"ubicar contacto {t:.1f}",
            ancho=vl.ancho,
            nivel="liviano",
        )
    )
    datos = _json(r, f"ubicar {t:.1f}")
    if not isinstance(datos, dict):
        return []
    from .. import cuadros as cuadros_mod

    pista = []
    for d in _lista(datos, "puntos"):
        if not isinstance(d, dict):
            continue
        caja = cuadros_mod.caja_valida(d.get("caja"))
        try:
            tt = float(d.get("t_segundos"))
        except (TypeError, ValueError):
            continue
        if caja and ini - 1 <= tt <= fin + 1:
            pista.append([round(tt, 2), [round(x, 4) for x in caja]])
    return pista


def _numeros(lista: Any) -> list[int]:
    if not isinstance(lista, list):
        lista = [lista] if lista is not None else []
    return list(dict.fromkeys(n for n in (reglas.limpiar_numero(x) for x in lista) if n is not None))


def _juntar_opiniones(primera: dict[str, Any], experto: dict[str, Any]) -> dict[str, Any]:
    """Combina la respuesta del fuerte con la del experto.

    - Si los dos dicen los mismos tackleadores: se queda eso y la confianza
      de identificación sube a la mayor de las dos.
    - Si no coinciden: manda el experto para lo que pasó, pero la
      identificación queda baja (va a revisión) y los candidatos de los dos
      quedan para que una persona elija con un clic.
    """
    a, b = _numeros(primera.get("tackleadores")), _numeros(experto.get("tackleadores"))
    conf_a = reglas.normalizar_confianza(primera.get("confianza_identificacion", primera.get("confianza")))
    conf_b = reglas.normalizar_confianza(experto.get("confianza_identificacion", experto.get("confianza")))
    salida = dict(experto)
    salida["candidatos"] = reglas.unir_candidatos(
        experto.get("candidatos"), experto.get("tackleadores"),
        primera.get("candidatos"), primera.get("tackleadores"),
    )
    salida["opiniones"] = {"fuerte": a, "experto": b}
    if a and set(a) == set(b):
        salida["confianza_identificacion"] = max(conf_a, conf_b)
        salida["nota"] = (str(experto.get("nota") or "") + " | coinciden las dos miradas").strip(" |")
        salida["coinciden"] = True
    else:
        salida["confianza_identificacion"] = min(conf_a, conf_b, 0.5)
        if not b and a:
            # El experto no se animó; el fuerte sí: se conserva su lectura,
            # floja, para que la confirme una persona.
            salida["tackleadores"] = a
        salida["nota"] = (
            str(experto.get("nota") or "")
            + f" | no coinciden: {a or '¿?'} contra {b or '¿?'}"
        ).strip(" |")
        salida["coinciden"] = False
    return salida


def _marcar_imposibles(resueltos: list[dict[str, Any]], propio: str, ventana_s: float = 3.0) -> None:
    """Nadie tacklea en dos contactos distintos con menos de 3 s de diferencia."""
    tackles = [
        (c["t_segundos"], e)
        for c in resueltos
        for e in c["eventos"]
        if e.accion in ("Tackle", "Tackle errado") and e.equipo == propio and e.jugador_numero is not None
    ]
    for i, (t1, e1) in enumerate(tackles):
        for t2, e2 in tackles[i + 1:]:
            if e1 is e2 or e1.jugador_numero != e2.jugador_numero:
                continue
            if 0 < abs(t1 - t2) < ventana_s:
                for e in (e1, e2):
                    e.pendiente = True
                    if "dos tackles" not in e.nota:
                        e.nota = (e.nota + f" | el {e.jugador_numero} aparece en dos "
                                  "tackles casi al mismo tiempo").strip(" |")


def _contacto_a_eventos(
    datos: dict[str, Any],
    t: float,
    partido: Partido,
    cfg: Config,
    numeros_propios: set[int],
    numeros_rivales: set[int],
) -> list[Evento]:
    """Un contacto resuelto se abre en varios eventos: el carry y los tackles."""
    propio = partido.equipo_propio.nombre
    rival = partido.equipo_rival.nombre
    eventos: list[Evento] = []

    avance = reglas._plano(datos.get("avance_portador")).replace(" ", "_")
    metros = datos.get("metros_avance")
    metros = (
        float(metros)
        if isinstance(metros, (int, float)) and not isinstance(metros, bool)
        else None
    )
    conf = reglas.normalizar_confianza(datos.get("confianza"))
    conf_id = (
        reglas.normalizar_confianza(datos.get("confianza_identificacion"))
        if datos.get("confianza_identificacion") is not None
        else conf
    )
    nota = str(datos.get("nota") or "")
    reloj = reglas.reloj_desde_video(t, partido.video.desfase_s)

    eq_portador = reglas.nombre_equipo(
        str(datos.get("portador_equipo") or ""), propio, rival
    )
    eq_tackle = reglas.nombre_equipo(
        str(datos.get("tackleadores_equipo") or ""), propio, rival
    )
    if eq_portador and not eq_tackle:
        eq_tackle = rival if eq_portador == propio else propio
    if eq_tackle and not eq_portador:
        eq_portador = rival if eq_tackle == propio else propio

    # El carry del portador.
    portador = reglas.limpiar_numero(datos.get("portador"))
    resultado_carry = {
        "avanza": "avanza",
        "se_detiene": "detenido",
        "retrocede": "retrocede",
    }.get(avance, "")
    if portador is not None or resultado_carry:
        eventos.append(
            Evento(
                t_segundos=t,
                accion="Carry",
                equipo=eq_portador,
                jugador_numero=portador,
                resultado=resultado_carry,
                metros=metros,
                confianza=min(conf, conf_id) if portador is not None else conf * 0.5,
                fuente="video",
                reloj=reloj,
                nota=nota,
                propuesta_original=dict(datos),
            )
        )

    # Un evento de tackle por cada tackleador.
    crudos_t = datos.get("tackleadores")
    if not isinstance(crudos_t, list):
        crudos_t = [crudos_t] if crudos_t is not None else []
    tackleadores = list(
        dict.fromkeys(
            n for n in (reglas.limpiar_numero(x) for x in crudos_t) if n is not None
        )
    )
    completado = bool(datos.get("tackle_completado", True))
    resultado_tackle = reglas.clasificar_tackle(avance, cfg, metros)

    if not tackleadores:
        # Hubo contacto pero no se sabe quién: va a la cola, no se descarta.
        eventos.append(
            Evento(
                t_segundos=t,
                accion="Tackle" if completado else "Tackle errado",
                equipo=eq_tackle,
                jugador_numero=None,
                resultado=resultado_tackle if completado else "errado",
                confianza=conf * 0.4,
                fuente="video",
                reloj=reloj,
                pendiente=True,
                nota=(nota + " | no se pudo identificar al tackleador").strip(" |"),
                propuesta_original=dict(datos),
            )
        )
    else:
        for n in tackleadores:
            eventos.append(
                Evento(
                    t_segundos=t,
                    accion="Tackle" if completado else "Tackle errado",
                    equipo=eq_tackle,
                    jugador_numero=n,
                    resultado=resultado_tackle if completado else "errado",
                    jugadores_secundarios=[x for x in tackleadores if x != n],
                    confianza=min(conf, conf_id),
                    fuente="video",
                    reloj=reloj,
                    nota=nota,
                    propuesta_original=dict(datos),
                )
            )

    if datos.get("pelota_adelante"):
        eventos.append(
            Evento(
                t_segundos=t,
                accion="Pelota perdida",
                equipo=eq_portador,
                jugador_numero=portador,
                resultado="knock-on",
                confianza=min(conf, conf_id) if portador else conf * 0.5,
                fuente="video",
                reloj=reloj,
                nota=nota,
                propuesta_original=dict(datos),
            )
        )

    if cfg.solo_tackles:
        eventos = [e for e in eventos if e.accion in ("Tackle", "Tackle errado")]
    if cfg.solo_equipo_propio:
        # Del rival no se guarda nada. Lo que quedó sin equipo se conserva:
        # va a la cola para que una persona diga de quién era.
        eventos = [e for e in eventos if e.equipo != rival]
    return eventos


# ---------------------------------------------------------------------------
# Paso 5 — relato
# ---------------------------------------------------------------------------


def pasada_relato(
    cliente: Any,
    partido: Partido,
    jugadores_propios: list[Jugador],
    jugadores_rivales: list[Jugador],
    cfg: Config,
    progreso=None,
) -> list[dict[str, Any]]:
    """Transcribe el relato pasándole los apellidos y apodos como vocabulario."""
    if getattr(cliente, "necesita_video_local", False):
        return _relato_por_subtitulos(
            cliente, partido, jugadores_propios, jugadores_rivales, cfg, progreso
        )
    desde, hasta = _tramo_a_analizar(partido)
    # El relato se pide en ventanas grandes: es audio, sale barato.
    rangos = ventanas(desde, hasta, 600, 10)
    fragmentos: list[dict[str, Any]] = []

    for i, (ini, fin) in enumerate(rangos, 1):
        if progreso:
            progreso(f"Relato {i}/{len(rangos)}", i / max(1, len(rangos)))
        r = cliente.pedir(
            Pedido(
                prompt=prompts.prompt_relato(
                    jugadores_propios, jugadores_rivales, ini, fin
                ),
                video_uri=_uri(partido),
                inicio_s=ini,
                fin_s=fin,
                fps=0.1,  # el audio es lo que importa; mandamos casi sin cuadros
                resolucion="MEDIA_RESOLUTION_LOW",
                esquema=prompts.ESQUEMA_RELATO,
                etiqueta=f"relato {ini:.0f}-{fin:.0f}",
                nivel=cfg.nivel_de("relato"),
            )
        )
        crudos = _alinear_tiempos(
            _lista(_json(r, f"relato {ini:.0f}"), "fragmentos"),
            ("inicio_s", "fin_s"), ini, fin, "Relato",
        )
        for d in crudos:
            try:
                fragmentos.append(
                    {
                        "inicio_s": float(d["inicio_s"]),
                        "fin_s": float(d["fin_s"]),
                        "texto": str(d.get("texto") or ""),
                        "jugadores_nombrados": [
                            n
                            for n in (
                                reglas.limpiar_numero(x)
                                for x in (d.get("jugadores_nombrados") or [])
                            )
                            if n is not None
                        ],
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
    return fragmentos


def _relato_por_subtitulos(
    cliente: Any,
    partido: Partido,
    jugadores_propios: list[Jugador],
    jugadores_rivales: list[Jugador],
    cfg: Config,
    progreso=None,
) -> list[dict[str, Any]]:
    """Para quien no escucha audio (Claude): subtítulos de YouTube + corrección.

    Los subtítulos automáticos traen la frase y su momento, pero erran los
    apellidos. Se le pasa el texto al modelo liviano con el plantel como
    vocabulario para que corrija los nombres y marque a quién nombra. Es
    texto solo: sale muy barato.
    """
    from .. import subtitulos
    from ..gemini import video as video_mod

    if partido.video.tipo != "youtube":
        log.info("El relato por subtítulos solo funciona con videos de YouTube.")
        return []
    desde, hasta = _tramo_a_analizar(partido)
    if progreso:
        progreso("Bajando los subtítulos del relato", 0.1)
    crudos = subtitulos.relato_desde_youtube(
        partido.video.referencia, cfg.dir_video / "subtitulos",
        video_mod._cmd_ytdlp(), desde, hasta,
    )
    if not crudos:
        return []

    corregidos: list[dict[str, Any]] = []
    # En tandas de unos diez minutos de relato.
    tandas: list[list[dict[str, Any]]] = [[]]
    for f in crudos:
        if tandas[-1] and f["inicio_s"] - tandas[-1][0]["inicio_s"] > 600:
            tandas.append([])
        tandas[-1].append(f)
    for i, tanda in enumerate(tandas, 1):
        if progreso:
            progreso(f"Corrigiendo nombres del relato {i}/{len(tandas)}", i / len(tandas))
        try:
            r = cliente.pedir(
                Pedido(
                    prompt=prompts.prompt_relato_corregir(
                        jugadores_propios, jugadores_rivales, tanda
                    ),
                    esquema=prompts.ESQUEMA_RELATO,
                    etiqueta=f"relato {tanda[0]['inicio_s']:.0f}",
                    nivel=cfg.nivel_de("relato"),
                )
            )
            devueltos = _lista(_json(r, "relato"), "fragmentos")
        except Exception as exc:  # noqa: BLE001 - sin corrección vale el crudo
            log.warning("No pude corregir el relato (%s): queda sin corregir.", exc)
            devueltos = []
        if not devueltos:
            corregidos.extend(tanda)
            continue
        for d in devueltos:
            try:
                corregidos.append(
                    {
                        "inicio_s": float(d["inicio_s"]),
                        "fin_s": float(d["fin_s"]),
                        "texto": str(d.get("texto") or ""),
                        "jugadores_nombrados": [
                            n
                            for n in (
                                reglas.limpiar_numero(x)
                                for x in (d.get("jugadores_nombrados") or [])
                            )
                            if n is not None
                        ],
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
    return corregidos


# ---------------------------------------------------------------------------
# Paso 6 — cambios
# ---------------------------------------------------------------------------


def pasada_cambios(
    cliente: Any,
    partido: Partido,
    jugadores_propios: list[Jugador],
    jugadores_rivales: list[Jugador],
    cfg: Config,
    transcripcion: list[dict[str, Any]] | None = None,
    progreso=None,
) -> list[dict[str, Any]]:
    """Detecta cambios por tres lados: número en cancha, relato y línea de touch."""
    desde, hasta = _tramo_a_analizar(partido)
    rangos = ventanas(desde, hasta, 600, 10)
    cambios: list[dict[str, Any]] = []
    propio = partido.equipo_propio.nombre
    rival = partido.equipo_rival.nombre

    for i, (ini, fin) in enumerate(rangos, 1):
        if progreso:
            progreso(f"Cambios {i}/{len(rangos)}", i / max(1, len(rangos)))
        r = cliente.pedir(
            Pedido(
                prompt=prompts.prompt_cambios(
                    partido.equipo_propio,
                    partido.equipo_rival,
                    jugadores_propios,
                    jugadores_rivales,
                    ini,
                    fin,
                    _relato_de(transcripcion, ini, fin),
                ),
                video_uri=_uri(partido),
                inicio_s=ini,
                fin_s=fin,
                fps=0.5,
                resolucion="MEDIA_RESOLUTION_LOW",
                esquema=prompts.ESQUEMA_CAMBIOS,
                etiqueta=f"cambios {ini:.0f}-{fin:.0f}",
                nivel=cfg.nivel_de("cambios"),
            )
        )
        crudos = _alinear_tiempos(
            _lista(_json(r, f"cambios {ini:.0f}"), "cambios"),
            ("t_segundos",), ini, fin, "Cambios",
        )
        for d in crudos:
            try:
                t = float(d["t_segundos"])
            except (KeyError, TypeError, ValueError):
                continue
            cambios.append(
                {
                    "t_segundos": t,
                    "minuto": round((t + partido.video.desfase_s) / 60, 1),
                    "equipo": reglas.nombre_equipo(
                        str(d.get("equipo") or ""), propio, rival
                    ),
                    "entra": reglas.limpiar_numero(d.get("entra")),
                    "sale": reglas.limpiar_numero(d.get("sale")),
                    "como_lo_supe": str(d.get("como_lo_supe") or ""),
                    "confianza": reglas.normalizar_confianza(d.get("confianza")),
                    "nota": str(d.get("nota") or ""),
                }
            )
    return cambios
