"""El diagnóstico contra la API real, probado contra un servidor falso.

El diagnóstico es lo primero que se corre con la clave nueva, así que si el
propio diagnóstico tiene un bug, no sirve para nada. Acá se lo corre entero
con el SDK de verdad y un transporte HTTP falso.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

httpx = pytest.importorskip("httpx2", reason="el SDK de Gemini usa httpx2")

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
CLAVE = "AQ.clave-secreta-de-prueba-1234567890"


def _cargar_modulo():
    spec = importlib.util.spec_from_file_location(
        "diagnostico_bajo_prueba", RAIZ / "scripts" / "diagnostico.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _respuesta(datos: dict, tokens: int = 2000):
    return httpx.Response(
        200,
        json={
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": json.dumps(datos)}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": tokens, "candidatesTokenCount": 50},
        },
    )


def _servidor(req):
    """Contesta según qué esquema pide cada pedido."""
    cuerpo = json.loads(req.content)
    props = (
        cuerpo.get("generationConfig", {}).get("responseSchema", {}).get("properties", {})
    )
    if "ok" in props:
        return _respuesta({"ok": True}, tokens=20)
    if "hay_cartel" in props:
        return _respuesta(
            {"hay_cartel": True, "reloj": "08:30", "marcador": "0-0",
             "hay_juego": True, "que_se_ve": "un scrum"}
        )
    if "tramos" in props:
        return _respuesta({"tramos": [{"inicio_s": 612, "fin_s": 700,
                                       "tipo": "juego", "confianza": 0.9}]})
    if "fragmentos" in props:
        return _respuesta({"fragmentos": []})
    if "lecturas" in props:
        return _respuesta({"lecturas": []})
    if "eventos" in props:
        return _respuesta({"eventos": [{"t_segundos": 630.0, "accion": "Tackle",
                                        "equipo": "propio", "confianza": 0.6}]})
    if "tackleadores" in props:
        return _respuesta({"tackleadores": [7], "avance_portador": "se_detiene",
                           "confianza": 0.8})
    if "cambios" in props:
        return _respuesta({"cambios": []})
    return _respuesta({})


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    """Una copia de config.yaml en una carpeta temporal, con su .env."""
    shutil.copy(RAIZ / "config.yaml", tmp_path / "config.yaml")
    texto = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    texto = texto.replace("    fuerte: 4\n", "    fuerte: 1000000\n").replace(
        "    liviano: 8\n", "    liviano: 1000000\n"
    )
    assert "1000000" in texto, "cambió el formato de config.yaml"
    # Estos tests son del camino de Gemini.
    texto = texto.replace("proveedor: claude", "proveedor: gemini")
    assert "proveedor: gemini" in texto
    (tmp_path / "config.yaml").write_text(texto, encoding="utf-8")
    monkeypatch.setattr("time.sleep", lambda s: None)
    (tmp_path / ".env").write_text(f"GEMINI_API_KEY={CLAVE}\n", encoding="utf-8")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("RUGBY_STATS_DB", str(tmp_path / "d.db"))
    return tmp_path


def _correr(entorno, monkeypatch, servidor, extra=()):
    mod = _cargar_modulo()
    pedidos: list[dict] = []

    def handler(req):
        pedidos.append(json.loads(req.content))
        return servidor(req)

    original = mod.ClienteGemini

    def fabrica(**kwargs):
        c = original(**kwargs)
        c.cliente = genai.Client(
            api_key=kwargs["api_key"],
            http_options=types.HttpOptions(
                client_args={"transport": httpx.MockTransport(handler)},
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
        return c

    monkeypatch.setattr(mod, "ClienteGemini", fabrica)
    monkeypatch.setattr(
        sys, "argv",
        ["diagnostico.py", "--config", str(entorno / "config.yaml"),
         "--video", "https://www.youtube.com/watch?v=VsRZotfwL6A", *extra],
    )
    codigo = mod.main()
    carpeta = entorno / "salidas" / "diagnostico"
    informes = sorted(carpeta.glob("*.json")) if carpeta.exists() else []
    informe = json.loads(informes[-1].read_text(encoding="utf-8")) if informes else None
    return codigo, informe, pedidos, carpeta


def test_corre_entero_y_deja_el_informe(entorno, monkeypatch):
    codigo, informe, pedidos, carpeta = _correr(entorno, monkeypatch, _servidor)
    assert codigo == 0
    assert informe["pasos_mal"] == 0
    nombres = [p["paso"] for p in informe["pasos"]]
    for esperado in ("texto simple", "pasada gruesa", "relato", "fichas",
                     "eventos", "contacto", "cambios"):
        assert any(esperado in n for n in nombres), f"falta el paso {esperado}"
    assert list(carpeta.glob("*.txt"))


def test_la_clave_no_queda_en_ningun_archivo(entorno, monkeypatch):
    """El informe se puede compartir para pedir ayuda: no puede llevar la clave."""

    def con_clave_en_el_error(req):
        return httpx.Response(
            400,
            json={"error": {"code": 400, "status": "INVALID_ARGUMENT",
                            "message": f"API key {CLAVE} not valid"}},
        )

    _, _, _, carpeta = _correr(entorno, monkeypatch, con_clave_en_el_error)
    for archivo in carpeta.iterdir():
        assert CLAVE not in archivo.read_text(encoding="utf-8"), archivo.name


def test_si_la_clave_no_anda_corta_y_lo_dice(entorno, monkeypatch):
    def rechaza(req):
        return httpx.Response(
            400,
            json={"error": {"code": 400, "status": "INVALID_ARGUMENT",
                            "message": "API key not valid"}},
        )

    codigo, informe, pedidos, _ = _correr(entorno, monkeypatch, rechaza)
    assert codigo != 0
    assert informe["cortado_antes_de_terminar"] is True
    assert len(pedidos) == 1  # no siguió gastando pedidos de video


def test_usa_el_reloj_leido_para_ubicarse(entorno, monkeypatch):
    _, informe, _, _ = _correr(entorno, monkeypatch, _servidor)
    # Leyó 08:30 al final de la ventana que arranca a los 900 s (912 s).
    assert informe["desfase_reloj_s"] == pytest.approx(510 - 912)


def test_el_contacto_se_prueba_sobre_un_evento_que_salio(entorno, monkeypatch):
    _, informe, _, _ = _correr(entorno, monkeypatch, _servidor)
    contacto = next(p for p in informe["pasos"] if "contacto" in p["paso"])
    assert contacto["inicio_s"] == pytest.approx(630.0 - 1.5)
    assert contacto["fps"] == 10.0


def test_un_paso_que_falla_no_frena_los_demas(entorno, monkeypatch):
    def falla_en_fichas(req):
        cuerpo = json.loads(req.content)
        props = cuerpo.get("generationConfig", {}).get("responseSchema", {}).get(
            "properties", {}
        )
        if "lecturas" in props:
            return httpx.Response(
                400,
                json={"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                "message": "Invalid JSON schema"}},
            )
        return _servidor(req)

    codigo, informe, _, _ = _correr(entorno, monkeypatch, falla_en_fichas)
    assert codigo == 2
    assert informe["pasos_mal"] == 1
    malo = next(p for p in informe["pasos"] if not p["ok"])
    assert "fichas" in malo["paso"]
    assert "Invalid JSON schema" in malo["error"]
    assert any("cambios" in p["paso"] and p["ok"] for p in informe["pasos"])


def test_avisa_si_los_tokens_por_segundo_son_disparatados(entorno, monkeypatch):
    """Es la señal de que el audio del video entero viaja en cada pedido."""

    def caro(req):
        r = _servidor(req)
        datos = json.loads(r.content)
        datos["usageMetadata"]["promptTokenCount"] = 400_000
        return httpx.Response(200, json=datos)

    _, _, _, carpeta = _correr(entorno, monkeypatch, caro)
    texto = sorted(carpeta.glob("*.txt"))[-1].read_text(encoding="utf-8")
    assert "audio del video entero" in texto


def test_sin_clave_explica_que_hacer(tmp_path, monkeypatch, capsys):
    shutil.copy(RAIZ / "config.yaml", tmp_path / "config.yaml")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("RUGBY_STATS_DB", str(tmp_path / "d.db"))
    mod = _cargar_modulo()
    monkeypatch.setattr(
        sys, "argv", ["diagnostico.py", "--config", str(tmp_path / "config.yaml")]
    )
    assert mod.main() == 1
    assert "2_poner_clave.bat" in capsys.readouterr().out


def test_si_el_modelo_principal_esta_saturado_sigue_con_otro(entorno, monkeypatch):
    """Lo que pasó en la primera corrida real: el modelo gratis saturado."""

    def saturado_el_principal(req):
        if "gemini-3.8-flash" in str(req.url):
            return httpx.Response(
                503,
                json={"error": {"code": 503, "status": "UNAVAILABLE",
                                "message": "The model is overloaded. Please try again later."}},
            )
        return _servidor(req)

    monkeypatch.setattr("time.sleep", lambda s: None)
    codigo, informe, _, _ = _correr(entorno, monkeypatch, saturado_el_principal)
    assert informe["modelo_elegido"] == "gemini-3.6-flash"
    assert informe["saturaciones"] > 0
    primero = informe["pasos"][0]
    assert not primero["ok"] and "saturad" in primero["error"]
    assert any("overloaded" in e for e in primero["errores_de_google"])
    # Y todo lo demás corrió con el modelo que sí respondía.
    assert all(p["ok"] for p in informe["pasos"][1:])


def test_el_informe_se_guarda_despues_de_cada_paso(entorno, monkeypatch):
    """Si se cierra la ventana a la mitad, lo hecho tiene que quedar."""
    vistos = []

    def espia(req):
        carpeta = entorno / "salidas" / "diagnostico"
        vistos.append(len(list(carpeta.glob("*.json"))) if carpeta.exists() else 0)
        return _servidor(req)

    _correr(entorno, monkeypatch, espia)
    assert vistos[0] == 0      # antes del primer pedido no hay nada
    assert vistos[1] == 1      # al segundo ya está guardado el primero
    assert max(vistos) == 1    # siempre el mismo archivo, no uno por paso


def test_el_env_con_marca_invisible_y_export(tmp_path, monkeypatch):
    """Notepad a veces guarda una marca al principio; y hay quien escribe export."""
    from rugby_stats import config

    monkeypatch.delenv("CLAVE_DE_PRUEBA_A", raising=False)
    monkeypatch.delenv("CLAVE_DE_PRUEBA_B", raising=False)
    env = tmp_path / ".env"
    env.write_bytes("﻿CLAVE_DE_PRUEBA_A= abc \nexport CLAVE_DE_PRUEBA_B=\"xyz\"\n".encode("utf-8"))
    config._cargar_env(env)
    import os

    assert os.environ["CLAVE_DE_PRUEBA_A"] == "abc"
    assert os.environ["CLAVE_DE_PRUEBA_B"] == "xyz"
    env.write_text("CLAVE_DE_PRUEBA_A=nueva\nCLAVE_DE_PRUEBA_B=\n", encoding="utf-8")
    config._cargar_env(env, pisar=True)
    assert os.environ["CLAVE_DE_PRUEBA_A"] == "nueva"
    assert os.environ["CLAVE_DE_PRUEBA_B"] == "xyz"  # vacío no pisa
