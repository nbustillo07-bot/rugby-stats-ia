"""Las reglas del sistema, separadas del modelo.

Esto es a propósito: el modelo mira el video y dice qué pasó; las reglas
deciden qué significa. Si mañana se cambia el criterio de tackle positivo, se
recalcula sobre lo ya medido sin volver a gastar un solo pedido de API.
"""

from __future__ import annotations

from typing import Any

from ..config import Config
from ..modelos import ACCIONES, RESULTADOS, Evento, Jugador

# ---------------------------------------------------------------------------
# Tackle positivo / negativo
# ---------------------------------------------------------------------------


def clasificar_tackle(
    avance_portador: str, cfg: Config, metros_avance: float | None = None
) -> str:
    """Convierte la observación del avance en positivo / negativo / neutro.

    El criterio por defecto es la convención del rugby y lo que pidió Nico:
    **positivo si el portador no avanza** (se queda donde lo agarraron o lo
    hacen retroceder), **negativo si avanza**. Se invierte en config.yaml con
    ``reglas.tackle_positivo: portador_avanza``.

    ``avance_portador`` viene del modelo y vale "avanza", "se_detiene",
    "retrocede" o "no_se_ve". Con "no_se_ve" devolvemos cadena vacía: el
    evento queda sin clasificar y va a la cola, porque sin clasificar no vale.
    """
    if avance_portador not in ("avanza", "se_detiene", "retrocede"):
        return ""

    tolerancia_m = float(cfg.get("reglas.tolerancia_avance_cm", 50)) / 100.0
    avanzo = avance_portador == "avanza"

    # Si el modelo midió los metros, manda la medición sobre la etiqueta: un
    # "avanza" de 20 cm está dentro de la tolerancia y no cuenta como avance.
    if metros_avance is not None:
        if metros_avance > tolerancia_m:
            avanzo = True
        elif metros_avance < -tolerancia_m:
            avanzo = False
        else:
            return "neutro"

    if cfg.tackle_positivo_es_portador_detenido:
        return "negativo" if avanzo else "positivo"
    return "positivo" if avanzo else "negativo"


def descripcion_criterio(cfg: Config) -> str:
    """El criterio en una línea, para el Excel y la interfaz."""
    tol = cfg.get("reglas.tolerancia_avance_cm", 50)
    if cfg.tackle_positivo_es_portador_detenido:
        return (
            "Tackle positivo: el portador NO avanza después del contacto "
            f"(se queda en el lugar o retrocede). Tolerancia: {tol} cm."
        )
    return (
        "Tackle positivo: el portador SÍ avanza después del contacto. "
        f"Tolerancia: {tol} cm."
    )


# ---------------------------------------------------------------------------
# Puntos
# ---------------------------------------------------------------------------


def puntos_de(evento: Evento, cfg: Config) -> int:
    p = cfg.puntos
    if evento.accion == "Try":
        return p["try"]
    if evento.accion == "Conversión" and evento.resultado == "adentro":
        return p["conversion"]
    if evento.accion == "Penal a los palos" and evento.resultado == "adentro":
        return p["penal"]
    if evento.accion == "Drop" and evento.resultado == "adentro":
        return p["drop"]
    return 0


# ---------------------------------------------------------------------------
# Validación: "sin clasificar no vale"
# ---------------------------------------------------------------------------


def validar_evento(
    evento: Evento, numeros_validos: set[int], cfg: Config
) -> tuple[bool, str]:
    """¿Este evento se puede exportar, o va a la cola como pendiente?

    Devuelve (sirve, motivo). Si no sirve, el motivo se guarda en la nota para
    que la persona sepa qué mirar.
    """
    if evento.accion not in ACCIONES:
        return False, f"acción desconocida: {evento.accion!r}"

    if evento.jugador_numero is None:
        return False, "sin jugador identificado"

    if numeros_validos and evento.jugador_numero not in numeros_validos:
        return (
            False,
            f"el número {evento.jugador_numero} no está en el plantel de "
            f"{evento.equipo or 'ese equipo'}",
        )

    validos = RESULTADOS.get(evento.accion, ())
    if not evento.resultado:
        return False, "sin resultado"
    if evento.resultado not in validos:
        return False, f"resultado {evento.resultado!r} no válido para {evento.accion}"

    if evento.confianza < cfg.umbral_confianza_revision:
        # No es un error: es un evento que hay que confirmar antes de usarlo.
        return True, ""

    return True, ""


def necesita_revision(evento: Evento, cfg: Config) -> bool:
    """Todo lo pendiente, y todo lo que esté por debajo del umbral."""
    if evento.revisado:
        return False
    if evento.pendiente:
        return True
    return evento.confianza < cfg.umbral_confianza_revision


def numeros_del_plantel(jugadores: list[Jugador], equipo: str | None = None) -> set[int]:
    return {j.numero for j in jugadores if equipo is None or j.equipo == equipo}


# ---------------------------------------------------------------------------
# Minutos jugados
# ---------------------------------------------------------------------------


def minutos_jugados(
    jugador: Jugador, duracion_partido_min: float = 80.0
) -> float:
    """Minutos en cancha según entró/salió. Es un cálculo, no una medición."""
    desde = jugador.entro_min if jugador.entro_min is not None else (
        0.0 if jugador.titular else None
    )
    if desde is None:
        return 0.0
    hasta = jugador.salio_min if jugador.salio_min is not None else duracion_partido_min
    return max(0.0, round(hasta - desde, 1))


# ---------------------------------------------------------------------------
# Reloj
# ---------------------------------------------------------------------------


def reloj_desde_video(t_video_s: float, desfase_s: float = 0.0) -> str:
    """Pasa segundos de video a reloj de partido (mm:ss)."""
    t = max(0.0, t_video_s + desfase_s)
    m, s = divmod(int(round(t)), 60)
    return f"{m:02d}:{s:02d}"


def segundos_desde_reloj(reloj: str) -> float | None:
    """'22:48' -> 1368.0. Devuelve None si no se entiende."""
    if not reloj:
        return None
    partes = reloj.strip().split(":")
    try:
        if len(partes) == 2:
            return int(partes[0]) * 60 + float(partes[1])
        if len(partes) == 3:
            return int(partes[0]) * 3600 + int(partes[1]) * 60 + float(partes[2])
    except ValueError:
        return None
    return None


def calcular_desfase(reloj_visto: str, t_video_s: float) -> float | None:
    """Desfase entre el reloj del cartel y el tiempo del video.

    Con un solo punto de referencia alcanza: ``reloj = t_video + desfase``.
    """
    seg = segundos_desde_reloj(reloj_visto)
    return None if seg is None else seg - t_video_s


# ---------------------------------------------------------------------------
# Normalización de lo que devuelve el modelo
# ---------------------------------------------------------------------------


def nombre_equipo(etiqueta: str, propio: str, rival: str) -> str:
    """El modelo contesta "propio"/"rival"; acá se traduce al club real."""
    e = (etiqueta or "").strip().lower()
    if e in ("propio", "local_propio", "nosotros"):
        return propio
    if e in ("rival", "visitante_rival", "ellos"):
        return rival
    return etiqueta or ""


def etiqueta_equipo(nombre: str, propio: str) -> str:
    """Al revés: del nombre del club a "propio"/"rival"."""
    return "propio" if nombre == propio else "rival"


def limpiar_numero(valor: Any) -> int | None:
    """Un número de camiseta, o None. Nunca revienta."""
    if valor is None:
        return None
    try:
        n = int(valor)
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= 99 else None
