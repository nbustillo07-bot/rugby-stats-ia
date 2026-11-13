"""Piezas compartidas por los tests.

Todo corre sin clave de API: el cliente simulado devuelve respuestas con la
forma correcta, así que se puede probar la base, las reglas, el pipeline y el
Excel de punta a punta.
"""

from __future__ import annotations

import pytest

from rugby_stats.config import Config
from rugby_stats.db import BaseDatos
from rugby_stats.modelos import Equipo, FuenteVideo, Jugador, Partido

CONFIG_BASE = {
    "modelo": {"nombre": "gemini-3.8-flash", "alternativas": [], "temperatura": 0.1},
    "velocidades": {
        "gruesa": {"fps": 0.5, "ventana_s": 300},
        "juego": {"fps": 2.0, "ventana_s": 120},
        "contacto": {
            "fps": 10.0, "ventana_s": 3.0,
            "margen_antes_s": 1.5, "margen_despues_s": 1.5,
            "resolucion": "MEDIA_RESOLUTION_HIGH",
        },
        "fichas": {"fps": 3.0, "ventana_s": 60},
    },
    "ventanas": {"solape_s": 5},
    "reglas": {
        "tackle_positivo": "portador_detenido",
        "tolerancia_avance_cm": 50,
        "umbral_confianza_revision": 0.85,
        "umbral_precision_objetivo": 0.95,
        "reintentos_sin_clasificar": 1,
    },
    "puntos": {"try": 5, "conversion": 2, "penal": 3, "drop": 3, "try_penal": 7},
    "api": {"rpm_inicial": 1000, "cache": False, "max_reintentos": 1},
    "video": {"descargar_si_dura_mas_de_s": 999999, "clip_revision_s": 6},
    "salida": {"jugadores_de": "propio"},
}


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(
        {
            **CONFIG_BASE,
            "api": {**CONFIG_BASE["api"], "cache_dir": str(tmp_path / "cache")},
            "video": {**CONFIG_BASE["video"], "dir_trabajo": str(tmp_path / "video")},
            "salida": {**CONFIG_BASE["salida"], "dir": str(tmp_path / "salidas")},
        },
        raiz=tmp_path,
    )


@pytest.fixture
def db(tmp_path) -> BaseDatos:
    base = BaseDatos(tmp_path / "test.db")
    yield base
    base.cerrar()


def hacer_plantel(equipo: str, cuantos: int = 23) -> list[Jugador]:
    return [
        Jugador(
            numero=n,
            nombre=f"Jugador {n} de {equipo}",
            equipo=equipo,
            apodo=f"Apodo{n}" if n % 3 == 0 else "",
            titular=n <= 15,
        )
        for n in range(1, cuantos + 1)
    ]


@pytest.fixture
def partido() -> Partido:
    return Partido(
        nombre="Club A vs Club B",
        competencia="Liga de prueba",
        equipo_propio=Equipo(
            nombre="Club A",
            kit="rayas verdes y blancas, número blanco",
            numero_legible="baja",
            es_propio=True,
        ),
        equipo_rival=Equipo(
            nombre="Club B", kit="azul liso, número blanco", numero_legible="alta"
        ),
        video=FuenteVideo(
            tipo="youtube",
            referencia="https://www.youtube.com/watch?v=aaaaaaaaaaa",
            duracion_s=600.0,
            uri_subida="https://www.youtube.com/watch?v=aaaaaaaaaaa",
        ),
        analizar_desde_s=0.0,
        analizar_hasta_s=300.0,
    )


@pytest.fixture
def partido_cargado(db: BaseDatos, partido: Partido) -> Partido:
    """Un partido guardado, con los dos planteles."""
    pid = db.guardar_partido(partido)
    db.guardar_plantel(pid, hacer_plantel("Club A"))
    db.guardar_plantel(pid, hacer_plantel("Club B"))
    return db.partido(pid)
