"""Una persona usando la interfaz de punta a punta.

Crea un partido, carga el plantel, busca fichas, corre el análisis, revisa,
mira la precisión y exporta: todo apretando los botones de verdad, en modo
simulado. Es la prueba de que las pantallas no solo abren sino que hacen lo
que dicen.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit.testing.v1", reason="hace falta streamlit >= 1.28")

from rugby_stats.db import BaseDatos  # noqa: E402
from rugby_stats.modelos import Jugador  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent


def abrir(pantalla: str, **estado):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(RAIZ / pantalla), default_timeout=120)
    for clave, valor in estado.items():
        at.session_state[clave] = valor
    at.run()
    sin_error(at, f"abrir {pantalla}")
    return at


def sin_error(at, que: str) -> None:
    assert not at.exception, (
        f"{que}: {at.exception[0].message if at.exception else ''}"
    )


def por_etiqueta(coleccion, etiqueta: str):
    for elemento in coleccion:
        if elemento.label == etiqueta:
            return elemento
    raise AssertionError(
        f"No hay '{etiqueta}'. Hay: {[e.label for e in coleccion]}"
    )


@pytest.fixture
def base(tmp_path, monkeypatch):
    ruta = tmp_path / "flujo.db"
    monkeypatch.setenv("RUGBY_STATS_DB", str(ruta))
    monkeypatch.chdir(RAIZ)
    db = BaseDatos(ruta)
    yield db
    db.cerrar()


def crear_partido(base) -> int:
    at = abrir("pages/1_Partido.py")
    por_etiqueta(at.text_input, "Nombre del club").set_value("Club A")
    por_etiqueta(at.text_input, "Cómo es la camiseta").set_value("rayas verdes")
    por_etiqueta(at.text_input, "Nombre del club rival").set_value("Club B")
    por_etiqueta(at.text_input, "Cómo es la camiseta rival").set_value("azul liso")
    por_etiqueta(
        at.text_input, "Link de YouTube, otra URL, o la ruta de un archivo"
    ).set_value("https://www.youtube.com/watch?v=VsRZotfwL6A")
    por_etiqueta(at.text_input, "Desde (mm:ss del video)").set_value("10:00")
    por_etiqueta(
        at.text_input, "Hasta (mm:ss del video, vacío = hasta el final)"
    ).set_value("14:00")
    at.select_slider(key="legible_propio").set_value("baja")
    por_etiqueta(at.button, "Crear partido").click()
    at.run()
    sin_error(at, "crear partido")
    partidos = base.partidos()
    assert len(partidos) == 1
    return partidos[0].id


def cargar_plantel(base, pid: int) -> None:
    base.guardar_plantel(
        pid,
        [
            Jugador(numero=n, nombre=f"Jugador {n}", equipo="Club A", titular=n <= 15)
            for n in range(1, 24)
        ],
    )


class TestPartido:
    def test_crear_un_partido_lo_guarda_como_se_cargo(self, base):
        pid = crear_partido(base)
        p = base.partido(pid)
        assert p.equipo_propio.nombre == "Club A"
        assert p.equipo_propio.kit == "rayas verdes"
        assert p.equipo_propio.numero_legible == "baja"
        assert p.equipo_rival.nombre == "Club B"
        assert p.video.tipo == "youtube"
        assert (p.analizar_desde_s, p.analizar_hasta_s) == (600.0, 840.0)
        assert p.nombre == "Club A vs Club B"  # se arma solo si queda vacío

    def test_sin_datos_avisa_y_no_crea_nada(self, base):
        at = abrir("pages/1_Partido.py")
        por_etiqueta(at.button, "Crear partido").click()
        at.run()
        sin_error(at, "crear vacío")
        assert base.partidos() == []
        avisos = " ".join(e.value for e in at.error)
        assert "equipo propio" in avisos and "video" in avisos

    def test_los_dos_equipos_no_pueden_llamarse_igual(self, base):
        at = abrir("pages/1_Partido.py")
        por_etiqueta(at.text_input, "Nombre del club").set_value("Club A")
        por_etiqueta(at.text_input, "Nombre del club rival").set_value("Club A")
        por_etiqueta(
            at.text_input, "Link de YouTube, otra URL, o la ruta de un archivo"
        ).set_value("https://youtu.be/VsRZotfwL6A")
        por_etiqueta(at.button, "Crear partido").click()
        at.run()
        assert base.partidos() == []
        assert any("mismo nombre" in e.value for e in at.error)

    def test_un_tramo_al_reves_se_rechaza(self, base):
        at = abrir("pages/1_Partido.py")
        por_etiqueta(at.text_input, "Nombre del club").set_value("Club A")
        por_etiqueta(at.text_input, "Nombre del club rival").set_value("Club B")
        por_etiqueta(
            at.text_input, "Link de YouTube, otra URL, o la ruta de un archivo"
        ).set_value("https://youtu.be/VsRZotfwL6A")
        por_etiqueta(at.text_input, "Desde (mm:ss del video)").set_value("20:00")
        por_etiqueta(
            at.text_input, "Hasta (mm:ss del video, vacío = hasta el final)"
        ).set_value("10:00")
        por_etiqueta(at.button, "Crear partido").click()
        at.run()
        assert base.partidos() == []


class TestPlantel:
    def test_guardar_una_tabla_vacia_avisa(self, base):
        pid = crear_partido(base)
        at = abrir("pages/2_Plantel.py", partido_id=pid)
        por_etiqueta(at.button, "Guardar plantel").click()
        at.run()
        sin_error(at, "guardar plantel vacío")
        assert base.plantel(pid) == []
        assert any("ningún jugador" in e.value for e in at.error)

    def test_volver_a_guardar_no_pierde_a_nadie(self, base):
        pid = crear_partido(base)
        cargar_plantel(base, pid)
        at = abrir("pages/2_Plantel.py", partido_id=pid)
        por_etiqueta(at.button, "Guardar plantel").click()
        at.run()
        sin_error(at, "re-guardar plantel")
        jugadores = base.plantel(pid, "Club A")
        assert len(jugadores) == 23
        assert len([j for j in jugadores if j.titular]) == 15


class TestFlujoCompleto:
    def test_de_las_fichas_al_excel(self, base, tmp_path):
        pid = crear_partido(base)
        cargar_plantel(base, pid)

        # Fichas
        at = abrir("pages/3_Fichas.py", partido_id=pid, forzar_simulado=True)
        por_etiqueta(at.button, "Buscar fichas").click()
        at.run()
        sin_error(at, "buscar fichas")
        assert base.fichas(pid, "Club A")
        confirmar = [b for b in at.button if b.label == "Confirmar"]
        assert confirmar, "tendría que haber fichas para confirmar"
        confirmar[0].click()
        at.run()
        sin_error(at, "confirmar ficha")
        assert any(f.confirmada for f in base.fichas(pid, "Club A"))

        # Análisis
        at = abrir("pages/4_Analisis.py", partido_id=pid, forzar_simulado=True)
        por_etiqueta(at.button, "Correr análisis").click()
        at.run()
        sin_error(at, "correr análisis")
        eventos = base.eventos(pid)
        assert eventos
        assert base.tramos(pid)
        # El aviso de que son datos inventados tiene que estar a la vista.
        assert any("simulado" in w.value.lower() for w in at.warning)

        # Revisión: una corrección y un descarte
        at = abrir("pages/5_Revision.py", partido_id=pid, forzar_simulado=True)
        botones = [b for b in at.button if b.label in ("Confirmar", "Guardar corrección")]
        assert botones
        botones[0].click()
        at.run()
        sin_error(at, "confirmar o corregir un evento")
        descartar = [b for b in at.button if b.label == "No existió"]
        descartar[0].click()
        at.run()
        sin_error(at, "descartar un evento")
        revisados = [e for e in base.eventos(pid) if e.revisado]
        assert len(revisados) == 2
        assert any(c.campo == "descartado" for c in base.correcciones(pid))

        # Precisión
        at = abrir("pages/6_Precision.py", partido_id=pid)
        assert not any("ninguno confirmado" in w.value for w in at.warning)

        # Exportar
        at = abrir("pages/7_Exportar.py", partido_id=pid, forzar_simulado=True)
        por_etiqueta(at.button, "Generar el Excel").click()
        at.run()
        sin_error(at, "generar el Excel")
        assert any("Listo" in s.value for s in at.success)

    def test_analizar_sin_plantel_frena_con_un_aviso(self, base):
        pid = crear_partido(base)
        at = abrir("pages/4_Analisis.py", partido_id=pid, forzar_simulado=True)
        assert any("plantel" in w.value.lower() for w in at.warning)
        assert base.eventos(pid) == []

    def test_las_pantallas_de_resultado_no_explotan_sin_eventos(self, base):
        pid = crear_partido(base)
        cargar_plantel(base, pid)
        for pantalla in ("5_Revision", "6_Precision", "7_Exportar"):
            at = abrir(f"pages/{pantalla}.py", partido_id=pid)
            assert any("no hay eventos" in i.value.lower() for i in at.info)
