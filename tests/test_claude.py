"""El camino de Claude, de punta a punta, sin gastar un centavo.

Se genera un video sintético con ffmpeg, se levanta un servidor de Anthropic
falso (el SDK de verdad, con un transporte HTTP de mentira) y se corre el
análisis completo. Lo que se verifica es lo que no se puede ver de otra forma
sin gastar plata:

- que los cuadros salgan del tramo correcto, con su segundo exacto al lado;
- que ningún pedido pase el límite de imágenes;
- que la plata se cuente bien y el tope frene ANTES de mandar el pedido;
- que las fichas visuales se recorten y viajen en los pedidos siguientes;
- que los errores de la API se traduzcan en algo que se entienda.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

httpx = pytest.importorskip("httpx2", reason="el SDK de Anthropic usa httpx2")
pytest.importorskip("anthropic")

from rugby_stats import costos, cuadros  # noqa: E402
from rugby_stats.analisis import pipeline  # noqa: E402
from rugby_stats.config import Config  # noqa: E402
from rugby_stats.gemini.cliente import Pedido  # noqa: E402
from rugby_stats.modelos import Equipo, FuenteVideo, Partido  # noqa: E402
from rugby_stats.proveedor_claude import (  # noqa: E402
    ClienteClaude,
    SinSaldo,
    TopeDeGastoAlcanzado,
    esquema_para_claude,
)
from tests.conftest import CONFIG_BASE, hacer_plantel  # noqa: E402

FFMPEG = cuadros.ffmpeg_exe()
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="hace falta ffmpeg")

PRECIOS = {
    "claude-sonnet-5-5": {"entrada": 2.0, "salida": 10.0, "cache_lectura": 0.1, "cache_escritura": 2.5},
    "claude-haiku-5-5": {"entrada": 0.1, "salida": 0.5, "cache_lectura": 0.01, "cache_escritura": 0.125},
}


# ---------------------------------------------------------------------------
# Piezas
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def video(tmp_path_factory) -> Path:
    """Dos minutos de video de prueba, 640x360, con el tiempo impreso."""
    ruta = tmp_path_factory.mktemp("video") / "sintetico.mp4"
    subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=10:duration=120",
         "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
         str(ruta)],
        check=True,
    )
    return ruta


def cfg_claude(tmp_path, tope: float = 10.0) -> Config:
    datos = json.loads(json.dumps(CONFIG_BASE))
    datos["proveedor"] = "claude"
    datos["claude"] = {
        "modelos": {"fuerte": ["claude-sonnet-5-5"], "liviano": ["claude-haiku-5-5"]},
        "esfuerzo": "low",
        "tope_usd_por_partido": tope,
        "max_cuadros_por_pedido": 40,
        "precios": PRECIOS,
        "velocidades": {
            "gruesa": {"fps": 0.2, "ventana_s": 60, "ancho": 320},
            "juego": {"fps": 2.0, "ventana_s": 20, "ancho": 320},
            "contacto": {"fps": 6.0, "ventana_s": 3.0, "ancho": 448,
                         "margen_antes_s": 1.5, "margen_despues_s": 1.5},
            "fichas": {"fps": 1.0, "ventana_s": 20, "ancho": 448},
        },
    }
    datos["api"]["cache_dir"] = str(tmp_path / "cache")
    datos["video"] = {"dir_trabajo": str(tmp_path / "video"), "descargar_si_dura_mas_de_s": 0}
    datos["salida"] = {"dir": str(tmp_path / "salidas"), "jugadores_de": "propio"}
    datos["analisis"] = {"solo_equipo_propio": True}
    return Config(datos, raiz=tmp_path)


def respuesta(datos: dict, entrada: int = 5000, salida: int = 300,
              cache_lect: int = 0, cache_escr: int = 0, modelo: str = "claude-sonnet-5-5",
              stop: str = "end_turn"):
    return httpx.Response(
        200,
        json={
            "id": "msg_x", "type": "message", "role": "assistant", "model": modelo,
            "content": [{"type": "text", "text": json.dumps(datos, ensure_ascii=False)}],
            "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": entrada, "output_tokens": salida,
                      "cache_read_input_tokens": cache_lect,
                      "cache_creation_input_tokens": cache_escr},
        },
    )


def error(codigo: int, tipo: str, mensaje: str, encabezados: dict | None = None):
    return httpx.Response(
        codigo,
        json={"type": "error", "error": {"type": tipo, "message": mensaje}},
        headers=encabezados or {},
    )


def tiempos(cuerpo: dict) -> list[float]:
    """Los segundos que el cliente le anunció al modelo, en orden."""
    salida = []
    for b in cuerpo["messages"][0]["content"]:
        if b.get("type") == "text" and b["text"].startswith("t="):
            salida.append(float(b["text"][2:].split()[0]))
    return salida


def imagenes(cuerpo: dict) -> int:
    return sum(1 for b in cuerpo["messages"][0]["content"] if b.get("type") == "image")


def servidor_por_esquema(req, cuerpo):
    """Contesta según qué esquema pide el pedido, con datos plausibles."""
    props = (
        cuerpo.get("output_config", {}).get("format", {}).get("schema", {}).get("properties", {})
    )
    ts = tiempos(cuerpo) or [0.0]
    medio = ts[len(ts) // 2]
    if "tramos" in props:
        return respuesta({"tramos": [
            {"inicio_s": ts[0], "fin_s": ts[-1] + 1, "tipo": "juego", "confianza": 0.9}
        ]}, modelo="claude-haiku-5-5")
    if "lecturas" in props:
        return respuesta({"lecturas": [
            {"numero": 4, "t_segundos": medio, "confianza": 0.9,
             "rasgos": {"casco": "no", "color_botines": "rojos"},
             "caja": [0.30, 0.20, 0.45, 0.90]},
            {"numero": 9, "t_segundos": medio, "confianza": 0.8,
             "rasgos": {"pelo": "oscuro"}, "caja": [0.6, 0.3, 0.7, 0.95]},
        ]})
    if "eventos" in props:
        return respuesta({"eventos": [
            {"t_segundos": medio, "accion": "Carry", "resultado": "detenido",
             "equipo": "rival", "numero": None, "confianza": 0.6},
            {"t_segundos": ts[0] + 1, "accion": "Pase", "resultado": "bueno",
             "equipo": "propio", "numero": 9, "confianza": 0.95},
        ]}, modelo="claude-haiku-5-5")
    if "tackleadores" in props:
        return respuesta({
            "portador": None, "portador_equipo": "rival",
            "tackleadores": [4], "tackleadores_equipo": "propio",
            "avance_portador": "se_detiene", "confianza": 0.9,
            "confianza_identificacion": 0.88,
        })
    if "cambios" in props:
        return respuesta({"cambios": []}, modelo="claude-haiku-5-5")
    if "fragmentos" in props:
        return respuesta({"fragmentos": []}, modelo="claude-haiku-5-5")
    return respuesta({"ok": True})


def armar_cliente(tmp_path, responder, tope: float = 10.0, max_cuadros: int = 40,
                  **kwargs):
    vistos: list[dict] = []

    def handler(req):
        cuerpo = json.loads(req.content)
        vistos.append(cuerpo)
        return responder(req, cuerpo)

    c = ClienteClaude(
        api_key="sk-ant-prueba",
        niveles={"fuerte": ["claude-sonnet-5-5"], "liviano": ["claude-haiku-5-5"]},
        precios=PRECIOS,
        dir_cache=tmp_path / "cache",
        usar_cache=kwargs.pop("usar_cache", False),
        dir_cuadros=tmp_path / "cuadros",
        max_cuadros=max_cuadros,
        tope_usd=tope,
        esperas=[0, 0, 0, 0],
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        **kwargs,
    )
    return c, vistos


@pytest.fixture
def partido_local(db, video) -> Partido:
    p = Partido(
        nombre="Club A vs Club B",
        equipo_propio=Equipo(nombre="Club A", kit="rayas", numero_legible="baja", es_propio=True),
        equipo_rival=Equipo(nombre="Club B", kit="liso", numero_legible="alta"),
        video=FuenteVideo(tipo="archivo", referencia=str(video), ruta_local=str(video)),
        analizar_desde_s=20.0,
        analizar_hasta_s=80.0,
    )
    pid = db.guardar_partido(p)
    db.guardar_plantel(pid, hacer_plantel("Club A"))
    return db.partido(pid)


# ---------------------------------------------------------------------------
# Cuadros
# ---------------------------------------------------------------------------


class TestCuadros:
    def test_saca_los_cuadros_del_tramo_pedido(self, video, tmp_path):
        lista = cuadros.extraer(video, 30.0, 40.0, 2.0, 320, tmp_path)
        assert len(lista) == 20
        assert lista[0].t == 30.0 and lista[-1].t == pytest.approx(39.5)
        assert all(c.ruta.exists() for c in lista)
        assert lista[0].ancho == 320 and lista[0].alto == 180

    def test_con_un_archivo_bajado_a_pedazos_los_tiempos_siguen_siendo_del_partido(
        self, video, tmp_path
    ):
        """Si el archivo arranca en el segundo 1000 del partido, el cuadro del
        segundo 1030 es el segundo 30 del archivo."""
        directo = cuadros.extraer(video, 30.0, 31.0, 1.0, 320, tmp_path / "a")
        con_desfase = cuadros.extraer(video, 1030.0, 1031.0, 1.0, 320, tmp_path / "b",
                                      desfase_archivo_s=1000.0)
        assert con_desfase[0].t == 1030.0
        assert directo[0].ruta.read_bytes() == con_desfase[0].ruta.read_bytes()

    def test_si_pide_demasiados_baja_los_cuadros_por_segundo(self, video, tmp_path):
        lista = cuadros.extraer(video, 0.0, 100.0, 2.0, 320, tmp_path, maximo=25)
        assert len(lista) <= 25
        assert lista[-1].t > 90  # sigue cubriendo todo el tramo

    def test_la_segunda_vez_no_vuelve_a_sacarlos(self, video, tmp_path):
        a = cuadros.extraer(video, 10.0, 12.0, 2.0, 320, tmp_path)
        mtime = a[0].ruta.stat().st_mtime_ns
        b = cuadros.extraer(video, 10.0, 12.0, 2.0, 320, tmp_path)
        assert b[0].ruta.stat().st_mtime_ns == mtime

    def test_tokens_por_cuadro(self):
        assert cuadros.tokens_imagen(640, 360) == 23 * 13
        assert cuadros.tokens_imagen(1008, 567) == 36 * 21

    def test_recorte_de_un_jugador(self, video, tmp_path):
        cuadro = cuadros.un_cuadro(video, 50.0, tmp_path / "c.jpg", 640)
        recorte = cuadros.recortar(cuadro, [0.25, 0.25, 0.5, 0.75], tmp_path / "r.jpg")
        from PIL import Image

        with Image.open(recorte) as im:
            assert im.size[1] == 384

    @pytest.mark.parametrize("caja", [[1, 2, 3], ["a", 0, 1, 1], [0.5, 0.5, 0.5, 0.5],
                                      [2000, 0, 3000, 400]])
    def test_cajas_raras_no_recortan(self, video, tmp_path, caja):
        cuadro = cuadros.un_cuadro(video, 50.0, tmp_path / "c.jpg", 640)
        assert cuadros.recortar(cuadro, caja, tmp_path / "r.jpg") is None

    def test_caja_en_escala_0_a_1000_se_entiende(self, video, tmp_path):
        cuadro = cuadros.un_cuadro(video, 50.0, tmp_path / "c.jpg", 640)
        assert cuadros.recortar(cuadro, [250, 250, 500, 750], tmp_path / "r.jpg")

    def test_duracion(self, video):
        assert cuadros.duracion_video(video) == pytest.approx(120.0, abs=0.5)


# ---------------------------------------------------------------------------
# Lo que sale hacia Anthropic
# ---------------------------------------------------------------------------


class TestPedido:
    def test_cada_cuadro_va_con_su_segundo(self, video, tmp_path):
        c, vistos = armar_cliente(tmp_path, servidor_por_esquema)
        c.pedir(Pedido(prompt="mirá", video_uri=str(video), inicio_s=40, fin_s=45,
                       fps=2, ancho=320, nivel="liviano", esquema={"type": "object",
                       "properties": {"ok": {"type": "boolean"}}}))
        cuerpo = vistos[0]
        assert tiempos(cuerpo) == [40.0, 40.5, 41.0, 41.5, 42.0, 42.5, 43.0, 43.5, 44.0, 44.5]
        assert imagenes(cuerpo) == 10
        assert cuerpo["model"] == "claude-haiku-5-5"
        # El texto con las instrucciones va al final, después de los cuadros.
        assert cuerpo["messages"][0]["content"][-1]["text"] == "mirá"

    def test_pide_json_con_esquema_y_poco_esfuerzo(self, video, tmp_path):
        c, vistos = armar_cliente(tmp_path, servidor_por_esquema)
        c.pedir(Pedido(prompt="x", esquema={"type": "object", "properties": {
            "n": {"type": "integer", "nullable": True}}}))
        oc = vistos[0]["output_config"]
        assert oc["effort"] == "low"
        assert oc["format"]["type"] == "json_schema"
        assert oc["format"]["schema"]["properties"]["n"]["type"] == ["integer", "null"]
        assert oc["format"]["schema"]["additionalProperties"] is False

    def test_las_fichas_visuales_van_primero_y_en_cache(self, video, tmp_path):
        foto = cuadros.un_cuadro(video, 10, tmp_path / "f.jpg", 320)
        c, vistos = armar_cliente(tmp_path, servidor_por_esquema)
        c.pedir(Pedido(prompt="x", video_uri=str(video), inicio_s=50, fin_s=51, fps=2,
                       ancho=320, referencias=[("4. Pablo Curuchet", str(foto)),
                                               ("9. Felipe Yofre", str(foto))]))
        bloques = vistos[0]["messages"][0]["content"]
        assert "FICHAS VISUALES" in bloques[0]["text"]
        assert bloques[1]["text"] == "Ficha: 4. Pablo Curuchet"
        assert bloques[2]["type"] == "image"
        assert bloques[4]["cache_control"] == {"type": "ephemeral"}  # la última ficha
        assert "CUADROS DEL VIDEO" in bloques[5]["text"]

    def test_una_ficha_cuya_imagen_no_existe_se_saltea(self, video, tmp_path):
        c, vistos = armar_cliente(tmp_path, servidor_por_esquema)
        c.pedir(Pedido(prompt="x", referencias=[("4. X", str(tmp_path / "no.jpg"))]))
        assert imagenes(vistos[0]) == 0

    def test_nunca_pasa_el_maximo_de_imagenes(self, video, tmp_path):
        c, vistos = armar_cliente(tmp_path, servidor_por_esquema, max_cuadros=30)
        c.pedir(Pedido(prompt="x", video_uri=str(video), inicio_s=0, fin_s=110,
                       fps=2, ancho=320))
        assert imagenes(vistos[0]) <= 30

    def test_sin_el_video_en_disco_lo_dice(self, tmp_path):
        from rugby_stats.gemini.cliente import ErrorGemini

        c, _ = armar_cliente(tmp_path, servidor_por_esquema)
        with pytest.raises(ErrorGemini) as exc:
            c.pedir(Pedido(prompt="x", video_uri=str(tmp_path / "no.mp4"), inicio_s=0, fin_s=5))
        assert "tiene que estar bajado" in str(exc.value)


class TestEsquema:
    def test_conversion_profunda(self):
        from rugby_stats.gemini import prompts

        for nombre in [n for n in dir(prompts) if n.startswith("ESQUEMA_")]:
            esq = esquema_para_claude(getattr(prompts, nombre))
            texto = json.dumps(esq)
            assert "nullable" not in texto, nombre

            def revisar(nodo):
                if isinstance(nodo, dict):
                    if nodo.get("type") == "object":
                        assert nodo.get("additionalProperties") is False
                    for v in nodo.values():
                        revisar(v)
                elif isinstance(nodo, list):
                    for v in nodo:
                        revisar(v)

            revisar(esq)

    def test_no_toca_el_original(self):
        original = {"type": "object", "properties": {"a": {"type": "integer", "nullable": True}}}
        esquema_para_claude(original)
        assert original["properties"]["a"]["nullable"] is True


# ---------------------------------------------------------------------------
# Plata
# ---------------------------------------------------------------------------


class TestPlata:
    def test_cuenta_lo_que_cobra_cada_modelo(self, tmp_path):
        c, _ = armar_cliente(
            tmp_path,
            lambda req, cuerpo: respuesta({"ok": True}, entrada=100_000, salida=10_000,
                                          cache_lect=50_000, modelo=cuerpo["model"]),
        )
        c.pedir(Pedido(prompt="a", nivel="fuerte", esquema={"type": "object"}))
        # Sonnet: 0.1 M × 2 + 0.01 M × 10 + 0.05 M × 0.1
        assert c.gasto_usd == pytest.approx(0.2 + 0.1 + 0.005)
        c.pedir(Pedido(prompt="b", nivel="liviano", esquema={"type": "object"}))
        # Haiku: 0.1 M × 0.1 + 0.01 M × 0.5 + 0.05 M × 0.01
        assert c.gasto_usd == pytest.approx(0.305 + 0.01 + 0.005 + 0.0005)
        assert set(c.resumen_gasto()["por_modelo"]) == {"claude-sonnet-5-5", "claude-haiku-5-5"}

    def test_el_tope_frena_antes_de_mandar(self, video, tmp_path):
        """El pedido que se pasaría del tope no sale: no se paga."""
        c, vistos = armar_cliente(tmp_path, servidor_por_esquema, tope=0.01)
        c.empezar_partido(gasto_previo_usd=0.0, tope_usd=0.01)
        with pytest.raises(TopeDeGastoAlcanzado) as exc:
            c.pedir(Pedido(prompt="x", video_uri=str(video), inicio_s=0, fin_s=20,
                           fps=2, ancho=1008, nivel="fuerte"))
        assert vistos == []
        assert "tope de gasto" in str(exc.value)
        assert "tope_usd_por_partido" in str(exc.value)

    def test_el_tope_cuenta_lo_gastado_antes_en_el_partido(self, tmp_path):
        c, vistos = armar_cliente(tmp_path, servidor_por_esquema, tope=5.0)
        c.empezar_partido(gasto_previo_usd=4.999, tope_usd=5.0)
        with pytest.raises(TopeDeGastoAlcanzado):
            c.pedir(Pedido(prompt="x", nivel="fuerte"))
        assert vistos == []

    def test_sin_tope_no_frena(self, tmp_path):
        c, vistos = armar_cliente(tmp_path, servidor_por_esquema, tope=0)
        c.empezar_partido(gasto_previo_usd=1000.0, tope_usd=0)
        c.pedir(Pedido(prompt="x"))
        assert len(vistos) == 1

    def test_un_modelo_sin_precio_se_cobra_como_el_mas_caro(self, tmp_path):
        """Para que el tope nunca quede corto por un precio que falta."""
        c, _ = armar_cliente(tmp_path, servidor_por_esquema)
        assert c.costo("claude-desconocido", 1_000_000, 0) == pytest.approx(2.0)

    def test_lo_que_vino_de_la_cache_no_se_paga(self, tmp_path):
        c, vistos = armar_cliente(tmp_path, servidor_por_esquema, usar_cache=True)
        p = Pedido(prompt="x", esquema={"type": "object", "properties": {"ok": {"type": "boolean"}}})
        c.pedir(p)
        gasto = c.gasto_usd
        r = c.pedir(p)
        assert r.desde_cache and c.gasto_usd == gasto and len(vistos) == 1


# ---------------------------------------------------------------------------
# Errores
# ---------------------------------------------------------------------------


class TestErrores:
    def test_ocupado_espera_y_reintenta(self, tmp_path, monkeypatch):
        respuestas = iter([
            error(529, "overloaded_error", "Overloaded"),
            error(429, "rate_limit_error", "Too many", {"retry-after": "3"}),
            respuesta({"ok": True}),
        ])
        dormidas: list[float] = []
        monkeypatch.setattr("rugby_stats.proveedor_claude.time.sleep", dormidas.append)
        c, vistos = armar_cliente(tmp_path, lambda req, cuerpo: next(respuestas))
        avisos: list[str] = []
        c.al_avisar = avisos.append
        assert c.pedir(Pedido(prompt="x", esquema={"type": "object"})).json() == {"ok": True}
        assert len(vistos) == 3
        assert dormidas[1] == pytest.approx(4.0)  # lo que pidió el encabezado + 1
        assert len(avisos) == 2

    def test_clave_mal_lo_dice_claro(self, tmp_path):
        from rugby_stats.gemini.cliente import ErrorGemini

        c, vistos = armar_cliente(
            tmp_path, lambda req, cuerpo: error(401, "authentication_error", "invalid x-api-key")
        )
        with pytest.raises(ErrorGemini) as exc:
            c.pedir(Pedido(prompt="x"))
        assert "ANTHROPIC_API_KEY" in str(exc.value)
        assert len(vistos) == 1

    def test_sin_saldo_lo_dice_claro(self, tmp_path):
        c, _ = armar_cliente(
            tmp_path,
            lambda req, cuerpo: error(400, "invalid_request_error",
                                      "Your credit balance is too low to access the API."),
        )
        with pytest.raises(SinSaldo) as exc:
            c.pedir(Pedido(prompt="x"))
        assert "saldo" in str(exc.value)

    def test_si_no_acepta_el_formato_sigue_sin_el(self, tmp_path):
        """Por si la salida estructurada no anda con algún esquema."""
        def responder(req, cuerpo):
            if "format" in cuerpo.get("output_config", {}):
                return error(400, "invalid_request_error", "output_config.format: invalid json_schema")
            return respuesta({"ok": True})

        c, vistos = armar_cliente(tmp_path, responder)
        r = c.pedir(Pedido(prompt="x", esquema={"type": "object", "properties": {"ok": {"type": "boolean"}}}))
        assert r.json() == {"ok": True}
        assert "esquema" in vistos[-1]["messages"][0]["content"][-1]["text"]
        # Y no vuelve a intentarlo en los pedidos siguientes.
        c.pedir(Pedido(prompt="y", esquema={"type": "object"}))
        assert "format" not in vistos[-1].get("output_config", {})

    def test_si_no_acepta_el_esfuerzo_sigue_sin_el(self, tmp_path):
        def responder(req, cuerpo):
            if "effort" in cuerpo.get("output_config", {}):
                return error(400, "invalid_request_error", "effort is not supported for this model")
            return respuesta({"ok": True})

        c, vistos = armar_cliente(tmp_path, responder)
        assert c.pedir(Pedido(prompt="x", esquema={"type": "object"})).json() == {"ok": True}

    def test_modelo_que_no_existe_pasa_al_siguiente(self, tmp_path):
        def responder(req, cuerpo):
            if cuerpo["model"] == "claude-viejo":
                return error(404, "not_found_error", "model: claude-viejo")
            return respuesta({"ok": True})

        c, vistos = armar_cliente(tmp_path, responder)
        c.niveles["fuerte"] = ["claude-viejo", "claude-sonnet-5-5"]
        r = c.pedir(Pedido(prompt="x", esquema={"type": "object"}))
        assert r.modelo == "claude-sonnet-5-5"

    def test_una_respuesta_cortada_no_queda_en_la_cache(self, tmp_path):
        def responder(req, cuerpo):
            return httpx.Response(200, json={
                "id": "m", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
                "content": [{"type": "text", "text": '{"eventos": [{"t_segundos": 1'}],
                "stop_reason": "max_tokens", "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 8000},
            })

        c, vistos = armar_cliente(tmp_path, responder, usar_cache=True)
        p = Pedido(prompt="x", esquema={"type": "object"})
        c.pedir(p)
        c.pedir(p)
        assert len(vistos) == 2


# ---------------------------------------------------------------------------
# De punta a punta
# ---------------------------------------------------------------------------


class TestPuntaAPunta:
    def test_el_analisis_completo_con_claude(self, db, partido_local, tmp_path):
        cfg = cfg_claude(tmp_path)
        c, vistos = armar_cliente(tmp_path, servidor_por_esquema)
        res = pipeline.correr(db, c, partido_local.id, cfg)

        assert set(res.pasos_corridos) == set(pipeline.PASOS)
        assert vistos, "no se mandó ningún pedido"
        # Ningún pedido mira fuera del tramo (más los márgenes de los contactos).
        for cuerpo in vistos:
            for t in tiempos(cuerpo):
                assert 15.0 <= t <= 85.0, t
            assert imagenes(cuerpo) <= 40 + 23
        # Las fichas se recortaron y viajaron en los pedidos de contactos.
        fichas = db.fichas(partido_local.id, "Club A")
        assert any(f.imagen and Path(f.imagen).exists() for f in fichas)
        contactos = [v for v in vistos if "tackleadores" in json.dumps(v.get("output_config", {}))]
        assert contactos
        assert any("FICHAS VISUALES" in json.dumps(v) for v in contactos)
        # Los eventos: el tackle del 4 salió del re-examen; nada del rival.
        eventos = db.eventos(partido_local.id)
        assert any(e.accion == "Tackle" and e.jugador_numero == 4 and e.resultado == "positivo"
                   for e in eventos)
        assert all(e.equipo == "Club A" for e in eventos)
        # La plata quedó registrada.
        assert res.gasto_usd > 0
        assert db.gasto_partido(partido_local.id) == pytest.approx(res.gasto_usd)
        assert "US$" in res.resumen()

    def test_el_relato_por_subtitulos_se_corrige_con_el_plantel(
        self, db, partido_local, tmp_path, monkeypatch
    ):
        from rugby_stats import subtitulos

        p = db.partido(partido_local.id)
        p.video.tipo = "youtube"
        p.video.referencia = "https://www.youtube.com/watch?v=VsRZotfwL6A"
        db.guardar_partido(p)
        monkeypatch.setattr(subtitulos, "relato_desde_youtube", lambda *a, **k: [
            {"inicio_s": 30.0, "fin_s": 45.0, "texto": "tacklea Shofre", "jugadores_nombrados": []},
        ])

        def responder(req, cuerpo):
            texto = json.dumps(cuerpo)
            if "fragmentos" in texto and "Shofre" in texto:
                return respuesta({"fragmentos": [
                    {"inicio_s": 30.0, "fin_s": 45.0, "texto": "tacklea Yofre",
                     "jugadores_nombrados": [9]}]}, modelo="claude-haiku-5-5")
            return servidor_por_esquema(req, cuerpo)

        cfg = cfg_claude(tmp_path)
        c, vistos = armar_cliente(tmp_path, responder)
        pipeline.correr(db, c, partido_local.id, cfg, pasos=["relato"])
        relato = db.transcripcion(partido_local.id)
        assert relato[0]["texto"] == "tacklea Yofre"
        assert relato[0]["jugadores_nombrados"] == [9]
        pedido = vistos[0]
        assert pedido["model"] == "claude-haiku-5-5"
        assert imagenes(pedido) == 0  # texto solo: barato

    def test_el_tope_corta_el_analisis_y_registra_lo_gastado(
        self, db, partido_local, tmp_path
    ):
        cfg = cfg_claude(tmp_path, tope=0.05)
        c, vistos = armar_cliente(tmp_path, servidor_por_esquema, tope=0.05)
        with pytest.raises(TopeDeGastoAlcanzado):
            pipeline.correr(db, c, partido_local.id, cfg)
        gastado = db.gasto_partido(partido_local.id)
        assert 0 < gastado <= 0.05
        # Al volver a correr, el tope cuenta lo ya gastado: no arranca de cero.
        antes = len(vistos)
        with pytest.raises(TopeDeGastoAlcanzado):
            pipeline.correr(db, c, partido_local.id, cfg)
        assert db.gasto_partido(partido_local.id) <= 0.05 + 1e-9
        assert len(vistos) - antes <= 1

    def test_lo_gastado_no_se_cuenta_doble_si_se_reusa_el_cliente(
        self, db, partido_local, tmp_path
    ):
        cfg = cfg_claude(tmp_path)
        c, _ = armar_cliente(tmp_path, servidor_por_esquema)
        r1 = pipeline.correr(db, c, partido_local.id, cfg, pasos=["gruesa"])
        r2 = pipeline.correr(db, c, partido_local.id, cfg, pasos=["eventos"])
        assert db.gasto_partido(partido_local.id) == pytest.approx(r1.gasto_usd + r2.gasto_usd)


# ---------------------------------------------------------------------------
# Estimación
# ---------------------------------------------------------------------------


class TestEstimacion:
    def test_crece_con_el_largo(self, tmp_path):
        cfg = cfg_claude(tmp_path)
        corto, largo = costos.estimar(360, cfg), costos.estimar(4800, cfg)
        assert 0 < corto.usd < largo.usd

    def test_la_configuracion_del_repo_da_un_partido_razonable(self):
        """Con Haiku para el volumen y Sonnet para lo difícil, un partido
        entero tiene que quedar en el orden de los 5 dólares."""
        from rugby_stats.config import cargar_config

        cfg = cargar_config(Path(__file__).resolve().parent.parent / "config.yaml")
        e = costos.estimar(80 * 60, cfg)
        assert 2 < e.usd < 10
        assert cfg.tope_usd_por_partido >= e.usd  # el tope deja terminar un partido

    def test_cero_segundos(self, tmp_path):
        assert costos.estimar(0, cfg_claude(tmp_path)).usd == 0
