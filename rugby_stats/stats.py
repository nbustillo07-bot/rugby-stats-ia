"""Agregación de eventos a stats por jugador.

Las mismas cuentas que hacen las fórmulas del Excel, pero en Python, para
mostrarlas en la interfaz sin tener que exportar.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .analisis import reglas
from .config import Config
from .modelos import Evento, Jugador

#: Columnas de la hoja de jugadores, en orden. Cada una dice cómo se cuenta:
#: (título, acción, resultado o None, tipo)
#: tipo: "contar" cuenta eventos, "sumar" suma la columna de metros.
COLUMNAS: tuple[tuple[str, str | None, str | None, str], ...] = (
    ("Tackles", "Tackle", None, "contar"),
    ("Tackles positivos", "Tackle", "positivo", "contar"),
    ("Tackles negativos", "Tackle", "negativo", "contar"),
    ("Tackles neutros", "Tackle", "neutro", "contar"),
    ("Tackles errados", "Tackle errado", None, "contar"),
    ("Carries", "Carry", None, "contar"),
    ("Metros ganados (est.)", "Carry", None, "sumar"),
    ("Quiebres", "Carry", "quiebre", "contar"),
    ("Pases", "Pase", None, "contar"),
    ("Offloads", "Offload", None, "contar"),
    ("Patadas", "Patada", None, "contar"),
    ("Rucks limpiados", "Ruck limpiado", None, "contar"),
    ("Pelotas perdidas", "Pelota perdida", None, "contar"),
    ("Pelotas recuperadas", "Pelota recuperada", None, "contar"),
    ("Penales cometidos", "Penal cometido", None, "contar"),
    ("Tries", "Try", None, "contar"),
    ("Conversiones", "Conversión", "adentro", "contar"),
    ("Penales a los palos", "Penal a los palos", "adentro", "contar"),
    ("Drops", "Drop", "adentro", "contar"),
    ("Lines tirados", "Line tiro", None, "contar"),
    ("Lines tirados ganados", "Line tiro", "ganado", "contar"),
    ("Lines tomados", "Line toma", None, "contar"),
)


@dataclass
class FilaJugador:
    numero: int
    nombre: str
    equipo: str
    valores: dict[str, float] = field(default_factory=dict)
    puntos: int = 0
    minutos: float = 0.0
    eventos_sin_revisar: int = 0

    def como_dict(self) -> dict[str, object]:
        d: dict[str, object] = {"N.º": self.numero, "Jugador": self.nombre}
        d.update(self.valores)
        d["Puntos"] = self.puntos
        d["Minutos jugados"] = self.minutos
        d["Eventos sin revisar"] = self.eventos_sin_revisar
        return d


def por_jugador(
    jugadores: list[Jugador],
    eventos: list[Evento],
    cfg: Config,
    duracion_partido_min: float = 80.0,
    solo_revisados: bool = False,
) -> list[FilaJugador]:
    """Una fila por jugador.

    Con ``solo_revisados=True`` se cuentan únicamente los eventos que una
    persona confirmó. Es la vista honesta: la precisión final del sistema es
    la de quien revisa.
    """
    usables = [
        e
        for e in eventos
        if not e.pendiente and (e.revisado if solo_revisados else True)
    ]
    filas: list[FilaJugador] = []

    for j in jugadores:
        suyos = [
            e for e in usables if e.equipo == j.equipo and e.jugador_numero == j.numero
        ]
        valores: dict[str, float] = {}
        for titulo, accion, resultado, tipo in COLUMNAS:
            coincidentes = [
                e
                for e in suyos
                if e.accion == accion and (resultado is None or e.resultado == resultado)
            ]
            if tipo == "sumar":
                valores[titulo] = round(
                    sum(e.metros for e in coincidentes if e.metros is not None), 1
                )
            else:
                valores[titulo] = len(coincidentes)

        filas.append(
            FilaJugador(
                numero=j.numero,
                nombre=j.nombre,
                equipo=j.equipo,
                valores=valores,
                puntos=sum(reglas.puntos_de(e, cfg) for e in suyos),
                minutos=reglas.minutos_jugados(j, duracion_partido_min),
                eventos_sin_revisar=len(
                    [
                        e
                        for e in eventos
                        if e.equipo == j.equipo
                        and e.jugador_numero == j.numero
                        and not e.revisado
                    ]
                ),
            )
        )
    return filas


def totales(filas: list[FilaJugador]) -> dict[str, float]:
    tot: dict[str, float] = {}
    for titulo, _, _, _ in COLUMNAS:
        tot[titulo] = round(sum(f.valores.get(titulo, 0) for f in filas), 1)
    tot["Puntos"] = sum(f.puntos for f in filas)
    return tot


def resumen_partido(eventos: list[Evento], cfg: Config) -> dict[str, object]:
    """Números gruesos del partido, para el encabezado de la interfaz."""
    total = len(eventos)
    pendientes = len([e for e in eventos if e.pendiente])
    revisados = len([e for e in eventos if e.revisado])
    sin_jugador = len([e for e in eventos if e.jugador_numero is None])
    baja_confianza = len(
        [e for e in eventos if e.confianza < cfg.umbral_confianza_revision]
    )
    return {
        "eventos": total,
        "pendientes": pendientes,
        "revisados": revisados,
        "sin_jugador": sin_jugador,
        "baja_confianza": baja_confianza,
        "porcentaje_revisado": round(100 * revisados / total, 1) if total else 0.0,
    }
