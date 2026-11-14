"""La aplicación en un navegador de verdad.

Las pruebas con el framework de Streamlit corren todo en un mismo hilo, y por
eso no vieron el primer error que apareció al usar la aplicación en serio: la
base de datos quedaba atada al hilo que la creó, y Streamlit atiende cada clic
en un hilo distinto. Esta prueba levanta el servidor y lo recorre con Chrome.

Se saltea sola si no hay Playwright o no hay un Chrome instalado.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api", reason="hace falta playwright")

RAIZ = Path(__file__).resolve().parent.parent
CHROME = next(
    (
        c
        for c in (
            shutil.which("google-chrome"),
            shutil.which("chromium"),
            shutil.which("chromium-browser"),
        )
        if c
    ),
    None,
)
pytestmark = pytest.mark.skipif(CHROME is None, reason="no hay Chrome instalado")


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def servidor(tmp_path):
    puerto = _puerto_libre()
    entorno = dict(os.environ, RUGBY_STATS_DB=str(tmp_path / "navegador.db"))
    entorno.pop("GEMINI_API_KEY", None)
    proceso = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "app.py",
         "--server.headless=true", f"--server.port={puerto}",
         "--browser.gatherUsageStats=false"],
        cwd=RAIZ, env=entorno, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{puerto}"
    for _ in range(60):
        try:
            socket.create_connection(("127.0.0.1", puerto), timeout=0.5).close()
            break
        except OSError:
            time.sleep(0.5)
    yield url, tmp_path / "navegador.db"
    proceso.terminate()


def _asentar(page) -> None:
    time.sleep(1.2)
    for _ in range(80):
        if page.locator('[data-testid="stStatusWidget"]').count() == 0:
            break
        time.sleep(0.25)
    time.sleep(0.6)


def _sin_error(page, paso: str) -> None:
    _asentar(page)
    error = page.locator('[data-testid="stException"]')
    assert error.count() == 0, f"{paso}: {error.first.inner_text()[:500]}"


def test_recorrido_completo_en_el_navegador(servidor):
    from rugby_stats.db import BaseDatos
    from rugby_stats.modelos import Jugador

    url, ruta_db = servidor
    with sync_api.sync_playwright() as pw:
        navegador = pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        page = navegador.new_page(viewport={"width": 1400, "height": 1000})

        page.goto(url)
        _sin_error(page, "inicio")
        # Cada navegación la atiende otro hilo: acá reventaba la base.
        for pantalla in ("Partido", "Plantel", "Partido"):
            page.goto(f"{url}/{pantalla}")
            _sin_error(page, f"abrir {pantalla}")

        def llenar(etiqueta: str, valor: str) -> None:
            page.get_by_label(etiqueta, exact=True).fill(valor)

        llenar("Nombre del club", "Club A")
        llenar("Cómo es la camiseta", "rayas verdes")
        llenar("Nombre del club rival", "Club B")
        llenar("Link de YouTube, otra URL, o la ruta de un archivo",
               "https://www.youtube.com/watch?v=VsRZotfwL6A")
        llenar("Desde (mm:ss del video)", "10:00")
        llenar("Hasta (mm:ss del video, vacío = hasta el final)", "13:00")
        page.get_by_role("button", name="Crear partido").click()
        _sin_error(page, "crear partido")

        db = BaseDatos(ruta_db)
        partidos = db.partidos()
        assert len(partidos) == 1
        pid = partidos[0].id
        db.guardar_plantel(pid, [
            Jugador(numero=n, nombre=f"Jugador {n}", equipo="Club A", titular=n <= 15)
            for n in range(1, 24)
        ])

        page.goto(f"{url}/Plantel")
        _sin_error(page, "plantel")
        page.get_by_role("button", name="Guardar plantel").click()
        _sin_error(page, "guardar plantel")
        assert len(db.plantel(pid)) == 23

        page.goto(f"{url}/Fichas")
        _sin_error(page, "fichas")
        page.get_by_role("button", name="Buscar fichas").click()
        _sin_error(page, "buscar fichas")
        assert db.fichas(pid)

        page.goto(f"{url}/Analisis")
        _sin_error(page, "análisis")
        page.get_by_role("button", name="Correr análisis").click()
        time.sleep(2)
        _sin_error(page, "correr análisis")
        assert db.eventos(pid)

        page.goto(f"{url}/Revision")
        _sin_error(page, "revisión")
        page.get_by_role("button", name="No existió", exact=True).first.click()
        _sin_error(page, "descartar un evento")
        assert any(e.revisado for e in db.eventos(pid))

        page.goto(f"{url}/Precision")
        _sin_error(page, "precisión")
        page.goto(f"{url}/Exportar")
        _sin_error(page, "exportar")
        page.get_by_role("button", name="Generar el Excel").click()
        _sin_error(page, "generar el Excel")
        page.goto(url)
        _sin_error(page, "inicio con el partido analizado")

        navegador.close()
        db.cerrar()
