"""Las reglas del sistema: lo que decide qué significa lo que vio el modelo."""

from __future__ import annotations

import pytest

from rugby_stats.analisis import reglas
from rugby_stats.config import Config
from rugby_stats.modelos import Evento, Jugador
from tests.conftest import CONFIG_BASE


def cfg_con(tackle_positivo: str, tolerancia_cm: int = 50) -> Config:
    datos = {
        **CONFIG_BASE,
        "reglas": {
            **CONFIG_BASE["reglas"],
            "tackle_positivo": tackle_positivo,
            "tolerancia_avance_cm": tolerancia_cm,
        },
    }
    return Config(datos)


class TestClasificarTackle:
    """El criterio por defecto: positivo si el portador NO avanza."""

    def test_portador_detenido_es_positivo(self):
        c = cfg_con("portador_detenido")
        assert reglas.clasificar_tackle("se_detiene", c) == "positivo"

    def test_portador_que_retrocede_es_positivo(self):
        c = cfg_con("portador_detenido")
        assert reglas.clasificar_tackle("retrocede", c) == "positivo"

    def test_portador_que_avanza_es_negativo(self):
        c = cfg_con("portador_detenido")
        assert reglas.clasificar_tackle("avanza", c) == "negativo"

    def test_criterio_invertido(self):
        """Quien quiera el criterio opuesto lo cambia en config.yaml."""
        c = cfg_con("portador_avanza")
        assert reglas.clasificar_tackle("avanza", c) == "positivo"
        assert reglas.clasificar_tackle("se_detiene", c) == "negativo"

    def test_sin_ver_el_final_no_clasifica(self):
        """Sin clasificar no vale: devuelve vacío y el evento va a la cola."""
        c = cfg_con("portador_detenido")
        assert reglas.clasificar_tackle("no_se_ve", c) == ""
        assert reglas.clasificar_tackle("", c) == ""
        assert reglas.clasificar_tackle("cualquier_cosa", c) == ""

    @pytest.mark.parametrize(
        "metros,esperado",
        [
            (3.0, "negativo"),    # avanzó de verdad
            (0.2, "neutro"),      # dentro de la tolerancia
            (-0.1, "neutro"),     # dentro de la tolerancia
            (-2.0, "positivo"),   # lo hicieron retroceder
        ],
    )
    def test_los_metros_medidos_mandan_sobre_la_etiqueta(self, metros, esperado):
        """Un 'avanza' de 20 cm no es un avance: la tolerancia decide."""
        c = cfg_con("portador_detenido", tolerancia_cm=50)
        assert reglas.clasificar_tackle("avanza", c, metros) == esperado

    def test_descripcion_del_criterio_dice_cual_es(self):
        texto = reglas.descripcion_criterio(cfg_con("portador_detenido"))
        assert "NO avanza" in texto
        assert "50 cm" in texto
        otro = reglas.descripcion_criterio(cfg_con("portador_avanza"))
        assert "SÍ avanza" in otro


class TestValidarEvento:
    """'Sin clasificar no vale': un evento incompleto no se exporta."""

    def test_evento_completo_sirve(self, cfg):
        e = Evento(
            t_segundos=10, accion="Tackle", resultado="positivo",
            equipo="Club A", jugador_numero=7, confianza=0.9,
        )
        sirve, motivo = reglas.validar_evento(e, {7}, cfg)
        assert sirve and motivo == ""

    def test_sin_jugador_no_sirve(self, cfg):
        e = Evento(t_segundos=10, accion="Tackle", resultado="positivo", confianza=0.9)
        sirve, motivo = reglas.validar_evento(e, {7}, cfg)
        assert not sirve
        assert "sin jugador" in motivo

    def test_sin_resultado_no_sirve(self, cfg):
        e = Evento(t_segundos=10, accion="Tackle", jugador_numero=7, confianza=0.9)
        sirve, motivo = reglas.validar_evento(e, {7}, cfg)
        assert not sirve
        assert "sin resultado" in motivo

    def test_numero_que_no_esta_en_el_plantel_no_sirve(self, cfg):
        """El modelo no puede inventar un número: el plantel es la verdad."""
        e = Evento(
            t_segundos=10, accion="Tackle", resultado="positivo",
            equipo="Club A", jugador_numero=47, confianza=0.95,
        )
        sirve, motivo = reglas.validar_evento(e, {1, 2, 3}, cfg)
        assert not sirve
        assert "47" in motivo

    def test_resultado_que_no_corresponde_a_la_accion_no_sirve(self, cfg):
        e = Evento(
            t_segundos=10, accion="Tackle", resultado="knock-on",
            equipo="Club A", jugador_numero=7, confianza=0.9,
        )
        sirve, motivo = reglas.validar_evento(e, {7}, cfg)
        assert not sirve
        assert "no válido" in motivo

    def test_confianza_baja_sirve_pero_pide_revision(self, cfg):
        e = Evento(
            t_segundos=10, accion="Tackle", resultado="positivo",
            equipo="Club A", jugador_numero=7, confianza=0.4,
        )
        sirve, _ = reglas.validar_evento(e, {7}, cfg)
        assert sirve
        assert reglas.necesita_revision(e, cfg)

    def test_evento_ya_revisado_no_vuelve_a_la_cola(self, cfg):
        e = Evento(
            t_segundos=10, accion="Tackle", resultado="positivo",
            jugador_numero=7, confianza=0.1, revisado=True,
        )
        assert not reglas.necesita_revision(e, cfg)


