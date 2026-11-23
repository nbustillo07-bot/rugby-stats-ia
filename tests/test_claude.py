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
        mas_caro = max(p["entrada"] for p in PRECIOS.values())
        assert c.costo("claude-desconocido", 1_000_000, 0) == pytest.approx(mas_caro)

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


# ---------------------------------------------------------------------------
# Tackles: zoom, varias fotos, segunda opinión, lógica de juego
# ---------------------------------------------------------------------------

PRECIOS["claude-opus-5-5"] = {"entrada": 4.0, "salida": 20.0, "cache_lectura": 0.2,
                              "cache_escritura": 5.0}


def cfg_tackles(tmp_path, experto: bool = True) -> Config:
    cfg = cfg_claude(tmp_path)
    d = cfg._d
    d["analisis"]["medir"] = "tackles"
    d["claude"]["modelos"]["experto"] = ["claude-opus-5-5"] if experto else []
    d["claude"]["velocidades"]["contacto"] = {
        "fps": 3.0, "ventana_s": 1.0, "ancho": 448, "margen_antes_s": 1.5,
        "margen_despues_s": 6.0,
    }
    d["claude"]["velocidades"]["localizar"] = {"fps": 2.0, "ventana_s": 1.0, "ancho": 320}
    d["claude"]["zoom"] = {"activo": True, "ancho_fuente": 640, "escala": 2.0,
                           "minimo": 0.3, "contexto_fps": 1.0, "contexto_ancho": 320}
    d["claude"]["fotos_por_jugador"] = 3
    d["claude"]["umbral_segunda_opinion"] = 0.6
    return cfg


