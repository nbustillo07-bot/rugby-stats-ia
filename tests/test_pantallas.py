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

    monkeypatch.chdir(RAIZ)
    at = AppTest.from_file(str(RAIZ / pantalla), default_timeout=90)
    at.run()
    assert not at.exception, (
        f"{pantalla} explotó: "
        f"{at.exception[0].message if at.exception else ''}"
    )


@pytest.mark.parametrize("pantalla", PANTALLAS)
def test_la_pantalla_renderiza_con_un_partido(pantalla, monkeypatch):
    """Con un partido elegido, que es el caso normal de uso."""
    from streamlit.testing.v1 import AppTest

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
