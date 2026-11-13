"""rugby-stats-ia — stats de rugby por jugador, desde el video, con IA.

Sirve para cualquier equipo: los clubes, las camisetas y los planteles se
cargan por partido. El video puede ser de YouTube, un archivo local o
cualquier URL.

El flujo es siempre el mismo:

    plantel -> fichas de apariencia -> eventos -> revisión humana -> Excel

y la precisión final la pone quien revisa, no el modelo.
"""

from .config import Config, cargar_config
from .db import BaseDatos
from .modelos import (
    ACCIONES,
    RESULTADOS,
    Equipo,
    Evento,
    Ficha,
    FuenteVideo,
    Jugador,
    Partido,
    Tramo,
)

__version__ = "0.1.0"

__all__ = [
    "ACCIONES",
    "RESULTADOS",
    "BaseDatos",
    "Config",
    "Equipo",
    "Evento",
    "Ficha",
    "FuenteVideo",
    "Jugador",
    "Partido",
    "Tramo",
    "cargar_config",
    "__version__",
]
