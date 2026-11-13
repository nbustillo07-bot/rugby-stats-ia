"""El cliente de Gemini: cola, reintentos, caché, y los prompts."""

from __future__ import annotations

import json

import pytest

from rugby_stats.gemini import prompts
from rugby_stats.gemini.cliente import (
    Cache,
    ClienteSimulado,
    ErrorGemini,
    Pedido,
    Respuesta,
    _es_429,
    _Ritmo,
    extraer_json,
)
from rugby_stats.gemini.video import (
    embed_youtube,
    id_youtube,
    url_youtube_con_tiempo,
    ventanas,
)
from rugby_stats.modelos import Equipo, Ficha
from tests.conftest import hacer_plantel


class TestExtraerJson:
    def test_json_limpio(self):
        assert extraer_json('{"a": 1}') == {"a": 1}

    def test_envuelto_en_bloque_de_codigo(self):
        assert extraer_json('```json\n{"a": 1}\n```') == {"a": 1}
        assert extraer_json('```\n[1, 2]\n```') == [1, 2]

    def test_con_texto_alrededor(self):
        """El modelo a veces agrega una frase antes del JSON."""
        texto = 'Acá va la respuesta:\n{"eventos": [{"t": 1}]}\nEspero que sirva.'
        assert extraer_json(texto) == {"eventos": [{"t": 1}]}

    def test_objetos_anidados(self):
        texto = 'blabla {"a": {"b": [1, {"c": 2}]}} fin'
        assert extraer_json(texto) == {"a": {"b": [1, {"c": 2}]}}

    def test_json_invalido_explota_con_mensaje_claro(self):
        with pytest.raises(ErrorGemini) as exc:
            extraer_json("no hay json acá")
        assert "no es JSON válido" in str(exc.value)


class TestRitmo:
    def test_el_primer_pedido_no_espera_y_los_siguientes_si(self):
        dormidas: list[float] = []
        r = _Ritmo(rpm=60)  # un pedido por segundo
        r.esperar(dormir=dormidas.append)
        assert dormidas == []  # el primero sale enseguida
        r.esperar(dormir=dormidas.append)
        r.esperar(dormir=dormidas.append)
        assert len(dormidas) == 2
        assert all(d == pytest.approx(1.0, abs=0.05) for d in dormidas)
        assert r.intervalo_s == pytest.approx(1.0)

    def test_ante_un_429_baja_el_ritmo_a_la_mitad(self):
        r = _Ritmo(rpm=8)
        r.bajar()
        assert r.rpm == 4
        r.bajar()
        assert r.rpm == 2

    def test_no_baja_de_un_minimo(self):
        r = _Ritmo(rpm=2, rpm_minimo=1)
        for _ in range(10):
            r.bajar()
        assert r.rpm == 1

    def test_recupera_pero_no_mas_que_el_original(self):
        r = _Ritmo(rpm=8)
        r.bajar()
        for _ in range(20):
            r.recuperar()
        assert r.rpm == 8


class TestDeteccionDeErrores:
    @pytest.mark.parametrize(
        "mensaje",
        [
            "429 Too Many Requests",
            "RESOURCE_EXHAUSTED",
            "You exceeded your current quota",
            "rate limit reached",
        ],
    )
    def test_reconoce_el_429(self, mensaje):
        assert _es_429(RuntimeError(mensaje))

    def test_no_confunde_otros_errores(self):
        assert not _es_429(RuntimeError("400 Bad Request"))
        assert not _es_429(ValueError("algo salió mal"))


