"""Respuestas del modelo que no vienen exactamente como se pidieron.

Nada de esto se pudo ver todavía contra Gemini de verdad, así que el sistema
tiene que aguantar las variantes más probables sin perder eventos en silencio.
"""

from __future__ import annotations

import pytest

from rugby_stats.analisis import pasadas, pipeline, reglas
from rugby_stats.gemini.cliente import ClienteSimulado


class TestTiemposRelativos:
    """El modelo puede contar desde el inicio del recorte en vez del video."""

    def test_detecta_tiempos_relativos(self):
        corrimiento, relativos = reglas.corregir_tiempos([5, 20, 48], 1368, 1488)
        assert relativos and corrimiento == 1368

    def test_no_toca_tiempos_absolutos(self):
        corrimiento, relativos = reglas.corregir_tiempos([1370, 1400, 1480], 1368, 1488)
        assert not relativos and corrimiento == 0

    def test_si_la_ventana_arranca_en_cero_no_hay_nada_que_decidir(self):
        assert reglas.corregir_tiempos([5, 20], 0, 120) == (0.0, False)

    def test_sin_tiempos_no_hace_nada(self):
        assert reglas.corregir_tiempos([], 1368, 1488) == (0.0, False)
        assert reglas.corregir_tiempos([None, "x"], 1368, 1488) == (0.0, False)

    def test_tiempos_disparatados_no_se_corrigen(self):
        """Ni en la ventana ni relativos: mejor no inventar."""
        assert reglas.corregir_tiempos([9000, 9500], 1368, 1488) == (0.0, False)

    def test_los_eventos_relativos_no_se_pierden(self, db, partido_cargado, cfg):
        """Antes caían todos fuera de la ventana y se descartaban."""
        partido_cargado.analizar_desde_s = 1368.0
        partido_cargado.analizar_hasta_s = 1488.0
        partido_cargado.video.duracion_s = 6000.0
        db.guardar_partido(partido_cargado)
        cliente = ClienteSimulado()
        cliente.respuestas["eventos"] = {
            "eventos": [
                {"t_segundos": 12, "accion": "Pase", "resultado": "bueno",
                 "equipo": "propio", "numero": 9, "confianza": 0.9},
                {"t_segundos": 47, "accion": "Patada", "resultado": "al touch",
                 "equipo": "propio", "numero": 10, "confianza": 0.9},
            ]
        }
        pipeline.correr(db, cliente, partido_cargado.id, cfg, pasos=["eventos"])
        tiempos = sorted(e.t_segundos for e in db.eventos(partido_cargado.id))
        assert tiempos == [1380.0, 1415.0]


class TestEquipo:
    def test_propio_y_rival(self):
        assert reglas.nombre_equipo("propio", "Los Tilos", "San Martín") == "Los Tilos"
        assert reglas.nombre_equipo("RIVAL", "Los Tilos", "San Martín") == "San Martín"

    def test_acepta_el_nombre_del_club_con_otra_escritura(self):
        assert reglas.nombre_equipo("los tilos", "Los Tilos", "San Martín") == "Los Tilos"
        assert reglas.nombre_equipo("San Martin", "Los Tilos", "San Martín") == "San Martín"

    def test_lo_que_no_reconoce_queda_vacio(self):
        """Un equipo mal escrito no coincidiría después con ningún plantel."""
        assert reglas.nombre_equipo("los verdes", "Los Tilos", "San Martín") == ""
        assert reglas.nombre_equipo("", "Los Tilos", "San Martín") == ""
        assert reglas.nombre_equipo(None, "Los Tilos", "San Martín") == ""


class TestResultado:
    @pytest.mark.parametrize(
        "accion,crudo,esperado",
        [
            ("Tackle", "Positivo", "positivo"),
            ("Tackle", " NEGATIVO ", "negativo"),
            ("Patada", "Al Touch", "al touch"),
            ("Pelota perdida", "knock on", "knock-on"),
            ("Pelota perdida", "Knock-On", "knock-on"),
            ("Conversión", "Adentro", "adentro"),
            ("Penal cometido", "obstruccion", "obstrucción"),
        ],
    )
    def test_variantes_de_escritura(self, accion, crudo, esperado):
        assert reglas.normalizar_resultado(accion, crudo) == esperado

    def test_lo_desconocido_pasa_tal_cual_y_lo_frena_la_validacion(self, cfg):
        from rugby_stats.modelos import Evento

        r = reglas.normalizar_resultado("Tackle", "espectacular")
        assert r == "espectacular"
        e = Evento(t_segundos=1, accion="Tackle", resultado=r, jugador_numero=7)
        assert not reglas.validar_evento(e, {7}, cfg)[0]

    def test_vacio(self):
        assert reglas.normalizar_resultado("Tackle", None) == ""
        assert reglas.normalizar_resultado("Tackle", "  ") == ""


class TestConfianza:
    @pytest.mark.parametrize(
        "crudo,esperado",
        [(0.8, 0.8), (80, 0.8), ("0,8", 0.8), ("85%", 0.85), (None, 0.0),
         ("alta", 0.0), (-1, 0.0), (1, 1.0), (100, 1.0), (250, 1.0)],
    )
    def test_siempre_queda_entre_cero_y_uno(self, crudo, esperado):
        assert reglas.normalizar_confianza(crudo) == pytest.approx(esperado)


