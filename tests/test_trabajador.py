"""La compu como trabajador de la app web, contra un Postgres de prueba.

Se corre si hay una base de prueba en ``RUGBY_PG_PRUEBA`` (por ejemplo
``postgresql://postgres@localhost:5433/postgres``). Cada prueba usa una base
nueva y la borra al final.
"""

from __future__ import annotations

import os
from pathlib import Path
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")
URL = os.environ.get("RUGBY_PG_PRUEBA", "")
pytestmark = pytest.mark.skipif(not URL, reason="sin RUGBY_PG_PRUEBA")

from rugby_stats.nube import Nube  # noqa: E402
from scripts import trabajador  # noqa: E402
from tests.test_claude import (  # noqa: E402
    armar_cliente,
    cfg_claude,
    servidor_por_esquema,
    video,  # noqa: F401 - fixture
)


@pytest.fixture
def nube():
    nombre = "prueba_" + uuid.uuid4().hex[:8]
    with psycopg.connect(URL, autocommit=True) as con:
        con.execute(f"CREATE DATABASE {nombre}")
    url = URL.rsplit("/", 1)[0] + "/" + nombre
    n = Nube(url)
    n.preparar()
    yield n
    n.cerrar()
    with psycopg.connect(URL, autocommit=True) as con:
        con.execute(f"DROP DATABASE {nombre} WITH (FORCE)")


def cargar_partido(nube: Nube, video_url: str) -> tuple[int, int]:
    with nube.cursor() as cur:
        cur.execute(
            """INSERT INTO partidos (nombre, equipo_propio, kit_propio, legible_propio,
               equipo_rival, kit_rival, video_url, desde_s, hasta_s, estado)
               VALUES ('Club A vs Club B','Club A','rayas','baja','Club B','liso',%s,20,80,'en_cola')
               RETURNING id""",
            (video_url,),
        )
        pid = cur.fetchone()[0]
        for n in range(1, 16):
            cur.execute(
                "INSERT INTO jugadores (partido_id, numero, nombre) VALUES (%s,%s,%s)",
                (pid, n, f"Jugador {n}"),
            )
        cur.execute("INSERT INTO trabajos (partido_id) VALUES (%s) RETURNING id", (pid,))
        tid = cur.fetchone()[0]
    return pid, tid


def test_el_pedido_de_la_app_se_analiza_y_queda_en_verde(nube, db, video, tmp_path):
    pid, _ = cargar_partido(nube, str(video))
    cfg = cfg_claude(tmp_path)
    cliente, vistos = armar_cliente(tmp_path, servidor_por_esquema)
    trabajo = nube.tomar_trabajo()
    assert trabajo and trabajo.partido_id == pid
    assert nube.partido(pid)["estado"] == "corriendo"
    assert trabajador.procesar(nube, db, cfg, trabajo, cliente=cliente) == "listo"

    p = nube.partido(pid)
    assert p["estado"] == "listo" and p["avance"] == 1.0
    assert p["gasto_usd"] > 0 and "tackles" in p["mensaje"]
    assert p["local_id"] and p["estimado_usd"] is not None
    with nube.cursor() as cur:
        cur.execute("SELECT accion, jugador_numero, jugador_modelo FROM eventos WHERE partido_id=%s", (pid,))
        eventos = cur.fetchall()
        cur.execute("SELECT count(*) FROM fotos WHERE partido_id=%s", (pid,))
        fotos = cur.fetchone()[0]
        cur.execute("SELECT estado FROM trabajos WHERE partido_id=%s", (pid,))
        assert cur.fetchone()[0] == "listo"
    assert any(a == "Tackle" and n == 4 and m == 4 for a, n, m in eventos)
    assert fotos >= 1
    assert nube.tomar_trabajo() is None


def test_lo_revisado_en_la_app_no_se_pisa(nube, db, video, tmp_path):
    pid, _ = cargar_partido(nube, str(video))
    cfg = cfg_claude(tmp_path)
    cliente, _ = armar_cliente(tmp_path, servidor_por_esquema)
    trabajador.procesar(nube, db, cfg, nube.tomar_trabajo(), cliente=cliente)
    with nube.cursor() as cur:
        cur.execute("SELECT count(*) FROM eventos WHERE partido_id=%s AND accion='Tackle'", (pid,))
        antes = cur.fetchone()[0]
        cur.execute(
            "UPDATE eventos SET jugador_numero=7, revisado=true WHERE id = (SELECT min(id) "
            "FROM eventos WHERE partido_id=%s AND accion='Tackle') RETURNING id", (pid,))
        revisado = cur.fetchone()[0]
        cur.execute("INSERT INTO trabajos (partido_id) VALUES (%s)", (pid,))
    trabajador.procesar(nube, db, cfg, nube.tomar_trabajo(), cliente=cliente)
    with nube.cursor() as cur:
        cur.execute("SELECT jugador_numero, revisado FROM eventos WHERE id=%s", (revisado,))
        assert cur.fetchone() == (7, True)
        cur.execute("SELECT count(*) FROM eventos WHERE partido_id=%s AND accion='Tackle'", (pid,))
        assert cur.fetchone()[0] == antes  # el nuevo de ese momento no se duplicó


def test_sin_clave_en_la_compu_avisa_en_la_app(nube, db, video, tmp_path):
    from rugby_stats.gemini.cliente import ClienteSimulado

    pid, _ = cargar_partido(nube, str(video))
    estado = trabajador.procesar(nube, db, cfg_claude(tmp_path), nube.tomar_trabajo(),
                                 cliente=ClienteSimulado())
    assert estado == "error"
    assert "ANTHROPIC_API_KEY" in nube.partido(pid)["mensaje"]