class TestCache:
    def test_guarda_y_lee(self, tmp_path):
        c = Cache(tmp_path / "cache")
        c.guardar("clave1", Respuesta(texto='{"a":1}', datos={"a": 1}))
        leida = c.leer("clave1")
        assert leida is not None
        assert leida.datos == {"a": 1}
        assert leida.desde_cache is True

    def test_clave_desconocida_devuelve_none(self, tmp_path):
        assert Cache(tmp_path / "cache").leer("no-existe") is None

    def test_desactivada_no_guarda_nada(self, tmp_path):
        c = Cache(tmp_path / "cache", activa=False)
        c.guardar("x", Respuesta(texto="{}"))
        assert c.leer("x") is None

    def test_archivo_corrupto_no_explota(self, tmp_path):
        c = Cache(tmp_path / "cache")
        (tmp_path / "cache" / "mala.json").write_text("{ roto", encoding="utf-8")
        assert c.leer("mala") is None


class TestClaveDeCache:
    def test_pedidos_iguales_dan_la_misma_clave(self):
        a = Pedido(prompt="hola", video_uri="u", inicio_s=0, fin_s=10, fps=2)
        b = Pedido(prompt="hola", video_uri="u", inicio_s=0, fin_s=10, fps=2)
        assert a.clave_cache("m") == b.clave_cache("m")

    def test_distinto_tramo_da_distinta_clave(self):
        a = Pedido(prompt="hola", video_uri="u", inicio_s=0, fin_s=10)
        b = Pedido(prompt="hola", video_uri="u", inicio_s=10, fin_s=20)
        assert a.clave_cache("m") != b.clave_cache("m")

    def test_distintos_cuadros_por_segundo_dan_distinta_clave(self):
        """Mirar lo mismo a 2 y a 10 fps son dos pedidos diferentes."""
        a = Pedido(prompt="p", video_uri="u", inicio_s=0, fin_s=3, fps=2)
        b = Pedido(prompt="p", video_uri="u", inicio_s=0, fin_s=3, fps=10)
        assert a.clave_cache("m") != b.clave_cache("m")

    def test_distinto_modelo_da_distinta_clave(self):
        p = Pedido(prompt="p")
        assert p.clave_cache("modelo-a") != p.clave_cache("modelo-b")

    def test_la_etiqueta_no_cambia_la_clave(self):
        """La etiqueta es para los logs, no es parte del pedido."""
        a = Pedido(prompt="p", etiqueta="una cosa")
        b = Pedido(prompt="p", etiqueta="otra cosa")
        assert a.clave_cache("m") == b.clave_cache("m")


class TestClienteSimulado:
    def test_marca_todo_como_simulado(self):
        c = ClienteSimulado()
        r = c.pedir(Pedido(prompt="x", etiqueta="eventos 0-60", inicio_s=0, fin_s=60))
        assert r.simulada is True
        assert r.modelo == "simulado"

    def test_devuelve_la_forma_esperada_por_etiqueta(self):
        c = ClienteSimulado()
        gruesa = c.pedir(Pedido(prompt="x", etiqueta="gruesa", inicio_s=0, fin_s=120))
        assert "tramos" in gruesa.json()
        eventos = c.pedir(Pedido(prompt="x", etiqueta="eventos", inicio_s=0, fin_s=120))
        assert "eventos" in eventos.json()
        contacto = c.pedir(Pedido(prompt="x", etiqueta="contacto", inicio_s=0, fin_s=3))
        assert "tackleadores" in contacto.json()
        fichas = c.pedir(Pedido(prompt="x", etiqueta="fichas", inicio_s=0, fin_s=60))
        assert "lecturas" in fichas.json()

    def test_es_repetible(self):
        """Misma semilla, misma salida: los tests no se vuelven flaky."""
        a = ClienteSimulado(semilla=1).pedir(
            Pedido(prompt="x", etiqueta="eventos", inicio_s=0, fin_s=120)
        )
        b = ClienteSimulado(semilla=1).pedir(
            Pedido(prompt="x", etiqueta="eventos", inicio_s=0, fin_s=120)
        )
        assert a.texto == b.texto

    def test_se_le_pueden_fijar_respuestas(self):
        c = ClienteSimulado()
        c.respuestas["eventos"] = {"eventos": [{"t_segundos": 5, "accion": "Try"}]}
        r = c.pedir(Pedido(prompt="x", etiqueta="eventos 0-60"))
        assert r.json()["eventos"][0]["accion"] == "Try"

    def test_cuenta_los_pedidos_y_el_video_mirado(self):
        c = ClienteSimulado()
        c.pedir(Pedido(prompt="x", etiqueta="gruesa", inicio_s=0, fin_s=60))
        c.pedir(Pedido(prompt="x", etiqueta="gruesa", inicio_s=60, fin_s=120))
        assert c.contadores.pedidos == 2
        assert c.contadores.segundos_video == 120


