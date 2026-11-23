"""Que las pantallas de Streamlit rendericen sin explotar.

Usa el framework de testing de Streamlit, que corre cada pantalla sin
navegador. No prueba que se vean bien: prueba que no se caigan, que es lo
que más duele cuando pasa a mitad de una revisión.
"""

from __future__ import annotations

import glob
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
PANTALLAS = ["app.py"] + sorted(
    str(Path(p).relative_to(RAIZ)) for p in glob.glob(str(RAIZ / "pages" / "*.py"))
)

pytest.importorskip("streamlit.testing.v1", reason="hace falta streamlit >= 1.28")


@pytest.mark.parametrize("pantalla", PANTALLAS)
def test_la_pantalla_renderiza_sin_partido(pantalla, tmp_path, monkeypatch):
    """Arranque en frío: sin partidos cargados, nada tiene que explotar."""
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("RUGBY_STATS_DB", str(tmp_path / "vacia.db"))
    monkeypatch.chdir(RAIZ)
    at = AppTest.from_file(str(RAIZ / pantalla), default_timeout=90)
    at.run()
    assert not at.exception, (
        f"{pantalla} explotó: "
        f"{at.exception[0].message if at.exception else ''}"
    )


@pytest.mark.parametrize("pantalla", PANTALLAS)
def test_la_pantalla_renderiza_con_un_partido(pantalla, tmp_path, monkeypatch):
    """Con un partido elegido y analizado, que es el caso normal de uso."""
    from streamlit.testing.v1 import AppTest

    from rugby_stats.analisis import pipeline
    from rugby_stats.config import cargar_config
    from rugby_stats.db import BaseDatos
    from rugby_stats.gemini.cliente import ClienteSimulado
    from rugby_stats.modelos import Equipo, FuenteVideo, Partido
    from tests.conftest import hacer_plantel

    ruta_db = tmp_path / "pantallas.db"
    base = BaseDatos(ruta_db)
    pid = base.guardar_partido(Partido(
        nombre="A vs B",
        equipo_propio=Equipo(nombre="Club A", kit="rayas", numero_legible="baja"),
        equipo_rival=Equipo(nombre="Club B", kit="liso"),
        video=FuenteVideo(
            tipo="youtube", referencia="https://youtu.be/aaaaaaaaaaa",
            duracion_s=300.0,
        ),
        analizar_hasta_s=120.0,
    ))
    base.guardar_plantel(pid, hacer_plantel("Club A"))
    pipeline.correr(base, ClienteSimulado(), pid, cargar_config(RAIZ / "config.yaml"))
    base.cerrar()
    monkeypatch.setenv("RUGBY_STATS_DB", str(ruta_db))
    monkeypatch.chdir(RAIZ)
    at = AppTest.from_file(str(RAIZ / pantalla), default_timeout=90)
    at.session_state["partido_id"] = 1
    at.run()
    assert not at.exception, (
        f"{pantalla} explotó: "
        f"{at.exception[0].message if at.exception else ''}"
    )


def test_ninguna_pantalla_usa_parametros_deprecados():
    """use_container_width se elimina de Streamlit: va `width` en su lugar."""
    for pantalla in PANTALLAS:
        texto = (RAIZ / pantalla).read_text(encoding="utf-8")
        assert "use_container_width" not in texto, (
            f"{pantalla} usa use_container_width, que está deprecado. "
            'Cambialo por width="stretch" o width="content".'
        )


@pytest.mark.parametrize("pantalla", ["app.py", "pages/3_Fichas.py", "pages/4_Analisis.py"])
def test_con_clave_de_claude_muestra_la_plata(pantalla, tmp_path, monkeypatch):
    """Con clave de Claude: la barra muestra lo gastado y Análisis el costo."""
    from streamlit.testing.v1 import AppTest

    from rugby_stats.db import BaseDatos
    from rugby_stats.modelos import Equipo, FuenteVideo, Partido
    from tests.conftest import hacer_plantel

    ruta_db = tmp_path / "plata.db"
    base = BaseDatos(ruta_db)
    pid = base.guardar_partido(Partido(
        nombre="A vs B",
        equipo_propio=Equipo(nombre="Club A", kit="rayas", numero_legible="baja"),
        equipo_rival=Equipo(nombre="Club B", kit="liso"),
        video=FuenteVideo(tipo="youtube", referencia="https://youtu.be/aaaaaaaaaaa",
                          duracion_s=4800.0),
        analizar_desde_s=600.0, analizar_hasta_s=960.0,
    ))
    base.guardar_plantel(pid, hacer_plantel("Club A"))
    base.registrar_gasto(pid, 1.25, "claude")
    base.cerrar()
    monkeypatch.setenv("RUGBY_STATS_DB", str(ruta_db))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-de-mentira")
    monkeypatch.chdir(RAIZ)
    at = AppTest.from_file(str(RAIZ / pantalla), default_timeout=90)
    at.session_state["partido_id"] = 1
    at.session_state["forzar_simulado"] = False
    at.run()
    assert not at.exception, at.exception[0].message if at.exception else ""
    if pantalla.endswith("4_Analisis.py"):
        assert any("US$" in str(m.value) for m in at.metric)
        assert any(m.value == "US$ 1.25" for m in at.metric)


def test_revision_muestra_los_candidatos_con_un_clic(tmp_path, monkeypatch):
    """Un tackle dudoso trae botones con los candidatos; tocar uno lo resuelve."""
    from streamlit.testing.v1 import AppTest

    from rugby_stats.db import BaseDatos
    from rugby_stats.modelos import Equipo, Evento, FuenteVideo, Partido
    from tests.conftest import hacer_plantel

    ruta_db = tmp_path / "rev.db"
    base = BaseDatos(ruta_db)
    pid = base.guardar_partido(Partido(
        nombre="A vs B",
        equipo_propio=Equipo(nombre="Club A", kit="rayas", numero_legible="baja"),
        equipo_rival=Equipo(nombre="Club B", kit="liso"),
        video=FuenteVideo(tipo="youtube", referencia="https://youtu.be/aaaaaaaaaaa"),
    ))
    base.guardar_plantel(pid, hacer_plantel("Club A"))
    base.guardar_evento(pid, Evento(
        t_segundos=30, accion="Tackle", equipo="Club A", jugador_numero=None,
        resultado="positivo", confianza=0.3, pendiente=True,
        propuesta_original={"candidatos": [{"numero": 4, "probabilidad": 0.6},
                                           {"numero": 7, "probabilidad": 0.3}]},
    ))
    base.cerrar()
    monkeypatch.setenv("RUGBY_STATS_DB", str(ruta_db))
    monkeypatch.chdir(RAIZ)
    at = AppTest.from_file(str(RAIZ / "pages/5_Revision.py"), default_timeout=90)
    at.session_state["partido_id"] = 1
    at.run()
    assert not at.exception
    botones = [b for b in at.button if b.key and b.key.startswith("cand_")]
    assert len(botones) == 2
    botones[0].click().run()
    assert not at.exception
    e = BaseDatos(ruta_db).eventos(1)[0]
    assert e.jugador_numero == 4 and e.revisado and not e.pendiente
