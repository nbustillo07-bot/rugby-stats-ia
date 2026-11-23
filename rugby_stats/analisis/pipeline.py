"""El orquestador: corre los pasos en orden y deja todo guardado.

Diseñado para cortarse. Cada paso marca su progreso en la base, así que si se
agota la cuota del día a mitad de un partido, al retomarlo arranca donde
quedó y no vuelve a pagar lo ya hecho (eso lo garantiza además la caché del
cliente).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import Config
from ..db import BaseDatos
from ..gemini import video as video_mod
from ..modelos import Evento, Ficha, Jugador, Partido
from . import pasadas, reglas

log = logging.getLogger(__name__)

# Los cambios van antes de los contactos: saber quién está en cancha descarta
# candidatos a la hora de decidir quién tackleó.
PASOS = (
    "gruesa",
    "relato",
    "cambios",
    "fichas",
    "eventos",
    "contactos",
)

NOMBRES_PASOS = {
    "gruesa": "Pasada gruesa (juego / tiempo muerto / marcador)",
    "relato": "Transcripción del relato",
    "fichas": "Fichas de apariencia de los jugadores",
    "eventos": "Detección de eventos",
    "contactos": "Re-examen de contactos a velocidad alta",
    "cambios": "Detección de cambios",
}


@dataclass
class Resultado:
    partido_id: int
    pasos_corridos: list[str] = field(default_factory=list)
    pasos_salteados: list[str] = field(default_factory=list)
    eventos_nuevos: int = 0
    eventos_pendientes: int = 0
    fichas_nuevas: int = 0
    tramos: int = 0
    simulado: bool = False
    aviso: str = ""
    contadores: Any = None
    gasto_usd: float = 0.0          # lo que costó esta corrida
    gasto_total_usd: float = 0.0    # lo que lleva gastado el partido

    def resumen(self) -> str:
        partes = [
            f"{self.eventos_nuevos} eventos en total ({self.eventos_pendientes} pendientes)",
            f"{self.fichas_nuevas} fichas",
            f"{self.tramos} tramos",
        ]
        if self.gasto_usd:
            partes.append(
                f"costó US$ {self.gasto_usd:.2f} (el partido lleva US$ {self.gasto_total_usd:.2f})"
            )
        if self.simulado:
            partes.append("MODO SIMULADO: estos datos no miden nada")
        return " · ".join(partes)


def correr(
    db: BaseDatos,
    cliente: Any,
    partido_id: int,
    cfg: Config,
    pasos: list[str] | None = None,
    forzar: bool = False,
    progreso: Callable[[str, float], None] | None = None,
) -> Resultado:
    """Corre el análisis y lleva la cuenta de la plata.

    Antes de arrancar le dice al cliente cuánto se gastó ya en este partido
    (para que respete el tope), y al terminar —aunque sea por un error o por
    el tope— registra lo que se gastó en esta corrida.
    """
    previo = db.gasto_partido(partido_id)
    empezar = getattr(cliente, "empezar_partido", None)
    if empezar is not None:
        empezar(previo, cfg.tope_usd_por_partido)
    gasto = 0.0
    try:
        res = _correr(db, cliente, partido_id, cfg, pasos, forzar, progreso)
    finally:
        gasto = float(getattr(cliente, "gasto_usd", 0.0) or 0.0)
        if gasto > 0:
            resumen = getattr(cliente, "resumen_gasto", None)
            db.registrar_gasto(
                partido_id, gasto, cfg.proveedor, resumen() if resumen else {}
            )
            # Para que si el mismo cliente se usa otra vez no se cuente doble.
            if empezar is not None:
                empezar(previo + gasto, cfg.tope_usd_por_partido)
    res.gasto_usd = gasto
    res.gasto_total_usd = previo + gasto
    return res


def _correr(
    db: BaseDatos,
    cliente: Any,
    partido_id: int,
    cfg: Config,
    pasos: list[str] | None = None,
    forzar: bool = False,
    progreso: Callable[[str, float], None] | None = None,
) -> Resultado:
    """Corre el análisis de un partido.

    ``pasos`` limita qué se hace (por ejemplo ``["eventos", "contactos"]``
    para volver a etiquetar sin repetir la pasada gruesa). ``forzar`` rehace
    pasos ya terminados.
    """
    partido = db.partido(partido_id)
    if partido is None:
        raise ValueError(f"No existe el partido {partido_id}.")

    propios = db.plantel(partido_id, partido.equipo_propio.nombre)
    rivales = db.plantel(partido_id, partido.equipo_rival.nombre)
    if not propios:
        raise ValueError(
            "Cargá primero el plantel del equipo propio: sin números de "
            "camiseta no hay a quién atribuirle nada."
        )

    simulado = getattr(cliente, "modelo", "") == "simulado"
    res = Resultado(partido_id=partido_id, simulado=simulado)
    if simulado:
        res.aviso = (
            "Corrió en modo simulado (sin GEMINI_API_KEY). Sirve para ver que "
            "el flujo y el Excel funcionan; los datos son inventados."
        )

    # Dejar el video listo (bajarlo y/o subirlo si hace falta).
    _avisar(progreso, "Preparando el video", 0.0)
    partido.video = video_mod.preparar(
        partido.video, cliente, cfg.dir_video, cfg.descargar_si_dura_mas_de_s,
        desde_s=partido.analizar_desde_s if partido.analizar_hasta_s else None,
        hasta_s=partido.analizar_hasta_s,
        calidad=cfg.calidad_descarga,
    )
    db.guardar_partido(partido)

    hechos = db.progreso(partido_id)
    a_correr = list(pasos) if pasos else list(PASOS)

    def hay_que_correr(paso: str) -> bool:
        if paso not in a_correr:
            res.pasos_salteados.append(paso)
            return False
        if not forzar and hechos.get(paso, {}).get("estado") == "listo":
            log.info("El paso %s ya estaba hecho, lo salto.", paso)
            res.pasos_salteados.append(paso)
            return False
        return True

    # ---- 1. pasada gruesa --------------------------------------------
    if hay_que_correr("gruesa"):
        db.marcar_progreso(partido_id, "gruesa", "corriendo")
        tramos = pasadas.pasada_gruesa(
            cliente, partido, cfg, _sub(progreso, "gruesa", 0.0, 0.15)
        )
        db.borrar_tramos(partido_id)
        db.guardar_tramos(partido_id, tramos)
        db.guardar_partido(partido)  # por el desfase del reloj
        db.marcar_progreso(
            partido_id, "gruesa", "listo", f"{len(tramos)} tramos"
        )
        res.pasos_corridos.append("gruesa")

    tramos = db.tramos(partido_id)
    tramos_juego = [t for t in tramos if t.tipo == "juego"]
    tramos_muertos = [t for t in tramos if t.tipo == "muerto"]
    res.tramos = len(tramos)
    if tramos:
        juego_s = sum(t.duracion_s for t in tramos_juego)
        total_s = sum(t.duracion_s for t in tramos) or 1
        log.info(
            "Juego real: %.0f min de %.0f (%.0f%%)",
            juego_s / 60, total_s / 60, 100 * juego_s / total_s,
        )

    # ---- 2. relato ----------------------------------------------------
    if hay_que_correr("relato"):
        db.marcar_progreso(partido_id, "relato", "corriendo")
        fragmentos = pasadas.pasada_relato(
            cliente, partido, propios, rivales, cfg,
            _sub(progreso, "relato", 0.15, 0.25),
        )
        db.borrar_transcripcion(partido_id)
        db.guardar_transcripcion(partido_id, fragmentos)
        db.marcar_progreso(
            partido_id, "relato", "listo", f"{len(fragmentos)} fragmentos"
        )
        res.pasos_corridos.append("relato")

    transcripcion = db.transcripcion(partido_id)

    # ---- 3. cambios ---------------------------------------------------
    if hay_que_correr("cambios"):
        db.marcar_progreso(partido_id, "cambios", "corriendo")
        cambios = pasadas.pasada_cambios(
            cliente, partido, propios, rivales, cfg, transcripcion,
            _sub(progreso, "cambios", 0.25, 0.3),
        )
        for c in cambios:
            if c["confianza"] < 0.5:
                continue
            if c["entra"] is not None:
                db.actualizar_cambio(
                    partido_id, c["equipo"], c["entra"], c["minuto"], None
                )
            if c["sale"] is not None:
                db.actualizar_cambio(
                    partido_id, c["equipo"], c["sale"], None, c["minuto"]
                )
        db.marcar_progreso(partido_id, "cambios", "listo", f"{len(cambios)} cambios")
        res.pasos_corridos.append("cambios")

    propios = db.plantel(partido_id, partido.equipo_propio.nombre)

    # ---- 4. fichas ----------------------------------------------------
    if hay_que_correr("fichas"):
        db.marcar_progreso(partido_id, "fichas", "corriendo")
        previas = db.fichas_vigentes(
            partido_id, partido.equipo_propio.nombre, float("inf")
        )
        nuevas = pasadas.pasada_fichas(
            cliente, partido, propios, partido.equipo_propio, cfg,
            tramos_muertos, previas, _sub(progreso, "fichas", 0.3, 0.45),
            fotos_previas=db.fichas(partido_id, partido.equipo_propio.nombre),
        )
        for f in nuevas:
            db.guardar_ficha(partido_id, f)
        res.fichas_nuevas = len(nuevas)
        # Si el rival tiene número legible, no hace falta ficharlo.
        if rivales and partido.equipo_rival.numero_legible == "baja":
            previas_r = db.fichas_vigentes(
                partido_id, partido.equipo_rival.nombre, float("inf")
            )
            nuevas_r = pasadas.pasada_fichas(
                cliente, partido, rivales, partido.equipo_rival, cfg,
                tramos_muertos, previas_r, None,
            )
            for f in nuevas_r:
                db.guardar_ficha(partido_id, f)
            res.fichas_nuevas += len(nuevas_r)
        db.marcar_progreso(
            partido_id, "fichas", "listo", f"{res.fichas_nuevas} lecturas"
        )
        res.pasos_corridos.append("fichas")

    fichas = db.fichas_vigentes(partido_id, partido.equipo_propio.nombre, float("inf"))
    fotos = db.fichas(partido_id, partido.equipo_propio.nombre)

    # ---- 4. eventos ---------------------------------------------------
    a_revisar: list[dict[str, Any]] = []
    if hay_que_correr("eventos"):
        db.marcar_progreso(partido_id, "eventos", "corriendo")
        eventos, a_revisar = pasadas.pasada_eventos(
            cliente, partido, propios, rivales, fichas, cfg,
            tramos_juego, transcripcion, _sub(progreso, "eventos", 0.45, 0.6),
            fotos=fotos,
        )
        eventos = _validar(eventos, propios, rivales, cfg)
        db.borrar_eventos(partido_id, partido.analizar_desde_s or 0.0,
                          partido.analizar_hasta_s)
        db.guardar_eventos(partido_id, eventos)
        res.eventos_nuevos += len(eventos)
        db.marcar_progreso(partido_id, "eventos", "listo", f"{len(eventos)} eventos")
        res.pasos_corridos.append("eventos")

    # ---- 5. contactos -------------------------------------------------
    if hay_que_correr("contactos"):
        if not a_revisar:
            # Si no se corrió "eventos" en esta pasada, se reconstruye la
            # lista desde lo guardado: los contactos dudosos.
            a_revisar = [
                {
                    "t_segundos": e.t_segundos,
                    "pista": f"{e.accion} ({e.equipo})",
                }
                for e in db.eventos(partido_id)
                if not e.revisado and (
                    pasadas._es_contacto_dudoso(e, cfg)
                    or (cfg.solo_tackles and e.accion in pasadas.ACCIONES_DE_CONTACTO)
                )
            ]
        db.marcar_progreso(partido_id, "contactos", "corriendo")
        resueltos = pasadas.revisar_contactos(
            cliente, partido, propios, rivales, fichas, cfg, a_revisar,
            _sub(progreso, "contactos", 0.6, 1.0), fotos=fotos,
        )
        for contacto in resueltos:
            contacto["eventos"] = _validar(contacto["eventos"], propios, rivales, cfg)
        _combinar_contactos(db, partido_id, resueltos)
        db.marcar_progreso(
            partido_id, "contactos", "listo", f"{len(resueltos)} contactos re-examinados"
        )
        res.pasos_corridos.append("contactos")

    todos = db.eventos(partido_id)
    res.eventos_nuevos = len(todos)
    res.eventos_pendientes = len([e for e in todos if e.pendiente])
    res.contadores = getattr(cliente, "contadores", None)
    _avisar(progreso, "Listo", 1.0)
    return res


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------


def _validar(
    eventos: list[Evento], propios: list[Jugador], rivales: list[Jugador], cfg: Config
) -> list[Evento]:
    """Aplica "sin clasificar no vale": marca pendiente y anota el motivo."""
    por_equipo = {
        propios[0].equipo if propios else "": reglas.numeros_del_plantel(propios),
        rivales[0].equipo if rivales else "": reglas.numeros_del_plantel(rivales),
    }
    for e in eventos:
        validos = por_equipo.get(e.equipo, set())
        sirve, motivo = reglas.validar_evento(e, validos, cfg)
        if not sirve:
            e.pendiente = True
            e.nota = (e.nota + f" | {motivo}").strip(" |")
        elif reglas.necesita_revision(e, cfg):
            e.pendiente = e.pendiente or False
    return eventos


def _combinar_contactos(
    db: BaseDatos, partido_id: int, contactos: list[dict[str, Any]]
) -> None:
    """Combina lo que dio el re-examen con lo que había de la pasada de juego.

    La regla es no perder información ni duplicarla:

    - Lo que ya revisó una persona no se toca nunca.
    - Si el re-examen identificó a los jugadores, sus eventos reemplazan a los
      dudosos del mismo contacto.
    - Si el re-examen no pudo identificar (devolvió el jugador vacío) pero la
      pasada de juego sí tenía un nombre, se conserva ese nombre —que seguirá
      en la cola por su confianza baja— y solo se le completa el resultado.
      Cambiar una pista floja por un "sin identificar" sería retroceder.
    """
    margen = 1.5
    for contacto in contactos:
        desde = contacto["desde_s"] - margen
        hasta = contacto["hasta_s"] + margen
        viejos = [
            e
            for e in db.eventos(partido_id)
            if desde <= e.t_segundos <= hasta
            and e.accion in pasadas.ACCIONES_DE_CONTACTO
            and not e.revisado
        ]
        if contacto.get("descartado"):
            # El re-examen vio que no hubo tackle propio: lo que había propuesto
            # la pasada rápida era un falso positivo.
            for viejo in viejos:
                if viejo.accion in ("Tackle", "Tackle errado") and viejo.id is not None:
                    db.borrar_evento(viejo.id)
            continue
        for familia in (("Carry",), ("Tackle", "Tackle errado")):
            nuevos_f = [e for e in contacto["eventos"] if e.accion in familia]
            viejos_f = [e for e in viejos if e.accion in familia]
            if not nuevos_f:
                continue  # el re-examen no dijo nada de esto: queda lo que había
            identificados = [e for e in nuevos_f if e.jugador_numero is not None]
            viejos_con_nombre = [e for e in viejos_f if e.jugador_numero is not None]

            if not identificados and viejos_con_nombre:
                # Se conserva la pista vieja y se le completa lo que falte.
                referencia = nuevos_f[0]
                for viejo in viejos_con_nombre:
                    if not viejo.resultado and referencia.resultado:
                        viejo.resultado = referencia.resultado
                    if viejo.metros is None and referencia.metros is not None:
                        viejo.metros = referencia.metros
                    viejo.pendiente = not viejo.esta_clasificado()
                    viejo.nota = (
                        viejo.nota + " | el re-examen no pudo confirmar al jugador"
                    ).strip(" |")
                    db.guardar_evento(partido_id, viejo)
                for viejo in viejos_f:
                    if viejo.jugador_numero is None and viejo.id is not None:
                        db.borrar_evento(viejo.id)
                continue

            for viejo in viejos_f:
                if viejo.id is not None:
                    db.borrar_evento(viejo.id)
            for nuevo in nuevos_f:
                db.guardar_evento(partido_id, nuevo)

        # Lo demás que trae el re-examen (por ejemplo un knock-on) se agrega
        # si no estaba ya.
        for nuevo in contacto["eventos"]:
            if nuevo.accion in pasadas.ACCIONES_DE_CONTACTO:
                continue
            repetido = any(
                e.accion == nuevo.accion and desde <= e.t_segundos <= hasta
                for e in db.eventos(partido_id)
            )
            if not repetido:
                db.guardar_evento(partido_id, nuevo)


class Interrumpir(Exception):
    """Lo levanta quien muestra el avance para cortar el análisis (p. ej.
    porque lo cancelaron desde la app). Es lo único que corta: cualquier otro
    error al mostrar el avance se ignora."""


def _avisar(progreso, texto: str, fraccion: float) -> None:
    if progreso:
        try:
            progreso(texto, fraccion)
        except Interrumpir:
            raise
        except Exception:  # noqa: BLE001 - la barra de progreso no frena el análisis
            pass


def _sub(progreso, paso: str, desde: float, hasta: float):
    """Mapea el progreso de un paso al tramo que le toca de la barra general."""
    if progreso is None:
        return None

    def interno(texto: str, fraccion: float) -> None:
        _avisar(progreso, texto, desde + (hasta - desde) * max(0.0, min(1.0, fraccion)))

    return interno


def estado(db: BaseDatos, partido_id: int) -> list[dict[str, str]]:
    """Qué pasos están hechos, para mostrarlo en la interfaz."""
    hechos = db.progreso(partido_id)
    return [
        {
            "paso": paso,
            "nombre": NOMBRES_PASOS[paso],
            "estado": hechos.get(paso, {}).get("estado", "pendiente"),
            "detalle": hechos.get(paso, {}).get("detalle", ""),
            "actualizado": hechos.get(paso, {}).get("actualizado", ""),
        }
        for paso in PASOS
    ]
