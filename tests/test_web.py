"""La app web de punta a punta, en un navegador de verdad.

Levanta la app (Next.js, ya compilada con ``npm run build`` en ``web/``)
contra un Postgres de prueba, carga un partido desde el formulario, hace que
el trabajador lo analice (con un Claude de mentira y un video sintético
servido por HTTP), y verifica que el partido se pone en verde, que la tabla
de tackles aparece, que se revisa con un clic y que el Excel baja bien.

Necesita ``RUGBY_PG_PRUEBA`` (dirección de un Postgres) y la app compilada.
"""

from __future__ import annotations

import functools
import http.server
import io
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
sync_api = pytest.importorskip("playwright.sync_api")

RAIZ = Path(__file__).resolve().parent.parent
WEB = RAIZ / "web"
URL_PG = os.environ.get("RUGBY_PG_PRUEBA", "")
CHROME = next((c for c in (shutil.which("google-chrome"), shutil.which("chromium"),
                           shutil.which("chromium-browser")) if c), None)
pytestmark = pytest.mark.skipif(
    not URL_PG or CHROME is None or not (WEB / ".next" / "BUILD_ID").exists(),
    reason="falta Postgres de prueba, Chrome o la app compilada (npm run build en web/)",
)

from rugby_stats.nube import Nube  # noqa: E402
from scripts import trabajador  # noqa: E402
from tests.test_claude import (  # noqa: E402
    cfg_tackles,
    cliente_tackles,
    servidor_tackles,
    video,  # noqa: F401 - fixture
)

CLAVE = "clave-de-prueba"
CAPTURAS = os.environ.get("RUGBY_CAPTURAS")


def _foto(pag, nombre: str) -> None:
    if CAPTURAS:
        Path(CAPTURAS).mkdir(parents=True, exist_ok=True)
        pag.screenshot(path=str(Path(CAPTURAS) / f"{nombre}.png"), full_page=True)


def _puerto() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def base_pg():
    nombre = "web_" + uuid.uuid4().hex[:8]
    with psycopg.connect(URL_PG, autocommit=True) as con:
        con.execute(f"CREATE DATABASE {nombre}")
    url = URL_PG.rsplit("/", 1)[0] + "/" + nombre
    yield url
    with psycopg.connect(URL_PG, autocommit=True) as con:
        con.execute(f"DROP DATABASE {nombre} WITH (FORCE)")


class _ClaudeFalso(http.server.BaseHTTPRequestHandler):
    """Contesta como la API de Claude leyendo una foto de formación."""
    vistos: list = []

    avisos: list = []

    def do_POST(self):  # noqa: N802
        import json as _json

        crudo = self.rfile.read(int(self.headers.get("content-length") or 0))
        if not self.path.startswith("/v1/messages"):
            # Hace de ntfy: registra el aviso a la compu.
            _ClaudeFalso.avisos.append((self.path, crudo.decode()))
            self.send_response(200)
            self.end_headers()
            return
        cuerpo = _json.loads(crudo)
        _ClaudeFalso.vistos.append(cuerpo)
        jugadores = [{"numero": n, "nombre": f"Jugador {n}", "apodo": "Curu" if n == 4 else None}
                     for n in range(1, 24)]
        texto = _json.dumps({"equipo": "Club A", "jugadores": jugadores, "aviso": None})
        datos = _json.dumps({"id": "m", "type": "message", "role": "assistant", "model": "x",
                             "content": [{"type": "text", "text": texto}], "stop_reason": "end_turn",
                             "usage": {"input_tokens": 1, "output_tokens": 1}}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def log_message(self, *a):
        pass


@pytest.fixture
def claude_falso():
    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ClaudeFalso)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{servidor.server_address[1]}"
    servidor.shutdown()


@pytest.fixture
def app(base_pg, claude_falso):
    puerto = _puerto()
    entorno = {**os.environ, "DATABASE_URL": base_pg, "APP_PASSWORD": CLAVE,
               "ANTHROPIC_API_KEY": "sk-ant-prueba", "ANTHROPIC_BASE_URL": claude_falso,
               "NTFY_BASE": claude_falso,
               "PORT": str(puerto), "NODE_ENV": "production"}
    proc = subprocess.Popen(["npx", "next", "start", "-p", str(puerto)], cwd=WEB, env=entorno,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{puerto}"
    for _ in range(60):
        try:
            urllib.request.urlopen(url + "/login", timeout=2)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    yield url
    proc.terminate()
    proc.wait(10)


@pytest.fixture
def video_http(video):
    """El video sintético servido por HTTP, como si fuera un link."""
    manejador = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(video.parent))
    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), manejador)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{servidor.server_address[1]}/{video.name}"
    servidor.shutdown()


