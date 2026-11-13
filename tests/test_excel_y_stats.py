"""Las stats por jugador y el Excel.

Lo que se verifica del Excel: que las tres hojas estén, que la de jugadores
traiga fórmulas y no números pegados, que las fórmulas excluyan lo pendiente,
y que la hoja de notas diga qué está medido y qué es estimado.
"""

from __future__ import annotations

import openpyxl
import pytest

from rugby_stats import excel
from rugby_stats.modelos import Evento
from rugby_stats.stats import COLUMNAS, por_jugador, resumen_partido, totales
from tests.conftest import hacer_plantel


@pytest.fixture
def eventos_de_prueba() -> list[Evento]:
    """Un set chico con todo lo que importa: tackles de los dos signos,
    carries con metros, puntos, un pendiente y uno sin revisar."""
    return [
        Evento(t_segundos=10, accion="Tackle", resultado="positivo",
               equipo="Club A", jugador_numero=7, confianza=0.95, revisado=True),
        Evento(t_segundos=20, accion="Tackle", resultado="positivo",
               equipo="Club A", jugador_numero=7, confianza=0.90, revisado=True),
        Evento(t_segundos=30, accion="Tackle", resultado="negativo",
               equipo="Club A", jugador_numero=7, confianza=0.88, revisado=False),
        Evento(t_segundos=40, accion="Carry", resultado="avanza", metros=8.0,
               equipo="Club A", jugador_numero=10, confianza=0.9, revisado=True),
        Evento(t_segundos=50, accion="Carry", resultado="quiebre", metros=22.0,
               equipo="Club A", jugador_numero=10, confianza=0.9, revisado=True),
        Evento(t_segundos=60, accion="Try", resultado="convertido",
               equipo="Club A", jugador_numero=10, confianza=1.0, revisado=True),
        Evento(t_segundos=65, accion="Conversión", resultado="adentro",
               equipo="Club A", jugador_numero=9, confianza=1.0, revisado=True),
        Evento(t_segundos=70, accion="Penal a los palos", resultado="afuera",
               equipo="Club A", jugador_numero=9, confianza=1.0, revisado=True),
        # Pendiente: no se cuenta en ninguna cuenta.
        Evento(t_segundos=80, accion="Tackle", resultado="", equipo="Club A",
               jugador_numero=None, confianza=0.3, pendiente=True,
               nota="sin jugador identificado"),
        # Del rival.
        Evento(t_segundos=90, accion="Tackle", resultado="positivo",
               equipo="Club B", jugador_numero=7, confianza=0.9, revisado=True),
    ]


class TestPorJugador:
    def test_cuenta_los_tackles_de_cada_uno(self, cfg, eventos_de_prueba):
        filas = por_jugador(hacer_plantel("Club A"), eventos_de_prueba, cfg)
        f7 = next(f for f in filas if f.numero == 7)
        assert f7.valores["Tackles"] == 3
        assert f7.valores["Tackles positivos"] == 2
        assert f7.valores["Tackles negativos"] == 1

    def test_no_mezcla_el_mismo_numero_de_los_dos_equipos(self, cfg, eventos_de_prueba):
        """El 7 de un equipo y el 7 del otro son dos jugadores distintos."""
        filas_a = por_jugador(hacer_plantel("Club A"), eventos_de_prueba, cfg)
        filas_b = por_jugador(hacer_plantel("Club B"), eventos_de_prueba, cfg)
        assert next(f for f in filas_a if f.numero == 7).valores["Tackles"] == 3
        assert next(f for f in filas_b if f.numero == 7).valores["Tackles"] == 1

    def test_suma_los_metros_estimados(self, cfg, eventos_de_prueba):
        filas = por_jugador(hacer_plantel("Club A"), eventos_de_prueba, cfg)
        f10 = next(f for f in filas if f.numero == 10)
        assert f10.valores["Carries"] == 2
        assert f10.valores["Metros ganados (est.)"] == 30.0
        assert f10.valores["Quiebres"] == 1

    def test_los_puntos_salen_de_los_tries_y_pateos_adentro(self, cfg, eventos_de_prueba):
        filas = por_jugador(hacer_plantel("Club A"), eventos_de_prueba, cfg)
        assert next(f for f in filas if f.numero == 10).puntos == 5   # try
        assert next(f for f in filas if f.numero == 9).puntos == 2    # conversión
        # El penal afuera no suma.
        assert next(f for f in filas if f.numero == 9).valores[
            "Penales a los palos"
        ] == 0

    def test_lo_pendiente_no_se_cuenta(self, cfg, eventos_de_prueba):
        """Sin clasificar no vale: no entra a ninguna celda."""
        filas = por_jugador(hacer_plantel("Club A"), eventos_de_prueba, cfg)
        assert totales(filas)["Tackles"] == 3  # los 3 del 7, no el pendiente

    def test_solo_revisados_es_la_vista_honesta(self, cfg, eventos_de_prueba):
        filas = por_jugador(
            hacer_plantel("Club A"), eventos_de_prueba, cfg, solo_revisados=True
        )
        f7 = next(f for f in filas if f.numero == 7)
        assert f7.valores["Tackles"] == 2  # el tercero no está confirmado

    def test_cuenta_los_que_faltan_revisar(self, cfg, eventos_de_prueba):
        filas = por_jugador(hacer_plantel("Club A"), eventos_de_prueba, cfg)
        assert next(f for f in filas if f.numero == 7).eventos_sin_revisar == 1

    def test_un_jugador_sin_eventos_sale_en_cero(self, cfg, eventos_de_prueba):
        filas = por_jugador(hacer_plantel("Club A"), eventos_de_prueba, cfg)
        f20 = next(f for f in filas if f.numero == 20)
        assert f20.valores["Tackles"] == 0
        assert f20.puntos == 0

    def test_todas_las_columnas_declaradas_aparecen(self, cfg, eventos_de_prueba):
        filas = por_jugador(hacer_plantel("Club A"), eventos_de_prueba, cfg)
        for titulo, _, _, _ in COLUMNAS:
            assert titulo in filas[0].valores