def servidor_tackles(experto_dice=(4,), fuerte_conf=0.4, hay_tackle=True, quien=None):
    def responder(req, cuerpo):
        props = (cuerpo.get("output_config", {}).get("format", {})
                 .get("schema", {}).get("properties", {}))
        ts = tiempos(cuerpo) or [0.0]
        if "puntos" in props:
            extra = {"quien_tacklea": quien, "seguridad": 0.95} if quien else {}
            return respuesta({"se_ve_el_contacto": True, **extra, "puntos": [
                {"t_segundos": ts[0], "caja": [0.2, 0.3, 0.4, 0.6]},
                {"t_segundos": ts[-1], "caja": [0.5, 0.3, 0.7, 0.6]},
            ]}, modelo="claude-haiku-5-5")
        if "eventos" in props:
            medio = ts[len(ts) // 2]
            return respuesta({"eventos": [
                {"t_segundos": medio, "accion": "Tackle", "resultado": "",
                 "equipo": "propio", "numero": None, "confianza": 0.5},
                {"t_segundos": ts[0] + 1, "accion": "Pase", "resultado": "bueno",
                 "equipo": "propio", "numero": 9, "confianza": 0.95},
            ]}, modelo="claude-haiku-5-5")
        if "tackleadores" in props:
            if cuerpo["model"] == "claude-opus-5-5":
                return respuesta({
                    "portador_equipo": "rival", "tackleadores": list(experto_dice),
                    "tackleadores_equipo": "propio", "avance_portador": "se_detiene",
                    "confianza": 0.9, "confianza_identificacion": 0.9,
                    "hay_tackle_propio": True,
                    "candidatos": [{"numero": n, "probabilidad": 0.8} for n in experto_dice],
                }, modelo="claude-opus-5-5")
            return respuesta({
                "portador_equipo": "rival", "tackleadores": [4],
                "tackleadores_equipo": "propio", "avance_portador": "se_detiene",
                "confianza": 0.9, "confianza_identificacion": fuerte_conf,
                "hay_tackle_propio": hay_tackle,
                "candidatos": [{"numero": 4, "probabilidad": 0.5},
                               {"numero": 7, "probabilidad": 0.3}],
            })
        return servidor_por_esquema(req, cuerpo)
    return responder


def cliente_tackles(tmp_path, responder):
    c, vistos = armar_cliente(tmp_path, responder)
    c.niveles["experto"] = ["claude-opus-5-5"]
    return c, vistos


class TestZoom:
    def test_interpolar_y_ventana(self):
        pista = [(10.0, (0.1, 0.1, 0.2, 0.2)), (12.0, (0.5, 0.5, 0.6, 0.6))]
        assert cuadros.interpolar_caja(pista, 11.0) == pytest.approx((0.3, 0.3, 0.4, 0.4))
        assert cuadros.interpolar_caja(pista, 5.0) == (0.1, 0.1, 0.2, 0.2)
        z = cuadros.ventana_zoom((0.9, 0.9, 1.0, 1.0), escala=2.0, minimo=0.3)
        assert 0 <= z[0] < z[2] <= 1 and 0 <= z[1] < z[3] <= 1
        assert z[2] - z[0] == pytest.approx(0.3)

    def test_los_recortes_siguen_al_contacto(self, video, tmp_path):
        pista = [(40.0, (0.0, 0.0, 0.1, 0.1)), (43.0, (0.8, 0.8, 0.9, 0.9))]
        lista = cuadros.extraer_zoom(video, 40.0, 43.0, 2.0, pista, 320, tmp_path,
                                     ancho_fuente=640, escala=2.0, minimo=0.3)
        assert len(lista) == 6
        assert all(c.ancho == 320 and c.alto == 180 for c in lista)
        assert lista[0].ruta.read_bytes() != lista[-1].ruta.read_bytes()

    def test_hoja_de_referencias(self, video, tmp_path):
        foto = cuadros.recortar(cuadros.un_cuadro(video, 5, tmp_path / "c.jpg", 640),
                                [0.2, 0.2, 0.4, 0.8], tmp_path / "f.jpg")
        hoja = cuadros.hoja_referencias([("4. A", [foto, foto]), ("9. B", [foto])],
                                        tmp_path / "h.jpg")
        from PIL import Image

        with Image.open(hoja) as im:
            assert im.size[1] > 2 * 200

    def test_el_pedido_lleva_zoom_y_vista_general(self, video, tmp_path):
        c, vistos = armar_cliente(tmp_path, servidor_por_esquema)
        c.pedir(Pedido(prompt="x", video_uri=str(video), inicio_s=40, fin_s=44, fps=3,
                       ancho=320, zoom={"pista": [[40, [0.2, 0.2, 0.4, 0.4]]],
                                        "ancho_fuente": 640, "contexto_fps": 1.0,
                                        "contexto_ancho": 320}))
        textos = [b["text"] for b in vistos[0]["messages"][0]["content"] if b.get("type") == "text"]
        assert sum("zoom al contacto" in x and x.startswith("t=") for x in textos) == 12
        assert sum("vista general" in x and x.startswith("t=") for x in textos) == 4


class TestReferencias:
    def test_varias_fotos_por_jugador_en_hojas(self, video, tmp_path):
        from rugby_stats.analisis import pasadas
        from rugby_stats.modelos import Ficha

        foto = cuadros.recortar(cuadros.un_cuadro(video, 5, tmp_path / "c.jpg", 640),
                                [0.2, 0.2, 0.4, 0.8], tmp_path / "f.jpg")
        jugadores = hacer_plantel("Club A")
        fotos = [
            Ficha(jugador_numero=j.numero, equipo="Club A", t_segundos=t, imagen=str(foto),
                  confianza=0.9)
            for j in jugadores[:6] for t in (10, 12, 40, 90)  # 12 está muy cerca de 10
        ]
        refs = pasadas.referencias_visuales({}, jugadores, fotos=fotos, por_jugador=3,
                                            carpeta=tmp_path / "hojas")
        assert len(refs) == 2  # 6 jugadores de a 4 por hoja
        assert all(Path(r[1]).exists() for r in refs)

    def test_una_foto_sin_carpeta(self, video, tmp_path):
        from rugby_stats.analisis import pasadas
        from rugby_stats.modelos import Ficha

        foto = cuadros.un_cuadro(video, 5, tmp_path / "c.jpg", 320)
        jugadores = hacer_plantel("Club A")
        fichas = {4: Ficha(jugador_numero=4, equipo="Club A", t_segundos=1, imagen=str(foto))}
        assert len(pasadas.referencias_visuales(fichas, jugadores)) == 1


class TestTackles:
    def test_solo_tackles_con_zoom_y_segunda_opinion_que_coincide(
        self, db, partido_local, tmp_path
    ):
        cfg = cfg_tackles(tmp_path)
        c, vistos = cliente_tackles(tmp_path, servidor_tackles(experto_dice=(4,)))
        pipeline.correr(db, c, partido_local.id, cfg)
        eventos = db.eventos(partido_local.id)
        assert eventos and all(e.accion in ("Tackle", "Tackle errado") for e in eventos)
        t4 = [e for e in eventos if e.jugador_numero == 4]
        assert t4 and all(not e.pendiente for e in t4)
        assert all(e.confianza >= 0.85 for e in t4)
        # Se ubicó el contacto, se mandó con zoom, y opinó el experto.
        assert any("puntos" in json.dumps(v.get("output_config", {})) for v in vistos)
        contactos = [v for v in vistos if "tackleadores" in json.dumps(v.get("output_config", {}))]
        assert any(v["model"] == "claude-opus-5-5" for v in contactos)
        assert any("zoom al contacto" in json.dumps(v, ensure_ascii=False) for v in contactos)
        # Mira hasta 6 s después del contacto.
        for v in contactos:
            ts = tiempos(v)
            assert max(ts) - min(ts) >= 6.5

    def test_si_no_coinciden_queda_pendiente_con_los_candidatos(
        self, db, partido_local, tmp_path
    ):
        from rugby_stats import revision

        cfg = cfg_tackles(tmp_path)
        c, _ = cliente_tackles(tmp_path, servidor_tackles(experto_dice=(9,)))
        pipeline.correr(db, c, partido_local.id, cfg)
        tackles = [e for e in db.eventos(partido_local.id) if e.accion == "Tackle"]
        assert tackles and all(e.pendiente for e in tackles)
        numeros = {x["numero"] for x in revision.candidatos_de(tackles[0])}
        assert {4, 9} <= numeros
        assert "no coinciden" in tackles[0].nota

    def test_un_falso_positivo_se_borra(self, db, partido_local, tmp_path):
        cfg = cfg_tackles(tmp_path, experto=False)
        c, _ = cliente_tackles(tmp_path, servidor_tackles(hay_tackle=False, fuerte_conf=0.9))
        pipeline.correr(db, c, partido_local.id, cfg)
        assert [e for e in db.eventos(partido_local.id) if e.accion == "Tackle"] == []

    def test_si_tacklea_el_rival_no_se_gasta_el_zoom(self, db, partido_local, tmp_path):
        cfg = cfg_tackles(tmp_path)
        c, vistos = cliente_tackles(tmp_path, servidor_tackles(quien="rival"))
        pipeline.correr(db, c, partido_local.id, cfg)
        assert [e for e in db.eventos(partido_local.id) if e.accion == "Tackle"] == []
        assert not [v for v in vistos if "tackleadores" in json.dumps(v.get("output_config", {}))]

    def test_si_tacklea_el_propio_sigue_al_zoom(self, db, partido_local, tmp_path):
        cfg = cfg_tackles(tmp_path)
        c, vistos = cliente_tackles(tmp_path, servidor_tackles(quien="propio"))
        pipeline.correr(db, c, partido_local.id, cfg)
        assert [v for v in vistos if "tackleadores" in json.dumps(v.get("output_config", {}))]

    def test_sin_experto_no_se_le_pregunta(self, db, partido_local, tmp_path):
        cfg = cfg_tackles(tmp_path, experto=False)
        c, vistos = cliente_tackles(tmp_path, servidor_tackles())
        c.niveles.pop("experto", None)
        pipeline.correr(db, c, partido_local.id, cfg)
        assert all(v["model"] != "claude-opus-5-5" for v in vistos)

    def test_el_que_no_esta_en_cancha_no_puede_tacklear(self, db, partido_local, tmp_path):
        from rugby_stats.analisis import reglas

        # El reloj del partido va 10 minutos adelante del video, y el 4 salió
        # en el minuto 5.
        p = db.partido(partido_local.id)
        p.video.desfase_s = 600.0
        db.guardar_partido(p)
        jugadores = db.plantel(partido_local.id, "Club A")
        j4 = next(j for j in jugadores if j.numero == 4)
        j4.salio_min = 5.0
        assert 4 not in reglas.en_cancha(jugadores, 10.0)
        db.actualizar_cambio(partido_local.id, "Club A", 4, None, 5.0)
        cfg = cfg_tackles(tmp_path, experto=False)
        cfg._d.setdefault("pasos", {})
        c, _ = cliente_tackles(tmp_path, servidor_tackles(fuerte_conf=0.95))
        pipeline.correr(db, c, partido_local.id, cfg, pasos=["gruesa", "fichas", "eventos", "contactos"])
        t4 = [e for e in db.eventos(partido_local.id) if e.jugador_numero == 4]
        assert t4 and all(e.pendiente and "no estaba en cancha" in e.nota for e in t4)

    def test_sin_cambios_detectados_no_restringe(self):
        from rugby_stats.analisis import reglas

        assert reglas.en_cancha(hacer_plantel("Club A"), 30.0) is None

    def test_dos_tackles_del_mismo_casi_juntos(self):
        from rugby_stats.analisis import pasadas
        from rugby_stats.modelos import Evento

        a = Evento(t_segundos=10, accion="Tackle", equipo="Club A", jugador_numero=4, resultado="positivo")
        b = Evento(t_segundos=11.5, accion="Tackle", equipo="Club A", jugador_numero=4, resultado="positivo")
        c = Evento(t_segundos=30, accion="Tackle", equipo="Club A", jugador_numero=4, resultado="positivo")
        pasadas._marcar_imposibles([
            {"t_segundos": 10, "eventos": [a]}, {"t_segundos": 11.5, "eventos": [b]},
            {"t_segundos": 30, "eventos": [c]},
        ], "Club A")
        assert a.pendiente and b.pendiente and not c.pendiente

    def test_unir_candidatos(self):
        from rugby_stats.analisis import reglas

        r = reglas.unir_candidatos([{"numero": 4, "probabilidad": 0.6}, {"numero": 7, "probabilidad": 0.3}],
                                   [9], [{"numero": "4", "probabilidad": 0.8}])
        assert [x["numero"] for x in r][0] == 4
        assert {x["numero"] for x in r} == {4, 7, 9}

    def test_estimacion_en_modo_tackles(self, tmp_path):
        cfg = cfg_tackles(tmp_path)
        con = costos.estimar(4800, cfg)
        sin = costos.estimar(4800, cfg_tackles(tmp_path, experto=False))
        assert con.usd > sin.usd > 0
        assert {"localizar", "contactos", "experto"} <= {p.paso for p in con.pasos}


class TestAlbum:
    def test_las_fotos_de_antes_entran_aunque_hoy_no_haya_foto(self, video, tmp_path):
        from rugby_stats.analisis import pasadas
        from rugby_stats.modelos import Ficha

        foto = cuadros.recortar(cuadros.un_cuadro(video, 5, tmp_path / "c.jpg", 640),
                                [0.2, 0.2, 0.4, 0.8], tmp_path / "f.jpg")
        jugadores = hacer_plantel("Club A")
        hoy = [Ficha(jugador_numero=4, equipo="Club A", t_segundos=10, imagen=str(foto), confianza=0.9)]
        refs = pasadas.referencias_visuales({}, jugadores, fotos=hoy, por_jugador=3,
                                            carpeta=tmp_path / "hojas",
                                            album={9: [str(foto)], 99: [str(foto)]})
        assert len(refs) == 1
        assert "4." in refs[0][0] and "9." in refs[0][0]  # el 99 no está en el plantel


class TestAguante:
    def test_un_pedido_que_falla_no_corta_el_partido(self, db, partido_local, tmp_path):
        """Una ventana con un error puntual se saltea; el resto sigue."""
        estado = {"fallados": 0}

        def responder(req, cuerpo):
            props = json.dumps(cuerpo.get("output_config", {}))
            if '"eventos"' in props and estado["fallados"] == 0:
                estado["fallados"] += 1
                return error(400, "invalid_request_error", "Could not process image")
            return servidor_por_esquema(req, cuerpo)

        c, vistos = armar_cliente(tmp_path, responder)
        res = pipeline.correr(db, c, partido_local.id, cfg_claude(tmp_path))
        assert estado["fallados"] == 1
        assert "eventos" in res.pasos_corridos
        assert any(e.accion == "Tackle" for e in db.eventos(partido_local.id))

    def test_si_falla_todo_seguido_si_corta(self, db, partido_local, tmp_path):
        from rugby_stats.gemini.cliente import ErrorGemini

        c, _ = armar_cliente(tmp_path, lambda req, cuerpo: error(400, "invalid_request_error", "roto"))
        with pytest.raises(ErrorGemini):
            pipeline.correr(db, c, partido_local.id, cfg_claude(tmp_path), pasos=["eventos"])

    def test_sin_saldo_corta_enseguida(self, db, partido_local, tmp_path):
        c, vistos = armar_cliente(
            tmp_path, lambda req, cuerpo: error(400, "invalid_request_error",
                                                "Your credit balance is too low"))
        with pytest.raises(SinSaldo):
            pipeline.correr(db, c, partido_local.id, cfg_claude(tmp_path), pasos=["eventos"])
        assert len(vistos) == 1

    def test_una_cuenta_nueva_con_limites_bajos_espera_y_sigue(self, tmp_path, monkeypatch):
        """Muchos 429 seguidos (límite por minuto) no cortan: espera lo que pide."""
        respuestas = iter([error(429, "rate_limit_error", "rate", {"retry-after": "7"})] * 9
                          + [respuesta({"ok": True})])
        dormidas: list[float] = []
        monkeypatch.setattr("rugby_stats.proveedor_claude.time.sleep", dormidas.append)
        c, vistos = armar_cliente(tmp_path, lambda req, cuerpo: next(respuestas))
        assert c.pedir(Pedido(prompt="x", esquema={"type": "object"})).json() == {"ok": True}
        assert len(vistos) == 10 and all(d == 8.0 for d in dormidas)


class TestCacheDelTexto:
    def test_lo_fijo_va_primero_y_en_cache(self, video, tmp_path):
        c, vistos = armar_cliente(tmp_path, servidor_por_esquema)
        c.pedir(Pedido(prompt="FIJO largo\n\nESTE CONTACTO: t=10", prefijo="FIJO largo",
                       video_uri=str(video), inicio_s=10, fin_s=11, fps=2, ancho=320))
        bloques = vistos[0]["messages"][0]["content"]
        assert bloques[0] == {"type": "text", "text": "FIJO largo", "cache_control": {"type": "ephemeral"}}
        assert bloques[-1]["text"] == "ESTE CONTACTO: t=10"

    def test_los_contactos_del_partido_comparten_el_mismo_texto_fijo(self, db, partido_local, tmp_path):
        cfg = cfg_tackles(tmp_path, experto=False)
        c, vistos = cliente_tackles(tmp_path, servidor_tackles(fuerte_conf=0.95))
        pipeline.correr(db, c, partido_local.id, cfg)
        fijos = [v["messages"][0]["content"][0] for v in vistos
                 if "tackleadores" in json.dumps(v.get("output_config", {}))]
        assert len(fijos) >= 2
        assert all(f.get("cache_control") for f in fijos)
        assert len({f["text"] for f in fijos}) == 1


from rugby_stats.analisis import pasadas  # noqa: E402
from rugby_stats.gemini import prompts  # noqa: E402
from rugby_stats.modelos import Ficha  # noqa: E402


class TestCamisetas:
    def _equipo(self):
        from rugby_stats.modelos import Equipo
        return Equipo(nombre="Los Molinos", kit="Rayas horizontales negras blancas y bordo")

    def test_lisa_no_es_la_rayada(self):
        eq = self._equipo()
        assert not pasadas._camiseta_coincide({"camiseta_vista": "bordo lisa"}, eq)
        assert not pasadas._camiseta_coincide({"rasgos": {"otro": "camiseta bordo lisa"}}, eq)
        assert not pasadas._camiseta_coincide({"es_del_propio": False}, eq)
        assert pasadas._camiseta_coincide({"camiseta_vista": "rayas negras y blancas"}, eq)
        assert pasadas._camiseta_coincide({}, eq)

    def test_el_prompt_pide_mirar_la_camiseta(self):
        from rugby_stats.modelos import Equipo
        texto = prompts.prompt_fichas(self._equipo(), hacer_plantel("Los Molinos"), 0, 60,
                                      rival=Equipo(nombre="Lanus", kit=""))
        assert "CAMISETA" in texto and "Lanus" in texto
        assert "es_del_propio" in prompts.ESQUEMA_FICHAS["properties"]["lecturas"]["items"]["required"]
        assert "REGLA DE LA CAMISETA" in prompts.describir_equipos(self._equipo(), Equipo(nombre="Lanus"))

    def test_la_verificacion_saca_las_fotos_del_rival(self, video, tmp_path):
        from rugby_stats.modelos import Equipo
        foto = cuadros.un_cuadro(video, 5, tmp_path / "c.jpg", 320)

        class Cliente:
            visto = None
            def pedir(self, p):
                Cliente.visto = p
                class R:
                    def json(self):
                        return {"del_propio": [1], "de_otro_equipo": [2]}
                return R()

        fichas = [Ficha(jugador_numero=6, equipo="Los Molinos", t_segundos=t, imagen=str(foto))
                  for t in (10, 20)]
        quedan = pasadas._verificar_camisetas(Cliente(), self._equipo(), Equipo(nombre="Lanus"), fichas)
        assert [f.t_segundos for f in quedan] == [10]
        assert Cliente.visto.nivel == "liviano" and len(Cliente.visto.referencias) == 2