PLANTEL = "\n".join(f"{n} Jugador {n}" + (" (Curu)" if n == 4 else "") for n in range(1, 19))


def test_de_punta_a_punta(app, base_pg, video_http, video, db, tmp_path):
    with sync_api.sync_playwright() as pw:
        nav = pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        pag = nav.new_page(viewport={"width": 420, "height": 900})  # como un teléfono

        # Sin sesión, al login. Contraseña mal, error; bien, adentro.
        pag.goto(app + "/")
        assert "/login" in pag.url
        pag.fill("#clave", "mal")
        pag.click("text=Entrar")
        pag.wait_for_selector("text=Contraseña incorrecta")
        pag.fill("#clave", CLAVE)
        pag.click("text=Entrar")
        pag.wait_for_selector("text=Partidos")
        assert "todavía no se conectó" in pag.content()

        # Cargar el partido desde el formulario.
        pag.click("text=+ Nuevo partido")
        pag.fill("#video_url", video_http)
        pag.fill("#desde", "0:20")
        pag.fill("#hasta", "1:20")
        pag.fill("#equipo_propio", "Club A")
        pag.fill("#kit_propio", "rayas")
        pag.select_option("#legible_propio", "baja")
        pag.fill("#equipo_rival", "Club B")
        pag.fill("#plantel", "1 Uno\n2 Dos")  # incompleto: avisa
        pag.click("button:has-text('Analizar')")
        pag.wait_for_selector("text=hacen falta por lo menos los 15")
        # La foto de la formación llena el plantel sola.
        from PIL import Image

        foto = tmp_path / "formacion.png"
        Image.new("RGB", (600, 900), (20, 80, 40)).save(foto)
        pag.set_input_files("input[type=file]", str(foto))
        pag.wait_for_selector("text=Leí 23 jugadores", timeout=20000)
        texto = pag.input_value("#plantel")
        assert texto.splitlines()[3] == "4 Jugador 4 (Curu)" and len(texto.splitlines()) == 23
        assert _ClaudeFalso.vistos[-1]["messages"][0]["content"][0]["type"] == "image"
        _foto(pag, "1_nuevo")
        pag.click("button:has-text('Analizar')")
        pag.wait_for_selector("text=Esperando a la compu")
        partido_id = int(pag.url.rstrip("/").split("/")[-1])
        _foto(pag, "2_esperando")

        # La compu toma el pedido y lo analiza (con Claude de mentira).
        nube = Nube(base_pg)
        nube.latido("esperando pedidos")
        # yt-dlp no sabe cortar un pedazo de un mp4 servido por un servidor
        # HTTP de prueba: para el análisis se usa el archivo directo (con un
        # link de YouTube real, baja el pedazo como siempre).
        with nube.cursor() as cur:
            cur.execute("UPDATE partidos SET video_url=%s WHERE id=%s",
                        (str(video), partido_id))
        trabajo = nube.tomar_trabajo()
        assert trabajo.partido_id == partido_id
        cfg = cfg_tackles(tmp_path)
        cliente, _ = cliente_tackles(tmp_path, servidor_tackles(experto_dice=(9,)))
        assert trabajador.procesar(nube, db, cfg, trabajo, cliente=cliente) == "listo"

        # La página se actualiza sola y queda en verde.
        pag.wait_for_selector(".chip.listo", timeout=15000)
        assert "Compu conectada" in pag.content()
        assert "Tackles por jugador" in pag.content()
        assert "Para revisar" in pag.content()

        _foto(pag, "3_listo")
        # Revisar con un clic: el primer candidato.
        tarjetas = pag.locator(".candidatos")
        antes = tarjetas.count()
        assert antes >= 1
        pag.locator(".candidato").first.click()
        pag.wait_for_function(f"document.querySelectorAll('.candidatos').length < {antes}", timeout=15000)

        # El Excel baja y tiene la hoja de jugadores con fórmulas.
        with pag.expect_download() as descarga:
            pag.click("text=Bajar Excel")
        contenido = Path(descarga.value.path()).read_bytes()
        from openpyxl import load_workbook

        libro = load_workbook(io.BytesIO(contenido))
        assert libro.sheetnames[0] == "Jugadores"
        assert str(libro["Jugadores"]["C2"].value).startswith("=COUNTIFS(")
        assert libro["Eventos"].max_row >= 2

        # En la lista, el partido aparece en verde con su resumen.
        pag.goto(app + "/")
        pag.wait_for_selector(".chip.listo")
        assert "tackles" in pag.content()
        _foto(pag, "4_lista")
        nav.close()
        nube.cerrar()