def test_si_la_compu_se_apago_a_mitad_vuelve_a_la_cola(nube, video):
    pid, tid = cargar_partido(nube, str(video))
    assert nube.tomar_trabajo().id == tid
    assert nube.recuperar_colgados() == 1
    assert nube.partido(pid)["estado"] == "en_cola"
    assert nube.tomar_trabajo().id == tid


def test_cancelar_desde_la_app(nube, db, video, tmp_path, monkeypatch):
    pid, tid = cargar_partido(nube, str(video))
    cfg = cfg_claude(tmp_path)
    cliente, _ = armar_cliente(tmp_path, servidor_por_esquema)
    trabajo = nube.tomar_trabajo()
    with nube.cursor() as cur:
        cur.execute("UPDATE trabajos SET estado='cancelado' WHERE id=%s", (tid,))
    reloj = iter(range(0, 100000, 20))
    monkeypatch.setattr(trabajador.time, "monotonic", lambda: float(next(reloj)))
    assert trabajador.procesar(nube, db, cfg, trabajo, cliente=cliente) == "cancelado"
    assert nube.partido(pid)["estado"] == "cancelado"


def test_latido(nube):
    nube.latido("esperando pedidos")
    with nube.cursor() as cur:
        cur.execute("SELECT estado, ultimo_latido IS NOT NULL FROM compu")
        assert cur.fetchone() == ("esperando pedidos", True)


def test_el_album_se_usa_y_se_alimenta(nube, db, video, tmp_path, monkeypatch):
    """Las fotos de antes llegan al análisis y las de hoy quedan en el álbum."""
    from rugby_stats import cuadros

    pid, _ = cargar_partido(nube, str(video))
    foto = cuadros.recortar(cuadros.un_cuadro(video, 5, tmp_path / "c.jpg", 640),
                            [0.2, 0.2, 0.4, 0.8], tmp_path / "f.jpg")
    with nube.cursor() as cur:
        cur.execute("INSERT INTO clubes (nombre) VALUES ('Club A') RETURNING id")
        club = cur.fetchone()[0]
        cur.execute("INSERT INTO equipos (club_id, nombre) VALUES (%s, 'Primera') RETURNING id", (club,))
        equipo = cur.fetchone()[0]
        cur.execute("UPDATE partidos SET equipo_id=%s WHERE id=%s", (equipo, pid))
        personas = {}
        for n in range(1, 16):
            cur.execute("INSERT INTO personas (club_id, equipo_id, nombre) VALUES (%s,%s,%s) RETURNING id",
                        (club, equipo, f"Jugador {n}"))
            personas[n] = cur.fetchone()[0]
            cur.execute("UPDATE jugadores SET persona_id=%s WHERE partido_id=%s AND numero=%s",
                        (personas[n], pid, n))
        cur.execute("INSERT INTO fotos_persona (persona_id, imagen) VALUES (%s, %s)",
                    (personas[9], foto.read_bytes()))

    recibido = {}
    original = trabajador.pipeline.correr

    def espiar(*a, **k):
        recibido.update(k.get("album") or {})
        return original(*a, **k)

    monkeypatch.setattr(trabajador.pipeline, "correr", espiar)
    cfg = cfg_claude(tmp_path)
    cliente, _ = armar_cliente(tmp_path, servidor_por_esquema)
    assert trabajador.procesar(nube, db, cfg, nube.tomar_trabajo(), cliente=cliente) == "listo"
    assert list(recibido) == [9] and Path(recibido[9][0]).exists()
    with nube.cursor() as cur:
        cur.execute("SELECT persona_id, origen FROM fotos_persona WHERE origen='partido'")
        nuevas = cur.fetchall()
    # El servidor falso lee al 4 y al 9 con confianza alta: quedan en su álbum.
    assert {p for p, _ in nuevas} == {personas[4], personas[9]}
    # Analizar de nuevo no duplica las de este partido.
    with nube.cursor() as cur:
        cur.execute("INSERT INTO trabajos (partido_id) VALUES (%s)", (pid,))
    trabajador.procesar(nube, db, cfg, nube.tomar_trabajo(), cliente=cliente)
    with nube.cursor() as cur:
        cur.execute("SELECT count(*) FROM fotos_persona WHERE origen='partido'")
        assert cur.fetchone()[0] == len(nuevas)


def test_el_despertador_escucha_los_avisos(tmp_path):
    """La app avisa por ntfy y la compu se despierta sin esperar su vuelta."""
    import http.server
    import threading

    class Ntfy(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"event":"open"}\n{"event":"keepalive"}\n')
            self.wfile.write(b'{"event":"message","message":"trabajo"}\n')
            self.wfile.flush()

        def log_message(self, *a):
            pass

    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Ntfy)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    d = trabajador.Despertador("canal", base=f"http://127.0.0.1:{servidor.server_address[1]}")
    d.start()
    try:
        assert d.esperar(10) is True
    finally:
        d.parar()
        servidor.shutdown()


def test_el_canal_de_aviso_es_siempre_el_mismo(tmp_path):
    a = trabajador.canal_de_aviso(tmp_path)
    assert a.startswith("rugby-stats-") and len(a) > 25
    assert trabajador.canal_de_aviso(tmp_path) == a
