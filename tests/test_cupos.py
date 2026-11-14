"""El cupo gratis: 20 pedidos por día en los modelos buenos, 500 en los livianos.

Estos tests usan el mensaje textual que devolvió Google el 14/11/2026 cuando
se acabó el cupo, y verifican que el cliente haga lo razonable: no insistir
con un modelo agotado, pasar al siguiente, y frenar con un mensaje claro
cuando no queda ninguno.
"""

from __future__ import annotations

import json
import time

import pytest

httpx = pytest.importorskip("httpx2", reason="el SDK de Gemini usa httpx2")

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

from rugby_stats.gemini import cliente as mod  # noqa: E402
from rugby_stats.gemini.cliente import (  # noqa: E402
    ClienteGemini,
    CupoDiarioAgotado,
    Cupos,
    Pedido,
)

NIVELES = {
    "fuerte": ["gemini-3.8-flash", "gemini-3.6-flash"],
    "liviano": ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"],
}


def cupo_diario(modelo: str):
    """La respuesta real de Google al acabarse el cupo del día."""
    return httpx.Response(
        429,
        json={
            "error": {
                "code": 429,
                "message": (
                    "You exceeded your current quota, please check your plan and "
                    "billing details. \n* Quota exceeded for metric: "
                    "generativelanguage.googleapis.com/generate_content_free_tier_requests, "
                    f"limit: 20, model: {modelo}, description: Generate content "
                    "requests per day limit exceeded on Free Tier.\n"
                    "Please retry in 10h24m10.154644444s."
                ),
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                        "violations": [
                            {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                             "quotaValue": "20"}
                        ],
                    },
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo",
                     "retryDelay": "37450s"},
                ],
            }
        },
    )


def muy_rapido():
    return httpx.Response(
        429,
        json={
            "error": {
                "code": 429,
                "message": (
                    "Quota exceeded for metric: generate_content_free_tier_requests, "
                    "limit: 5, description: Generate content requests per minute "
                    "limit exceeded.\nPlease retry in 21.5s."
                ),
                "status": "RESOURCE_EXHAUSTED",
                "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo",
                             "retryDelay": "21s"}],
            }
        },
    )


