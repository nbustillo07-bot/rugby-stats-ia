"""La cola de revisión humana.

Es el corazón del sistema, no un accesorio: el objetivo es 95% de precisión, y
la precisión final del sistema es la de quien confirma. Lo que hace el modelo
es ordenar el trabajo de la persona, no reemplazarlo.

La cola sale ordenada con lo más dudoso primero: pendientes, después por
confianza creciente. Así, las primeras tarjetas son las que más corrigen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .analisis import reglas
from .config import Config
from .db import BaseDatos
from .gemini import video as video_mod
from .modelos import RESULTADOS, Correccion, Evento, Jugador, Partido

CAMPOS_REVISABLES = ("jugador_numero", "accion", "resultado", "equipo", "metros")


@dataclass
class Tarjeta:
    """Un evento listo para que una persona lo mire."""

    evento: Evento
    jugador: Jugador | None
    inicio_clip_s: float
    fin_clip_s: float
    motivo: str
    # URL para embeber (YouTube) o ruta de un clip cortado (archivo local).
    embed_url: str | None = None
    ruta_clip: str | None = None
    opciones_jugador: list[Jugador] = field(default_factory=list)
    opciones_resultado: list[str] = field(default_factory=list)

    @property
    def titulo(self) -> str:
        quien = (
            self.jugador.etiqueta
            if self.jugador
            else (f"n.º {self.evento.jugador_numero}" if self.evento.jugador_numero
                  else "sin identificar")
        )
        return f"{self.evento.reloj} · {self.evento.accion} · {quien}"


def armar_cola(
    db: BaseDatos, partido_id: int, cfg: Config, limite: int = 50
) -> list[Tarjeta]:
    """Las próximas tarjetas a revisar, las más dudosas primero."""
    partido = db.partido(partido_id)
    if partido is None:
        return []
    eventos = db.cola_revision(partido_id)
    jugadores = db.plantel(partido_id)
    por_clave = {(j.equipo, j.numero): j for j in jugadores}
    largo = cfg.clip_revision_s

    tarjetas: list[Tarjeta] = []
    for e in eventos[:limite]:
        inicio = max(0.0, e.t_segundos - largo / 2)
        fin = inicio + largo
        motivo = e.motivo_pendiente() if e.pendiente else (
            f"confianza {e.confianza:.2f}"
            if e.confianza < cfg.umbral_confianza_revision
            else "control"
        )
        t = Tarjeta(
            evento=e,
            jugador=por_clave.get((e.equipo, e.jugador_numero)),
            inicio_clip_s=inicio,
            fin_clip_s=fin,
            motivo=motivo,
            opciones_jugador=[j for j in jugadores if j.equipo == e.equipo],
            opciones_resultado=list(RESULTADOS.get(e.accion, ())),
        )
        if partido.video.tipo == "youtube":
            t.embed_url = video_mod.embed_youtube(
                partido.video.referencia, inicio, fin
            )
        tarjetas.append(t)
    return tarjetas


def preparar_clip(
    partido: Partido, tarjeta: Tarjeta, cfg: Config
) -> str | None:
    """Corta el clip de un evento, si el video está en disco.

    Con YouTube no hace falta: se embebe con tiempo de inicio y se ahorra el
    corte.
    """
    if not partido.video.ruta_local:
        return None
    destino = (
        Path(cfg.dir_video)
        / "clips"
        / f"p{partido.id}_{int(tarjeta.inicio_clip_s * 10)}.mp4"
    )
    try:
        ruta = video_mod.cortar_clip(
            Path(partido.video.ruta_local),
            tarjeta.inicio_clip_s,
            tarjeta.fin_clip_s - tarjeta.inicio_clip_s,
            destino,
        )
    except video_mod.ErrorVideo:
        return None
    tarjeta.ruta_clip = str(ruta)
    return str(ruta)


# ---------------------------------------------------------------------------
# Acciones de la persona
# ---------------------------------------------------------------------------


def confirmar(db: BaseDatos, partido_id: int, evento: Evento) -> None:
    """La propuesta estaba bien. Sin correcciones: eso es lo que mide precisión."""
    evento.revisado = True
    evento.pendiente = False
    evento.confianza = 1.0
    evento.fuente = evento.fuente if evento.fuente != "manual" else "video"
    db.guardar_evento(partido_id, evento)


def corregir(
    db: BaseDatos,
    partido_id: int,
    evento: Evento,
    cambios: dict[str, Any],
    cfg: Config,
) -> list[Correccion]:
    """Aplica las correcciones de la persona y las guarda una por una.

    Cada corrección queda registrada: es el insumo de la pantalla de precisión.
    """
    registradas: list[Correccion] = []
    for campo, nuevo in cambios.items():
        if campo not in CAMPOS_REVISABLES:
            continue
        viejo = getattr(evento, campo, None)
        if _igual(viejo, nuevo):
            continue
        setattr(evento, campo, nuevo)
        c = Correccion(
            evento_id=evento.id or 0,
            campo=campo,
            valor_modelo="" if viejo is None else str(viejo),
            valor_persona="" if nuevo is None else str(nuevo),
        )
        db.guardar_correccion(partido_id, c)
        registradas.append(c)

    evento.revisado = True
    evento.fuente = "manual"
    evento.confianza = 1.0
    # Después de corregir, se vuelve a validar: si quedó completo, deja de
    # estar pendiente.
    jugadores = db.plantel(partido_id, evento.equipo)
    sirve, motivo = reglas.validar_evento(
        evento, reglas.numeros_del_plantel(jugadores), cfg
    )
    evento.pendiente = not sirve
    if not sirve:
        evento.nota = (evento.nota + f" | {motivo}").strip(" |")
    db.guardar_evento(partido_id, evento)
    return registradas


def descartar(db: BaseDatos, partido_id: int, evento: Evento, motivo: str = "") -> None:
    """El evento no existía. Se registra como corrección y se saca de las cuentas."""
    c = Correccion(
        evento_id=evento.id or 0,
        campo="descartado",
        valor_modelo=f"{evento.accion} de {evento.jugador_numero}",
        valor_persona="no existió",
    )
    db.guardar_correccion(partido_id, c)
    evento.revisado = True
    evento.pendiente = True  # pendiente = no se cuenta en las fórmulas
    evento.nota = (evento.nota + f" | descartado: {motivo or 'no existió'}").strip(" |")
    db.guardar_evento(partido_id, evento)


def agregar_a_mano(
    db: BaseDatos, partido_id: int, evento: Evento
) -> int:
    """Un evento que el sistema no vio y que la persona carga a mano."""
    evento.fuente = "manual"
    evento.revisado = True
    evento.confianza = 1.0
    evento.pendiente = False
    return db.guardar_evento(partido_id, evento)


def _igual(a: Any, b: Any) -> bool:
    if a is None and b is None:
        return True
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a or 0) - float(b or 0)) < 1e-6
        except (TypeError, ValueError):
            return False
    return str(a or "") == str(b or "")


# ---------------------------------------------------------------------------
# Fichas
# ---------------------------------------------------------------------------


def fichas_para_confirmar(db: BaseDatos, partido_id: int, equipo: str) -> list[dict]:
    """Las fichas agrupadas por jugador, para confirmarlas o corregirlas.

    Esto va antes del análisis de eventos: una ficha mal confirmada arrastra
    el error a todos los eventos de ese jugador.
    """
    jugadores = {j.numero: j for j in db.plantel(partido_id, equipo)}
    por_numero: dict[int, list] = {}
    for f in db.fichas(partido_id, equipo):
        por_numero.setdefault(f.jugador_numero, []).append(f)

    salida = []
    for numero, j in sorted(jugadores.items()):
        lecturas = sorted(
            por_numero.get(numero, []), key=lambda f: f.confianza, reverse=True
        )
        salida.append(
            {
                "jugador": j,
                "lecturas": lecturas,
                "mejor": lecturas[0] if lecturas else None,
                "confirmada": any(f.confirmada for f in lecturas),
                "sin_ficha": not lecturas,
            }
        )
    return salida


def candidatos_de(evento: Evento, maximo: int = 3) -> list[dict[str, Any]]:
    """Los jugadores más probables que propuso el modelo, para elegir con un clic.

    Salen de lo que guardó el re-examen del contacto. Si no hay candidatos
    pero sí un jugador propuesto, ese es el único.
    """
    from .analisis import reglas

    prop = evento.propuesta_original or {}
    lista = reglas.unir_candidatos(prop.get("candidatos"), maximo=maximo)
    if not lista and evento.jugador_numero is not None:
        lista = [{"numero": evento.jugador_numero, "probabilidad": evento.confianza}]
    return lista[:maximo]


def foto_de_cada_jugador(base, partido_id: int, equipo: str) -> dict[int, str]:
    """La mejor foto recortada de cada jugador (la de más confianza)."""
    from pathlib import Path

    fotos: dict[int, tuple[float, str]] = {}
    for f in base.fichas(partido_id, equipo):
        if not f.imagen or not Path(f.imagen).exists():
            continue
        puntaje = f.confianza + (1.0 if f.confirmada else 0.0)
        if f.jugador_numero not in fotos or puntaje > fotos[f.jugador_numero][0]:
            fotos[f.jugador_numero] = (puntaje, f.imagen)
    return {n: r for n, (_, r) in fotos.items()}