class TestResumen:
    def test_numeros_gruesos_del_partido(self, cfg, eventos_de_prueba):
        r = resumen_partido(eventos_de_prueba, cfg)
        assert r["eventos"] == 10
        assert r["pendientes"] == 1
        assert r["sin_jugador"] == 1
        assert 0 < r["porcentaje_revisado"] < 100


class TestExcel:
    def test_crea_las_tres_hojas(self, tmp_path, partido, cfg, eventos_de_prueba):
        ruta = excel.exportar(
            tmp_path / "salida.xlsx", partido,
            hacer_plantel("Club A") + hacer_plantel("Club B"),
            eventos_de_prueba, cfg,
        )
        wb = openpyxl.load_workbook(ruta)
        assert wb.sheetnames == ["Jugadores", "Eventos", "Notas"]

    def test_la_hoja_de_jugadores_trae_formulas(
        self, tmp_path, partido, cfg, eventos_de_prueba
    ):
        """Si fueran números pegados, corregir un evento no actualizaría nada."""
        ruta = excel.exportar(
            tmp_path / "s.xlsx", partido, hacer_plantel("Club A"),
            eventos_de_prueba, cfg,
        )
        h = openpyxl.load_workbook(ruta)["Jugadores"]
        # Columna D de la primera fila de datos: la primera stat.
        celda = h["D3"].value
        assert isinstance(celda, str) and celda.startswith("=COUNTIFS(")
        assert "Eventos!" in celda

    def test_las_formulas_excluyen_lo_pendiente(
        self, tmp_path, partido, cfg, eventos_de_prueba
    ):
        ruta = excel.exportar(
            tmp_path / "s.xlsx", partido, hacer_plantel("Club A"),
            eventos_de_prueba, cfg,
        )
        h = openpyxl.load_workbook(ruta)["Jugadores"]
        assert '"no"' in h["D3"].value  # la condición sobre Pendiente

    def test_los_metros_se_suman_no_se_cuentan(
        self, tmp_path, partido, cfg, eventos_de_prueba
    ):
        ruta = excel.exportar(
            tmp_path / "s.xlsx", partido, hacer_plantel("Club A"),
            eventos_de_prueba, cfg,
        )
        h = openpyxl.load_workbook(ruta)["Jugadores"]
        titulos = {h.cell(row=2, column=c).value: c for c in range(1, 40)}
        col = titulos["Metros ganados (est.)"]
        assert h.cell(row=3, column=col).value.startswith("=SUMIFS(")

    def test_los_puntos_apuntan_a_las_celdas_editables(
        self, tmp_path, partido, cfg, eventos_de_prueba
    ):
        """Los puntos por try son editables en la hoja Notas."""
        ruta = excel.exportar(
            tmp_path / "s.xlsx", partido, hacer_plantel("Club A"),
            eventos_de_prueba, cfg,
        )
        wb = openpyxl.load_workbook(ruta)
        h = wb["Jugadores"]
        titulos = {h.cell(row=2, column=c).value: c for c in range(1, 40)}
        formula = h.cell(row=3, column=titulos["Puntos"]).value
        assert f"Notas!{excel.CELDA_TRY}" in formula
        # Y esa celda tiene de verdad el valor de los puntos del try.
        notas = wb["Notas"]
        assert notas[f"B{excel.FILA_PUNTOS}"].value == cfg.puntos["try"]
        assert notas[f"A{excel.FILA_PUNTOS}"].value == "Try"

    def test_las_celdas_de_puntos_no_se_mueven_en_modo_simulado(
        self, tmp_path, partido, cfg, eventos_de_prueba
    ):
        """El aviso de simulado no puede correr la tabla: las fórmulas la fijan."""
        ruta = excel.exportar(
            tmp_path / "s.xlsx", partido, hacer_plantel("Club A"),
            eventos_de_prueba, cfg, simulado=True,
        )
        notas = openpyxl.load_workbook(ruta)["Notas"]
        assert notas[f"A{excel.FILA_PUNTOS}"].value == "Try"
        assert notas[f"B{excel.FILA_PUNTOS}"].value == cfg.puntos["try"]
        assert notas[f"A{excel.FILA_PUNTOS + 1}"].value == "Conversión"

    def test_hay_fila_de_total(self, tmp_path, partido, cfg, eventos_de_prueba):
        ruta = excel.exportar(
            tmp_path / "s.xlsx", partido, hacer_plantel("Club A"),
            eventos_de_prueba, cfg,
        )
        h = openpyxl.load_workbook(ruta)["Jugadores"]
        etiquetas = [h.cell(row=r, column=2).value for r in range(3, 40)]
        assert "TOTAL" in etiquetas

    def test_la_hoja_de_eventos_tiene_una_fila_por_evento(
        self, tmp_path, partido, cfg, eventos_de_prueba
    ):
        ruta = excel.exportar(
            tmp_path / "s.xlsx", partido, hacer_plantel("Club A"),
            eventos_de_prueba, cfg,
        )
        h = openpyxl.load_workbook(ruta)["Eventos"]
        assert h.max_row == len(eventos_de_prueba) + 1  # + cabecera

    def test_la_hoja_de_notas_distingue_medido_de_estimado(
        self, tmp_path, partido, cfg, eventos_de_prueba
    ):
        """Pedido explícito: siempre aclarar qué está medido y qué es estimado."""
        ruta = excel.exportar(
            tmp_path / "s.xlsx", partido, hacer_plantel("Club A"),
            eventos_de_prueba, cfg,
        )
        h = openpyxl.load_workbook(ruta)["Notas"]
        texto = " ".join(
            str(c.value) for fila in h.iter_rows() for c in fila if c.value
        )
        assert "Medido" in texto
        assert "Estimado" in texto
        assert "Metros ganados" in texto
        assert "Tackle positivo" in texto
        assert "Sin clasificar no vale" in texto

    def test_el_modo_simulado_queda_avisado_en_la_planilla(
        self, tmp_path, partido, cfg, eventos_de_prueba
    ):
        ruta = excel.exportar(
            tmp_path / "s.xlsx", partido, hacer_plantel("Club A"),
            eventos_de_prueba, cfg, simulado=True,
        )
        h = openpyxl.load_workbook(ruta)["Notas"]
        texto = " ".join(
            str(c.value) for fila in h.iter_rows() for c in fila if c.value
        )
        assert "SIMULADO" in texto.upper()
        assert "inventados" in texto

    def test_el_criterio_de_tackle_configurado_sale_en_las_notas(
        self, tmp_path, partido, eventos_de_prueba
    ):
        from rugby_stats.config import Config
        from tests.conftest import CONFIG_BASE

        invertido = Config({
            **CONFIG_BASE,
            "reglas": {**CONFIG_BASE["reglas"], "tackle_positivo": "portador_avanza"},
        })
        ruta = excel.exportar(
            tmp_path / "s.xlsx", partido, hacer_plantel("Club A"),
            eventos_de_prueba, invertido,
        )
        h = openpyxl.load_workbook(ruta)["Notas"]
        texto = " ".join(
            str(c.value) for fila in h.iter_rows() for c in fila if c.value
        )
        assert "SÍ avanza" in texto

    def test_sin_eventos_no_explota(self, tmp_path, partido, cfg):
        ruta = excel.exportar(
            tmp_path / "s.xlsx", partido, hacer_plantel("Club A"), [], cfg
        )
        wb = openpyxl.load_workbook(ruta)
        assert wb["Eventos"].max_row == 1

    def test_incluir_los_dos_equipos(self, tmp_path, partido, eventos_de_prueba):
        from rugby_stats.config import Config
        from tests.conftest import CONFIG_BASE

        cfg_ambos = Config({
            **CONFIG_BASE,
            "salida": {"jugadores_de": "ambos", "dir": str(tmp_path)},
        })
        ruta = excel.exportar(
            tmp_path / "s.xlsx", partido,
            hacer_plantel("Club A") + hacer_plantel("Club B"),
            eventos_de_prueba, cfg_ambos,
        )
        h = openpyxl.load_workbook(ruta)["Jugadores"]
        equipos = {h.cell(row=r, column=3).value for r in range(3, 60)}
        assert "Club A" in equipos and "Club B" in equipos