def ok():
    return httpx.Response(
        200,
        json={
            "candidates": [{"content": {"role": "model", "parts": [{"text": '{"ok": true}'}]},
                            "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 3},
        },
    )


def modelo_de(req) -> str:
    return str(req.url).split("/models/")[1].split(":")[0]


@pytest.fixture
def armar(tmp_path, monkeypatch):
    dormidas: list[float] = []
    monkeypatch.setattr(mod.time, "sleep", dormidas.append)

    def _armar(responder, respaldo=None, archivo=True):
        usados: list[str] = []

        def handler(req):
            usados.append(modelo_de(req))
            return responder(req)

        c = ClienteGemini(
            api_key="AQ.x",
            niveles=NIVELES,
            rpm_por_nivel={"fuerte": 1e9, "liviano": 1e9},
            respaldo=respaldo,
            esperas_429=[0, 0, 0],
            max_reintentos=2,
            dir_cache=tmp_path / "cache",
            usar_cache=False,
            archivo_cupos=(tmp_path / "cupos.json") if archivo else None,
        )
        c.cliente = genai.Client(
            api_key="AQ.x",
            http_options=types.HttpOptions(
                client_args={"transport": httpx.MockTransport(handler)},
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
        return c, usados

    _armar.dormidas = dormidas
    return _armar


class TestNiveles:
    def test_cada_pedido_va_a_los_modelos_de_su_nivel(self, armar):
        c, usados = armar(lambda req: ok())
        c.pedir(Pedido(prompt="a", nivel="liviano"))
        c.pedir(Pedido(prompt="b", nivel="fuerte"))
        assert usados == ["gemini-3.5-flash-lite", "gemini-3.8-flash"]

    def test_sin_niveles_usa_modelo_y_alternativas_como_antes(self, tmp_path):
        c = ClienteGemini(api_key="AQ.x", modelo="m1", alternativas=["m2"],
                          dir_cache=tmp_path, usar_cache=False)
        assert c.modelos_de("fuerte") == ["m1", "m2"]
        assert c.modelos_de("liviano") == ["m1", "m2"]


class TestCupoDiario:
    def test_no_reintenta_un_modelo_sin_cupo_y_pasa_al_siguiente(self, armar):
        """Reintentar no sirve: Google dice que vuelve en 10 horas."""
        c, usados = armar(
            lambda req: cupo_diario("gemini-3.8-flash")
            if modelo_de(req) == "gemini-3.8-flash" else ok()
        )
        r = c.pedir(Pedido(prompt="a", nivel="fuerte"))
        assert r.modelo == "gemini-3.6-flash"
        assert usados == ["gemini-3.8-flash", "gemini-3.6-flash"]  # un intento, no tres
        assert armar.dormidas == []                                 # y sin esperar

    def test_el_siguiente_pedido_ni_prueba_el_modelo_agotado(self, armar):
        c, usados = armar(
            lambda req: cupo_diario("gemini-3.8-flash")
            if modelo_de(req) == "gemini-3.8-flash" else ok()
        )
        c.pedir(Pedido(prompt="a", nivel="fuerte"))
        usados.clear()
        c.pedir(Pedido(prompt="b", nivel="fuerte"))
        assert usados == ["gemini-3.6-flash"]

    def test_sin_ningun_fuerte_frena_con_un_mensaje_que_se_entiende(self, armar):
        c, usados = armar(lambda req: cupo_diario(modelo_de(req)))
        with pytest.raises(CupoDiarioAgotado) as exc:
            c.pedir(Pedido(prompt="a", nivel="fuerte"))
        texto = str(exc.value)
        assert "cupo gratis de hoy" in texto
        assert "10 horas" in texto
        assert "quedó guardado" in texto
        assert exc.value.nivel == "fuerte"
        # Por defecto NO se pasa a los livianos: cuida la precisión.
        assert all("lite" not in m for m in usados)

    def test_agotado_todo_el_nivel_el_proximo_pedido_falla_sin_ir_a_la_red(self, armar):
        c, usados = armar(lambda req: cupo_diario(modelo_de(req)))
        with pytest.raises(CupoDiarioAgotado):
            c.pedir(Pedido(prompt="a", nivel="fuerte"))
        usados.clear()
        with pytest.raises(CupoDiarioAgotado):
            c.pedir(Pedido(prompt="b", nivel="fuerte"))
        assert usados == []

    def test_los_livianos_siguen_andando_aunque_se_acaben_los_fuertes(self, armar):
        c, _ = armar(
            lambda req: ok() if "lite" in modelo_de(req) else cupo_diario(modelo_de(req))
        )
        with pytest.raises(CupoDiarioAgotado):
            c.pedir(Pedido(prompt="a", nivel="fuerte"))
        assert c.pedir(Pedido(prompt="b", nivel="liviano")).json() == {"ok": True}

    def test_con_permiso_sigue_con_los_livianos(self, armar):
        avisos: list[str] = []
        c, usados = armar(
            lambda req: ok() if "lite" in modelo_de(req) else cupo_diario(modelo_de(req)),
            respaldo={"fuerte": "liviano"},
        )
        c.al_avisar = avisos.append
        r = c.pedir(Pedido(prompt="a", nivel="fuerte"))
        assert r.modelo == "gemini-3.5-flash-lite"
        assert any("menos precisos" in a for a in avisos)

    def test_lo_agotado_se_recuerda_entre_una_corrida_y_la_siguiente(self, armar):
        c, _ = armar(
            lambda req: cupo_diario("gemini-3.8-flash")
            if modelo_de(req) == "gemini-3.8-flash" else ok()
        )
        c.pedir(Pedido(prompt="a", nivel="fuerte"))
        c2, usados2 = armar(lambda req: ok())  # "otro día de uso", mismo archivo
        c2.pedir(Pedido(prompt="b", nivel="fuerte"))
        assert usados2 == ["gemini-3.6-flash"]


class TestDemasiadoRapido:
    def test_espera_lo_que_pide_google_y_reintenta_el_mismo_modelo(self, armar):
        respuestas = iter([muy_rapido(), ok()])
        c, usados = armar(lambda req: next(respuestas))
        r = c.pedir(Pedido(prompt="a", nivel="fuerte"))
        assert r.modelo == "gemini-3.8-flash"
        assert usados == ["gemini-3.8-flash", "gemini-3.8-flash"]
        assert armar.dormidas == [pytest.approx(22.0)]  # 21 s que pidió + 1
        assert not c.cupos.agotado("gemini-3.8-flash")  # no se da por agotado


class TestLeerLaDemora:
    @pytest.mark.parametrize(
        "texto,esperado",
        [
            ("Please retry in 10h24m10.154644444s.", 10 * 3600 + 24 * 60 + 10.154644444),
            ("Please retry in 21.5s.", 21.5),
            ("Please retry in 3m5s.", 185.0),
            ("Please retry in 250ms.", None),
            ("{'retryDelay': '37450s'}", 37450.0),
            ('"retryDelay": "21s"', 21.0),
            ("sin datos", None),
        ],
    )
    def test_formatos(self, texto, esperado):
        resultado = mod._demora_pedida(RuntimeError(texto))
        if esperado is None:
            assert resultado is None or resultado < 1
        else:
            assert resultado == pytest.approx(esperado)

    def test_distingue_tope_diario_de_ritmo(self):
        diario = RuntimeError("Generate content requests per day limit exceeded")
        ritmo = RuntimeError("requests per minute limit exceeded")
        assert mod._es_cupo_diario(diario, 37450)
        assert not mod._es_cupo_diario(ritmo, 20)
        # Sin texto claro manda la demora: 10 horas no es "ir muy rápido".
        assert mod._es_cupo_diario(RuntimeError("quota"), 36000)
        assert not mod._es_cupo_diario(RuntimeError("quota"), 30)
        assert not mod._es_cupo_diario(RuntimeError("quota"), None)


class TestCupos:
    def test_cuenta_los_pedidos_de_hoy(self, tmp_path):
        c = Cupos(tmp_path / "c.json")
        for _ in range(3):
            c.anotar("m")
        assert c.usados("m") == 3
        assert Cupos(tmp_path / "c.json").usados("m") == 3  # quedó en disco

    def test_al_cambiar_el_dia_la_cuenta_arranca_de_cero(self, tmp_path, monkeypatch):
        c = Cupos(tmp_path / "c.json")
        c.anotar("m")
        monkeypatch.setattr(Cupos, "_hoy", staticmethod(lambda: "2099-01-01"))
        assert c.usados("m") == 0
        assert Cupos(tmp_path / "c.json").usados("m") == 0

    def test_lo_agotado_vuelve_solo_cuando_pasa_el_tiempo(self, tmp_path, monkeypatch):
        c = Cupos(tmp_path / "c.json")
        c.marcar_agotado("m", 100)
        assert c.agotado("m")
        assert c.proxima_renovacion(["m"]) == pytest.approx(100, abs=2)
        ahora = time.time()
        monkeypatch.setattr(mod.time, "time", lambda: ahora + 101)
        assert not c.agotado("m")

    def test_sin_archivo_funciona_igual(self):
        c = Cupos(None)
        c.anotar("m")
        c.marcar_agotado("m", 10)
        assert c.usados("m") == 1 and c.agotado("m")

    def test_un_archivo_roto_no_rompe_nada(self, tmp_path):
        (tmp_path / "c.json").write_text("{ roto", encoding="utf-8")
        c = Cupos(tmp_path / "c.json")
        c.anotar("m")
        assert json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))["uso"] == {"m": 1}


class TestCacheEntreModelos:
    def test_lo_que_contesto_un_modelo_sirve_cuando_le_toca_a_otro(self, armar, tmp_path):
        """Si hoy contestó 3.8 y mañana le toca a 3.6, no se vuelve a pagar."""
        c, usados = armar(lambda req: ok())
        c.cache = mod.Cache(tmp_path / "cache", True)
        c.pedir(Pedido(prompt="a", nivel="fuerte"))
        c.cupos.marcar_agotado("gemini-3.8-flash", 3600)
        r = c.pedir(Pedido(prompt="a", nivel="fuerte"))
        assert r.desde_cache and len(usados) == 1


class TestRepartoDelTrabajo:
    def test_cada_paso_usa_el_nivel_que_dice_la_configuracion(self):
        from pathlib import Path

        from rugby_stats.config import cargar_config

        cfg = cargar_config(Path(__file__).resolve().parent.parent / "config.yaml")
        assert cfg.niveles["fuerte"][0] == "gemini-3.8-flash"
        assert all("lite" in m for m in cfg.niveles["liviano"])
        # Lo que es volumen, a los livianos (500 por día cada uno).
        for paso in ("gruesa", "relato", "cambios", "eventos"):
            assert cfg.nivel_de(paso) == "liviano", paso
        # Lo difícil y escaso, a los fuertes (20 por día cada uno).
        for paso in ("fichas", "contactos"):
            assert cfg.nivel_de(paso) == "fuerte", paso
        assert cfg.respaldo_de_niveles == {}  # por defecto frena, no degrada

    def test_el_pipeline_manda_cada_paso_a_su_nivel(self, db, partido_cargado):
        from pathlib import Path

        from rugby_stats.analisis import pipeline
        from rugby_stats.config import cargar_config
        from rugby_stats.gemini.cliente import ClienteSimulado

        cfg = cargar_config(Path(__file__).resolve().parent.parent / "config.yaml")
        vistos: dict[str, set[str]] = {}

        class Espia(ClienteSimulado):
            def pedir(self, p):
                vistos.setdefault(p.etiqueta.split()[0], set()).add(p.nivel)
                return super().pedir(p)

        pipeline.correr(db, Espia(), partido_cargado.id, cfg)
        assert vistos["gruesa"] == {"liviano"}
        assert vistos["eventos"] == {"liviano"}
        assert vistos["relato"] == {"liviano"}
        assert vistos["fichas"] == {"fuerte"}
        assert vistos["contacto"] == {"fuerte"}