class TestPuntos:
    def test_try_y_conversion(self, cfg):
        assert reglas.puntos_de(Evento(t_segundos=1, accion="Try"), cfg) == 5
        conv = Evento(t_segundos=1, accion="Conversión", resultado="adentro")
        assert reglas.puntos_de(conv, cfg) == 2

    def test_pateo_afuera_no_suma(self, cfg):
        afuera = Evento(t_segundos=1, accion="Conversión", resultado="afuera")
        assert reglas.puntos_de(afuera, cfg) == 0
        penal = Evento(t_segundos=1, accion="Penal a los palos", resultado="afuera")
        assert reglas.puntos_de(penal, cfg) == 0

    def test_penal_y_drop_adentro(self, cfg):
        penal = Evento(t_segundos=1, accion="Penal a los palos", resultado="adentro")
        assert reglas.puntos_de(penal, cfg) == 3
        drop = Evento(t_segundos=1, accion="Drop", resultado="adentro")
        assert reglas.puntos_de(drop, cfg) == 3

    def test_los_puntos_son_configurables(self):
        c = Config({**CONFIG_BASE, "puntos": {"try": 4, "conversion": 2, "penal": 3}})
        assert reglas.puntos_de(Evento(t_segundos=1, accion="Try"), c) == 4


class TestReloj:
    def test_ida_y_vuelta(self):
        assert reglas.segundos_desde_reloj("22:48") == 1368.0
        assert reglas.reloj_desde_video(1368.0) == "22:48"

    def test_con_horas(self):
        assert reglas.segundos_desde_reloj("1:05:30") == 3930.0

    def test_reloj_invalido_devuelve_none(self):
        assert reglas.segundos_desde_reloj("") is None
        assert reglas.segundos_desde_reloj("ayer") is None

    def test_desfase_entre_cartel_y_video(self):
        """El cartel marca 22:48 cuando el video va por 5:00."""
        d = reglas.calcular_desfase("22:48", 300.0)
        assert d == 1068.0
        assert reglas.reloj_desde_video(300.0, d) == "22:48"


class TestMinutosJugados:
    def test_titular_sin_cambio_juega_todo(self):
        j = Jugador(numero=1, nombre="A", equipo="X", titular=True)
        assert reglas.minutos_jugados(j, 80) == 80.0

    def test_titular_que_sale(self):
        j = Jugador(numero=1, nombre="A", equipo="X", titular=True, salio_min=55)
        assert reglas.minutos_jugados(j, 80) == 55.0

    def test_suplente_que_entra(self):
        j = Jugador(numero=17, nombre="B", equipo="X", titular=False, entro_min=55)
        assert reglas.minutos_jugados(j, 80) == 25.0

    def test_suplente_que_no_entro_no_juega(self):
        j = Jugador(numero=20, nombre="C", equipo="X", titular=False)
        assert reglas.minutos_jugados(j, 80) == 0.0


class TestNormalizacion:
    def test_propio_y_rival_se_traducen_al_club(self):
        assert reglas.nombre_equipo("propio", "Club A", "Club B") == "Club A"
        assert reglas.nombre_equipo("rival", "Club A", "Club B") == "Club B"

    def test_numero_fuera_de_rango_es_none(self):
        assert reglas.limpiar_numero(0) is None
        assert reglas.limpiar_numero(100) is None
        assert reglas.limpiar_numero("nueve") is None
        assert reglas.limpiar_numero(None) is None

    def test_numero_valido_como_texto(self):
        assert reglas.limpiar_numero("9") == 9
        assert reglas.limpiar_numero(9.0) == 9