def _entrar(pag, app):
    pag.goto(app + "/login")
    pag.fill("#clave", CLAVE)
    pag.click("text=Entrar")
    pag.wait_for_selector("text=Partidos")


def test_club_equipos_y_album(app, base_pg, video_http, video, db, tmp_path):
    from PIL import Image

    with sync_api.sync_playwright() as pw:
        nav = pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        pag = nav.new_page(viewport={"width": 420, "height": 900})
        _entrar(pag, app)

        # Crear el club con sus cinco equipos.
        pag.click("nav >> text=Club")
        pag.fill("#nombre", "Club A")
        pag.fill("#kit", "rayas")
        pag.click("text=Crear club")
        pag.wait_for_selector("h1:has-text('Club A')")
        for e in ("Primera", "Intermedia", "Pre-A", "Pre-B", "Pre-C"):
            assert pag.locator(f"h2:has-text('{e}')").count() == 1

        # Un partido de Primera: los jugadores quedan en el club.
        pag.goto(app + "/nuevo")
        pag.select_option("#equipo_id", label="Club A · Primera")
        pag.wait_for_url("**/nuevo?equipo=*")
        assert pag.input_value("#equipo_propio") == "Club A"
        assert pag.input_value("#kit_propio") == "rayas"
        pag.fill("#video_url", video_http)
        pag.fill("#desde", "0:20")
        pag.fill("#hasta", "1:20")
        pag.fill("#equipo_rival", "Club B")
        pag.fill("#plantel", PLANTEL)
        pag.click("button:has-text('Analizar')")
        pag.wait_for_selector("text=Esperando a la compu")
        partido_id = int(pag.url.rstrip("/").split("/")[-1])

        nube = Nube(base_pg)
        with nube.cursor() as cur:
            cur.execute("SELECT count(*) FROM jugadores WHERE partido_id=%s AND persona_id IS NOT NULL", (partido_id,))
            assert cur.fetchone()[0] == 18
            cur.execute("UPDATE partidos SET video_url=%s WHERE id=%s", (str(video), partido_id))
        cfg = cfg_tackles(tmp_path)
        cliente, _ = cliente_tackles(tmp_path, servidor_tackles(experto_dice=(4,)))
        assert trabajador.procesar(nube, db, cfg, nube.tomar_trabajo(), cliente=cliente) == "listo"

        # El club muestra a los jugadores; el 4 ya tiene fotos del partido.
        pag.goto(app + "/club")
        pag.click("text=Club A")
        pag.wait_for_selector(".ficha-jugador")
        assert pag.locator(".ficha-jugador").count() == 18
        _foto(pag, "6_club")
        pag.click(".ficha-jugador:has-text('4 · Curu')")
        pag.wait_for_selector("h1:has-text('Jugador 4')")
        antes = pag.locator(".album figure").count()
        assert antes >= 1

        # Subir fotos al álbum a mano.
        fotos = []
        for k in range(2):
            ruta = tmp_path / f"subida{k}.png"
            Image.new("RGB", (900, 1200), (k * 100, 50, 50)).save(ruta)
            fotos.append(str(ruta))
        pag.set_input_files("input[type=file]", fotos)
        pag.wait_for_selector("text=Listo: 2 foto(s) nuevas.")
        pag.wait_for_function(f"document.querySelectorAll('.album figure').length === {antes + 2}")
        _foto(pag, "7_jugador")

        # El segundo partido de Primera ya trae el plantel cargado.
        pag.goto(app + "/nuevo")
        pag.select_option("#equipo_id", label="Club A · Primera")
        pag.wait_for_url("**/nuevo?equipo=*")
        assert pag.input_value("#plantel").splitlines()[3] == "4 Jugador 4 (Curu)"

        # Y en ese análisis, el álbum del 4 llega como fotos de antes.
        pag.fill("#video_url", video_http)
        pag.fill("#desde", "0:20")
        pag.fill("#hasta", "1:20")
        pag.fill("#equipo_rival", "Club C")
        pag.click("button:has-text('Analizar')")
        pag.wait_for_selector("text=Esperando a la compu")
        segundo = int(pag.url.rstrip("/").split("/")[-1])
        album = nube.album_de_partido(segundo)
        assert len(album.get(4, [])) == 3  # 2 subidas + 1 del partido anterior (tope 3)
        _foto(pag, "5_club")
        nav.close()
        nube.cerrar()