class TestVentanas:
    def test_parte_un_tramo(self):
        assert ventanas(0, 300, 120, 0) == [(0, 120), (120, 240), (240, 300)]

    def test_con_solape(self):
        """El solape evita cortar una jugada al medio."""
        v = ventanas(0, 300, 120, 10)
        assert v[0] == (0, 120)
        assert v[1][0] == 110  # arranca 10 s antes del corte
        assert all(b > a for a, b in v)

    def test_tramo_mas_corto_que_la_ventana(self):
        assert ventanas(0, 50, 120, 5) == [(0, 50)]

    def test_tramo_vacio_o_invertido(self):
        assert ventanas(100, 100, 60, 0) == []
        assert ventanas(100, 50, 60, 0) == []

    def test_el_solape_no_puede_ser_mayor_que_media_ventana(self):
        """Si no, las ventanas no avanzarían nunca."""
        v = ventanas(0, 600, 100, 500)
        assert len(v) < 50
        assert v[-1][1] == 600

    def test_cubre_todo_el_tramo(self):
        v = ventanas(10, 500, 77, 7)
        assert v[0][0] == 10
        assert v[-1][1] == 500


class TestYouTube:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.youtube.com/watch?v=VsRZotfwL6A",
            "https://youtu.be/VsRZotfwL6A",
            "https://www.youtube.com/embed/VsRZotfwL6A",
            "https://www.youtube.com/live/VsRZotfwL6A",
            "https://www.youtube.com/watch?v=VsRZotfwL6A&t=120s",
        ],
    )
    def test_saca_el_id(self, url):
        assert id_youtube(url) == "VsRZotfwL6A"

    def test_no_es_youtube(self):
        assert id_youtube("https://vimeo.com/123") is None
        assert id_youtube("C:/videos/partido.mp4") is None
        assert id_youtube("") is None

    def test_url_con_tiempo(self):
        u = url_youtube_con_tiempo("https://youtu.be/VsRZotfwL6A", 1368.4)
        assert "t=1368s" in u

    def test_embed_con_inicio_y_fin(self):
        u = embed_youtube("https://youtu.be/VsRZotfwL6A", 100, 106)
        assert "start=100" in u and "end=106" in u


