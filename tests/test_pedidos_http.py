"""El cliente real contra un servidor falso.

Acá no se usa el cliente simulado: se usa ``ClienteGemini`` con el SDK de
verdad, y lo único falso es el transporte HTTP. Así se verifica lo que no se
puede ver de otra forma sin gastar cuota: qué JSON sale hacia Google, y cómo
reacciona el cliente a los errores que Google devuelve (cuota, modelo que no
existe, error del servidor, respuesta que no es JSON).
"""

from __future__ import annotations

import json

import pytest

httpx = pytest.importorskip("httpx2", reason="el SDK de Gemini usa httpx2")

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

from rugby_stats.gemini import prompts  # noqa: E402
from rugby_stats.gemini.cliente import (  # noqa: E402
    ClienteGemini,
    CuotaAgotada,
    ErrorGemini,
    Pedido,
)

URL = "https://www.youtube.com/watch?v=VsRZotfwL6A"


def respuesta_ok(texto: str = '{"ok": true}', tokens: int = 1234):
    return httpx.Response(
        200,
        json={
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": texto}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": tokens,
                "candidatesTokenCount": 7,
            },
        },
    )


def error(codigo: int, estado: str, mensaje: str):
    return httpx.Response(
        codigo,
        json={"error": {"code": codigo, "message": mensaje, "status": estado}},
    )


