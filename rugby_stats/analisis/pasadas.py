"""Las pasadas de análisis, una función por paso.

Cada pasada recibe lo que necesita, hace sus pedidos y devuelve objetos del
dominio. Ninguna escribe en la base: eso lo hace el pipeline, para que se
pueda cortar y retomar.
"""

from __future__ import annotations

import logging
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

    for i, (ini, fin) in enumerate(rangos, 1):
        faltantes = sorted(objetivo - set(previas))
        if not faltantes:
            log.info("Ya tengo ficha de los %d jugadores. Corto.", len(objetivo))
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
            fichas.append(ficha)
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
                ),
                video_uri=_uri(partido),
                inicio_s=ini,
                fin_s=fin,
                fps=v.fps,
                resolucion=v.resolucion,
                esquema=prompts.ESQUEMA_EVENTOS,
                etiqueta=f"eventos {ini:.0f}-{fin:.0f}",
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
            eventos.append(e)
            if _es_contacto_dudoso(e, cfg) or (
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
) -> list[dict[str, Any]]:
    """Vuelve a mirar cada contacto dudoso con muchos más cuadros por segundo.

    Acá se resuelve quién tackleó y si el portador avanzó, que son los dos
    datos que a 2 cuadros por segundo no salen. El criterio de positivo o
    negativo lo aplica ``reglas.clasificar_tackle`` sobre la observación.

    Devuelve un contacto por elemento: ``{"t_segundos", "desde_s", "hasta_s",
    "eventos"}``. Quien llama decide cómo se combinan con lo que ya había.
    """
    v = cfg.velocidad("contacto")
    a_revisar = agrupar_contactos(a_revisar)
    resueltos: list[dict[str, Any]] = []
    propio = partido.equipo_propio.nombre
    rival = partido.equipo_rival.nombre
    numeros_propios = reglas.numeros_del_plantel(jugadores_propios)
    numeros_rivales = reglas.numeros_del_plantel(jugadores_rivales)

    for i, item in enumerate(a_revisar, 1):
        t = float(item["t_segundos"])
        ini = max(0.0, t - v.margen_antes_s)
        fin = t + v.margen_despues_s + v.ventana_s / 2
        if progreso:
            progreso(f"Contactos {i}/{len(a_revisar)}", i / max(1, len(a_revisar)))

        intentos = 0
        datos: dict[str, Any] = {}
        fps = v.fps
        while intentos <= cfg.reintentos_sin_clasificar:
            r = cliente.pedir(
                Pedido(
                    prompt=prompts.prompt_contacto(
                        partido.equipo_propio,
                        partido.equipo_rival,
                        jugadores_propios,
                        jugadores_rivales,
                        fichas,
                        ini,
                        fin,
                        str(item.get("pista") or ""),
                    ),
                    video_uri=_uri(partido),
                    inicio_s=ini,
                    fin_s=fin,
                    fps=min(24.0, fps),
                    resolucion=v.resolucion,
                    esquema=prompts.ESQUEMA_CONTACTO,
                    etiqueta=f"contacto {t:.1f} intento {intentos}",
                )
            )
            leido = _json(r, f"contacto {t:.1f}")
            datos = leido if isinstance(leido, dict) else {}
            # "Sin clasificar no vale": si no salió ni el tackleador ni el
            # avance, se amplía la ventana y se suben los cuadros por segundo.
            falta = not datos.get("tackleadores") or datos.get(
                "avance_portador"
            ) in (None, "", "no_se_ve")
            if not falta:
                break
            intentos += 1
            ini = max(0.0, ini - 1.0)
            fin = fin + 1.0
            fps = min(24.0, fps * 1.5)

        resueltos.append(
            {
                "t_segundos": t,
                "desde_s": float(item.get("desde_s", t)),
                "hasta_s": float(item.get("hasta_s", t)),
                "eventos": _contacto_a_eventos(
                    datos, t, partido, cfg, numeros_propios, numeros_rivales
                ),
            }
        )

    return resueltos


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