class TestPrompts:
    """Lo que importa de los prompts: que no nombren ningún club fijo y que
    pidan observaciones en lugar de juicios."""

    def test_ningun_club_hardcodeado(self):
        import inspect

        fuente = inspect.getsource(prompts)
        for prohibido in ("Molinos", "Lanús", "Lanus"):
            assert prohibido not in fuente, (
                f"El prompt menciona {prohibido}: tiene que servir para "
                "cualquier equipo."
            )

    def test_describir_equipos_usa_propio_y_rival(self):
        texto = prompts.describir_equipos(
            Equipo(nombre="Club A", kit="rayas verdes", numero_legible="baja"),
            Equipo(nombre="Club B", kit="azul liso", numero_legible="alta"),
        )
        assert "Club A" in texto and "Club B" in texto
        assert "rayas verdes" in texto
        assert "casi no se lee" in texto      # legibilidad baja
        assert "se lee bien" in texto          # legibilidad alta
        assert '"propio"' in texto

    def test_el_prompt_de_contacto_pide_el_avance_no_el_juicio(self):
        """El criterio de positivo/negativo lo aplica el código, no el modelo."""
        texto = prompts.prompt_contacto(
            Equipo(nombre="A"), Equipo(nombre="B"),
            hacer_plantel("A"), hacer_plantel("B"), {}, 10, 14,
        )
        assert "avance_portador" in texto
        assert "no digas si el tackle fue bueno o malo" in texto.lower()
        assert "positivo" not in texto.lower() or "no digas" in texto.lower()

    def test_el_esquema_de_contacto_no_tiene_positivo_ni_negativo(self):
        opciones = prompts.ESQUEMA_CONTACTO["properties"]["avance_portador"]["enum"]
        assert set(opciones) == {"avanza", "se_detiene", "retrocede", "no_se_ve"}

    def test_el_contacto_separa_confianza_de_identificacion(self):
        """Se puede ver clarísimo que hubo un tackle y no saber quién lo hizo."""
        props = prompts.ESQUEMA_CONTACTO["properties"]
        assert "confianza" in props and "confianza_identificacion" in props

    def test_el_plantel_limita_los_numeros_validos(self):
        texto = prompts.describir_plantel(hacer_plantel("A", 23))
        assert "únicos números válidos" in texto
        assert "Titulares:" in texto and "Suplentes" in texto

    def test_sin_plantel_pide_dejar_los_numeros_en_null(self):
        texto = prompts.describir_plantel([])
        assert "null" in texto

    def test_las_fichas_entran_al_prompt_de_eventos(self):
        fichas = {
            4: Ficha(
                jugador_numero=4, equipo="A", t_segundos=100,
                rasgos={"casco": "sí, oscuro", "color_botines": "rojos"},
                confianza=0.9,
            )
        }
        texto = prompts.prompt_eventos(
            Equipo(nombre="A"), Equipo(nombre="B"),
            hacer_plantel("A"), hacer_plantel("B"), fichas, 0, 60,
        )
        assert "casco: sí, oscuro" in texto
        assert "Descarte" in texto  # son 15 en cancha

    def test_el_relato_se_marca_como_no_confiable(self):
        texto = prompts.prompt_eventos(
            Equipo(nombre="A"), Equipo(nombre="B"),
            hacer_plantel("A"), hacer_plantel("B"), {}, 0, 60,
            relato="dice algo el relator",
        )
        assert "gana el video" in texto

    def test_el_prompt_de_relato_pasa_el_vocabulario(self):
        """Los subtítulos automáticos erran los apellidos: hay que darle la lista."""
        jugadores = hacer_plantel("A", 3)
        jugadores[0].apodo = "Juancho"
        texto = prompts.prompt_relato(jugadores, [], 0, 600)
        assert "Juancho" in texto
        assert "vocabulario" in texto.lower()

    def test_todas_las_acciones_tienen_resultados_declarados(self):
        from rugby_stats.modelos import ACCIONES, RESULTADOS

        for accion in ACCIONES:
            assert accion in RESULTADOS, f"Falta declarar resultados de {accion}"
            assert RESULTADOS[accion], f"{accion} no tiene resultados posibles"

    def test_el_vocabulario_del_prompt_cubre_todas_las_acciones(self):
        from rugby_stats.modelos import ACCIONES

        texto = prompts._vocabulario_acciones()
        for accion in ACCIONES:
            assert accion in texto

    def test_los_esquemas_son_json_serializables(self):
        """Van tal cual al SDK: si no serializan, el pedido falla."""
        for nombre in dir(prompts):
            if nombre.startswith("ESQUEMA_"):
                json.dumps(getattr(prompts, nombre))

    def test_todos_los_prompts_piden_poder_decir_que_no_se_sabe(self):
        assert "null" in prompts.ENCABEZADO or "no sé" in prompts.ENCABEZADO
        assert "Un dato inventado" in prompts.ENCABEZADO