def armar(tmp_path, respuestas, **kwargs):
    """Un ClienteGemini cuyo HTTP contesta lo que diga ``respuestas``.

    ``respuestas`` es una lista (se consume en orden; la última se repite) o
    una función que recibe el pedido.
    """
    vistos: list[dict] = []

    def handler(req):
        cuerpo = json.loads(req.content) if req.content else {}
        vistos.append({"url": str(req.url), "cuerpo": cuerpo})
        if callable(respuestas):
            return respuestas(req, cuerpo)
        i = min(len(vistos) - 1, len(respuestas) - 1)
        return respuestas[i]

    opciones = dict(
        api_key="AQ.clave-de-prueba",
        modelo="gemini-3.8-flash",
        alternativas=["gemini-3.6-flash"],
        rpm=1_000_000,
        esperas_429=[0, 0, 0, 0],
        max_reintentos=2,
        dir_cache=tmp_path / "cache",
        usar_cache=False,
    )
    opciones.update(kwargs)
    c = ClienteGemini(**opciones)
    c.cliente = genai.Client(
        api_key="AQ.clave-de-prueba",
        http_options=types.HttpOptions(
            client_args={"transport": httpx.MockTransport(handler)},
            # Sin reintentos propios del SDK: los maneja nuestro cliente.
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )
    return c, vistos


def pedido_video(**kwargs) -> Pedido:
    base = dict(
        prompt="mirá esto",
        video_uri=URL,
        inicio_s=1368.0,
        fin_s=1400.0,
        fps=2.0,
        resolucion="MEDIA_RESOLUTION_LOW",
        esquema=prompts.ESQUEMA_EVENTOS,
        etiqueta="eventos 1368-1400",
    )
    base.update(kwargs)
    return Pedido(**base)


class TestLoQueSaleHaciaGoogle:
    def test_el_video_va_con_recorte_y_cuadros_por_segundo(self, tmp_path):
        c, vistos = armar(tmp_path, [respuesta_ok()])
        c.pedir(pedido_video())
        parte = vistos[0]["cuerpo"]["contents"][0]["parts"][0]
        archivo = parte["fileData"]
        assert (archivo.get("fileUri") or archivo.get("file_uri")) == URL
        meta = parte["videoMetadata"]
        inicio = meta.get("startOffset") or meta.get("start_offset")
        fin = meta.get("endOffset") or meta.get("end_offset")
        assert inicio == "1368.000s" and fin == "1400.000s"
        assert meta["fps"] == 2.0

    def test_la_resolucion_va_en_la_configuracion_del_pedido(self, tmp_path):
        """A nivel de la parte el SDK espera otro objeto: ahí rompía todo."""
        c, vistos = armar(tmp_path, [respuesta_ok()])
        c.pedir(pedido_video(resolucion="MEDIA_RESOLUTION_HIGH"))
        cuerpo = vistos[0]["cuerpo"]
        assert cuerpo["generationConfig"]["mediaResolution"] == "MEDIA_RESOLUTION_HIGH"
        parte = cuerpo["contents"][0]["parts"][0]
        assert "mediaResolution" not in parte and "media_resolution" not in parte

    def test_el_modelo_va_en_la_url(self, tmp_path):
        c, vistos = armar(tmp_path, [respuesta_ok()])
        c.pedir(pedido_video())
        assert "gemini-3.8-flash:generateContent" in vistos[0]["url"]

    def test_se_pide_json_con_esquema(self, tmp_path):
        c, vistos = armar(tmp_path, [respuesta_ok()])
        c.pedir(pedido_video())
        cfg = vistos[0]["cuerpo"]["generationConfig"]
        assert cfg["responseMimeType"] == "application/json"
        assert cfg["responseSchema"]["type"] == "OBJECT"

    def test_los_cuadros_por_segundo_no_pasan_de_24(self, tmp_path):
        c, vistos = armar(tmp_path, [respuesta_ok()])
        c.pedir(pedido_video(fps=60.0))
        meta = vistos[0]["cuerpo"]["contents"][0]["parts"][0]["videoMetadata"]
        assert meta["fps"] == 24.0

    @pytest.mark.parametrize(
        "nombre", [n for n in dir(prompts) if n.startswith("ESQUEMA_")]
    )
    def test_todos_los_esquemas_pasan_por_el_sdk(self, tmp_path, nombre):
        c, vistos = armar(tmp_path, [respuesta_ok()])
        c.pedir(pedido_video(esquema=getattr(prompts, nombre)))
        esquema = vistos[0]["cuerpo"]["generationConfig"]["responseSchema"]
        assert esquema["type"] == "OBJECT"
        # Los nombres de los campos tienen que ser ASCII: es lo seguro.
        assert json.dumps(esquema).isascii()

    def test_un_pedido_sin_video_no_manda_resolucion(self, tmp_path):
        c, vistos = armar(tmp_path, [respuesta_ok()])
        c.pedir(Pedido(prompt="hola"))
        assert "mediaResolution" not in vistos[0]["cuerpo"].get("generationConfig", {})


class TestRespuestas:
    def test_lee_el_json_y_los_tokens(self, tmp_path):
        c, _ = armar(tmp_path, [respuesta_ok('{"eventos": []}', tokens=3200)])
        r = c.pedir(pedido_video())
        assert r.json() == {"eventos": []}
        assert r.tokens_entrada == 3200
        assert c.contadores.tokens_entrada == 3200
        assert c.contadores.segundos_video == 32.0

    def test_json_envuelto_en_bloque_de_codigo(self, tmp_path):
        c, _ = armar(tmp_path, [respuesta_ok('```json\n{"eventos": []}\n```')])
        assert c.pedir(pedido_video()).json() == {"eventos": []}

    def test_respuesta_que_no_es_json_da_un_error_claro(self, tmp_path):
        c, _ = armar(tmp_path, [respuesta_ok("No puedo ver el video.")])
        r = c.pedir(pedido_video())
        with pytest.raises(ErrorGemini):
            r.json()

    def test_respuesta_vacia_por_filtro_no_explota_al_pedir(self, tmp_path):
        """Si Google bloquea la respuesta, no viene texto."""
        vacia = httpx.Response(
            200,
            json={
                "candidates": [{"finishReason": "SAFETY"}],
                "usageMetadata": {"promptTokenCount": 5},
            },
        )
        c, _ = armar(tmp_path, [vacia])
        r = c.pedir(pedido_video())
        assert r.texto == ""


class TestErrores:
    def test_ante_un_429_espera_y_reintenta(self, tmp_path):
        c, vistos = armar(
            tmp_path,
            [
                error(429, "RESOURCE_EXHAUSTED", "Quota exceeded"),
                error(429, "RESOURCE_EXHAUSTED", "Quota exceeded"),
                respuesta_ok(),
            ],
        )
        r = c.pedir(pedido_video())
        assert r.json() == {"ok": True}
        assert len(vistos) == 3
        assert c.contadores.errores_429 == 2
        assert c.ritmo.rpm < 1_000_000  # bajó el ritmo

    def test_si_la_cuota_no_vuelve_prueba_el_modelo_alternativo(self, tmp_path):
        def responder(req, cuerpo):
            if "gemini-3.8-flash" in str(req.url):
                return error(429, "RESOURCE_EXHAUSTED", "Quota exceeded")
            return respuesta_ok()

        c, vistos = armar(tmp_path, responder)
        r = c.pedir(pedido_video())
        assert r.modelo == "gemini-3.6-flash"
        assert any("gemini-3.6-flash" in v["url"] for v in vistos)

    def test_sin_cuota_en_ningun_modelo_avisa_claro(self, tmp_path):
        c, _ = armar(tmp_path, [error(429, "RESOURCE_EXHAUSTED", "Quota exceeded")])
        with pytest.raises(CuotaAgotada) as exc:
            c.pedir(pedido_video())
        assert "AI Studio" in str(exc.value)

    def test_modelo_que_no_existe_pasa_al_alternativo(self, tmp_path):
        def responder(req, cuerpo):
            if "gemini-3.8-flash" in str(req.url):
                return error(
                    404, "NOT_FOUND",
                    "models/gemini-3.8-flash is not found for API version v1beta",
                )
            return respuesta_ok()

        c, vistos = armar(tmp_path, responder)
        r = c.pedir(pedido_video())
        assert r.modelo == "gemini-3.6-flash"
        # No se queda reintentando un modelo que no existe.
        assert sum("gemini-3.8-flash" in v["url"] for v in vistos) == 1

    def test_una_vez_que_cambio_de_modelo_se_queda_en_ese(self, tmp_path):
        def responder(req, cuerpo):
            if "gemini-3.8-flash" in str(req.url):
                return error(404, "NOT_FOUND", "model is not found")
            return respuesta_ok()

        c, vistos = armar(tmp_path, responder)
        c.pedir(pedido_video())
        antes = len(vistos)
        c.pedir(pedido_video(inicio_s=1400.0, fin_s=1430.0))
        assert len(vistos) == antes + 1  # fue directo al que anda

    def test_error_del_servidor_se_reintenta(self, tmp_path):
        c, vistos = armar(
            tmp_path,
            [error(503, "UNAVAILABLE", "The model is overloaded"), respuesta_ok()],
        )
        assert c.pedir(pedido_video()).json() == {"ok": True}
        assert len(vistos) == 2

    def test_clave_invalida_no_se_reintenta_y_se_entiende(self, tmp_path):
        c, vistos = armar(
            tmp_path,
            [error(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key.")],
        )
        with pytest.raises(ErrorGemini) as exc:
            c.pedir(pedido_video())
        assert not isinstance(exc.value, CuotaAgotada)
        assert "API key" in str(exc.value)
        assert len(vistos) == 1

    def test_video_privado_no_se_reintenta(self, tmp_path):
        c, vistos = armar(
            tmp_path,
            [error(403, "PERMISSION_DENIED", "The caller does not have permission")],
        )
        with pytest.raises(ErrorGemini):
            c.pedir(pedido_video())
        assert len(vistos) == 1


class TestCacheConElClienteReal:
    def test_el_segundo_pedido_igual_no_va_a_la_red(self, tmp_path):
        c, vistos = armar(tmp_path, [respuesta_ok('{"eventos": []}')], usar_cache=True)
        c.pedir(pedido_video())
        r = c.pedir(pedido_video())
        assert r.desde_cache is True
        assert len(vistos) == 1
        assert r.json() == {"eventos": []}

    def test_un_error_no_queda_en_la_cache(self, tmp_path):
        c, vistos = armar(
            tmp_path,
            [error(400, "INVALID_ARGUMENT", "algo"), respuesta_ok()],
            usar_cache=True,
        )
        with pytest.raises(ErrorGemini):
            c.pedir(pedido_video())
        assert c.pedir(pedido_video()).json() == {"ok": True}
        assert len(vistos) == 2


class TestConexion:
    def test_si_la_conexion_se_cuelga_lo_dice_y_no_insiste(self, tmp_path):
        """Antes se quedaba esperando para siempre, sin decir nada."""
        intentos = []

        def colgada(req, cuerpo):
            intentos.append(1)
            raise httpx.ReadTimeout("The read operation timed out")

        c, _ = armar(tmp_path, colgada)
        with pytest.raises(ErrorGemini) as exc:
            c.pedir(Pedido(prompt="hola"))
        assert "No pude conectarme con Google" in str(exc.value)
        assert "antivirus" in str(exc.value)
        assert len(intentos) <= 2  # una vez por modelo como mucho

    def test_sin_internet_lo_dice(self, tmp_path):
        def sin_red(req, cuerpo):
            raise httpx.ConnectError("[Errno 11001] getaddrinfo failed")

        c, _ = armar(tmp_path, sin_red)
        with pytest.raises(ErrorGemini) as exc:
            c.pedir(Pedido(prompt="hola"))
        assert "No pude conectarme" in str(exc.value)

    def test_el_cliente_tiene_limite_de_tiempo(self, tmp_path):
        c = ClienteGemini(api_key="AQ.x", dir_cache=tmp_path, usar_cache=False)
        opciones = c.cliente._api_client._http_options
        assert opciones.timeout and opciones.timeout >= 60_000