class TestNumero:
    @pytest.mark.parametrize(
        "crudo,esperado",
        [(9, 9), ("9", 9), (" 9 ", 9), (9.0, 9), ("9.0", 9), ("#9", 9),
         (9.5, None), ("nueve", None), (0, None), (100, None), (None, None),
         (True, None), ("", None)],
    )
    def test_numero_de_camiseta(self, crudo, esperado):
        assert reglas.limpiar_numero(crudo) == esperado


class TestRespuestasMalFormadas:
    def _correr(self, db, partido_cargado, cfg, paso, respuesta, hasta=100.0):
        # Un tramo que entra en una sola ventana: la respuesta fija del
        # cliente simulado se repetiría igual en cada ventana.
        partido_cargado.analizar_hasta_s = hasta
        db.guardar_partido(partido_cargado)
        cliente = ClienteSimulado()
        cliente.respuestas[paso] = respuesta
        return pipeline.correr(db, cliente, partido_cargado.id, cfg, pasos=[paso])

    def test_la_lista_suelta_en_vez_del_objeto(self, db, partido_cargado, cfg):
        """Pide {"eventos": [...]} y a veces vuelve directamente la lista."""
        self._correr(db, partido_cargado, cfg, "eventos", [
            {"t_segundos": 10, "accion": "Pase", "resultado": "bueno",
             "equipo": "propio", "numero": 9, "confianza": 0.9},
        ])
        assert len(db.eventos(partido_cargado.id)) == 1

    @pytest.mark.parametrize("basura", [None, "texto", 5, {}, {"eventos": None},
                                        {"eventos": "ninguno"}, {"eventos": [None, 3, "x"]}])
    def test_basura_no_tira_abajo_el_analisis(self, db, partido_cargado, cfg, basura):
        res = self._correr(db, partido_cargado, cfg, "eventos", basura)
        assert "eventos" in res.pasos_corridos
        assert db.eventos(partido_cargado.id) == []

    def test_un_evento_roto_no_se_lleva_puestos_a_los_demas(
        self, db, partido_cargado, cfg
    ):
        self._correr(db, partido_cargado, cfg, "eventos", {"eventos": [
            {"accion": "Pase"},                          # sin tiempo
            {"t_segundos": "ayer", "accion": "Pase"},    # tiempo que no es número
            {"t_segundos": 20, "accion": "Pase", "resultado": "Bueno",
             "equipo": "Propio", "numero": "9", "confianza": 90},
        ]})
        eventos = db.eventos(partido_cargado.id)
        assert len(eventos) == 1
        e = eventos[0]
        assert (e.resultado, e.equipo, e.jugador_numero) == ("bueno", "Club A", 9)
        assert e.confianza == pytest.approx(0.9)
        assert not e.pendiente

    def test_tramos_que_se_pasan_de_la_ventana_se_recortan(
        self, db, partido_cargado, cfg
    ):
        self._correr(db, partido_cargado, cfg, "gruesa", {"tramos": [
            {"inicio_s": -50, "fin_s": 100, "tipo": "juego", "confianza": 0.9},
            {"inicio_s": 100, "fin_s": 99999, "tipo": "muerto", "confianza": 0.9,
             "marcador_local": "7", "marcador_visitante": None},
        ]}, hasta=300.0)
        tramos = db.tramos(partido_cargado.id)
        assert tramos[0].inicio_s == 0
        assert tramos[-1].fin_s == 300  # el tope del tramo a analizar
        assert tramos[-1].marcador_propio == 7

    def test_fichas_con_rasgos_raros(self, db, partido_cargado, cfg):
        self._correr(db, partido_cargado, cfg, "fichas", {"lecturas": [
            {"numero": "4", "t_segundos": 30, "confianza": 85,
             "rasgos": {"casco": "no", "pelo": None, "vendas": "null",
                        "color_botines": " rojos "}},
            {"numero": 5, "t_segundos": 31, "confianza": 0.7, "rasgos": "ninguno"},
            {"numero": 77, "t_segundos": 32, "confianza": 0.9, "rasgos": {}},
        ]})
        fichas = {f.jugador_numero: f for f in db.fichas(partido_cargado.id, "Club A")}
        assert set(fichas) == {4, 5}                    # el 77 no está en el plantel
        assert fichas[4].rasgos == {"casco": "no", "color_botines": "rojos"}
        assert fichas[4].confianza == pytest.approx(0.85)
        assert fichas[5].rasgos == {}

    def test_contacto_con_campos_en_formas_inesperadas(self, partido, cfg):
        eventos = pasadas._contacto_a_eventos(
            {"portador": "10", "portador_equipo": "Rival",
             "tackleadores": 6,                      # un número suelto, no una lista
             "avance_portador": "Se detiene",        # con mayúscula y espacio
             "confianza": 90, "confianza_identificacion": "0,8"},
            100.0, partido, cfg, {6}, {10},
        )
        tackle = next(e for e in eventos if e.accion == "Tackle")
        assert tackle.jugador_numero == 6
        assert tackle.resultado == "positivo"
        assert tackle.confianza == pytest.approx(0.8)
        assert tackle.equipo == "Club A"

    def test_un_tackleador_repetido_cuenta_una_vez(self, partido, cfg):
        eventos = pasadas._contacto_a_eventos(
            {"portador": 10, "portador_equipo": "rival", "tackleadores": [6, 6, "6"],
             "avance_portador": "se_detiene", "confianza": 0.9},
            100.0, partido, cfg, {6}, {10},
        )
        assert len([e for e in eventos if e.accion == "Tackle"]) == 1
