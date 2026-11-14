"""La base de datos: todo queda guardado para poder retomar."""

from __future__ import annotations

from rugby_stats.modelos import Correccion, Evento, Ficha, Jugador, Tramo
from tests.conftest import hacer_plantel


class TestPartidos:
    def test_guardar_y_recuperar(self, db, partido):
        pid = db.guardar_partido(partido)
        recuperado = db.partido(pid)
        assert recuperado.nombre == partido.nombre
        assert recuperado.equipo_propio.nombre == "Club A"
        assert recuperado.equipo_propio.numero_legible == "baja"
        assert recuperado.equipo_rival.kit == "azul liso, número blanco"
        assert recuperado.video.tipo == "youtube"
        assert recuperado.analizar_hasta_s == 300.0

    def test_actualizar_no_duplica(self, db, partido):
        pid = db.guardar_partido(partido)
        partido.nombre = "Otro nombre"
        db.guardar_partido(partido)
        assert len(db.partidos()) == 1
        assert db.partido(pid).nombre == "Otro nombre"

    def test_borrar_arrastra_todo(self, db, partido):
        pid = db.guardar_partido(partido)
        db.guardar_plantel(pid, hacer_plantel("Club A"))
        db.guardar_evento(pid, Evento(t_segundos=1, accion="Tackle"))
        db.borrar_partido(pid)
        assert db.partido(pid) is None
        assert db.plantel(pid) == []
        assert db.eventos(pid) == []


class TestPlantel:
    def test_guardar_y_leer_por_equipo(self, db, partido):
        pid = db.guardar_partido(partido)
        db.guardar_plantel(pid, hacer_plantel("Club A"))
        db.guardar_plantel(pid, hacer_plantel("Club B", 23))
        assert len(db.plantel(pid)) == 46
        assert len(db.plantel(pid, "Club A")) == 23
        assert all(j.equipo == "Club A" for j in db.plantel(pid, "Club A"))

    def test_volver_a_guardar_pisa_solo_ese_equipo(self, db, partido):
        pid = db.guardar_partido(partido)
        db.guardar_plantel(pid, hacer_plantel("Club A"))
        db.guardar_plantel(pid, hacer_plantel("Club B"))
        db.guardar_plantel(pid, [Jugador(numero=1, nombre="Único", equipo="Club A")])
        assert len(db.plantel(pid, "Club A")) == 1
        assert len(db.plantel(pid, "Club B")) == 23

    def test_titulares_y_suplentes(self, db, partido):
        pid = db.guardar_partido(partido)
        db.guardar_plantel(pid, hacer_plantel("Club A"))
        jugadores = db.plantel(pid, "Club A")
        assert len([j for j in jugadores if j.titular]) == 15
        assert len([j for j in jugadores if not j.titular]) == 8

    def test_cargar_un_cambio(self, db, partido):
        pid = db.guardar_partido(partido)
        db.guardar_plantel(pid, hacer_plantel("Club A"))
        db.actualizar_cambio(pid, "Club A", 17, entro_min=55.0, salio_min=None)
        db.actualizar_cambio(pid, "Club A", 2, entro_min=None, salio_min=55.0)
        por_numero = {j.numero: j for j in db.plantel(pid, "Club A")}
        assert por_numero[17].entro_min == 55.0
        assert por_numero[2].salio_min == 55.0


class TestFichas:
    def test_la_ficha_vigente_es_la_ultima_leida(self, db, partido_cargado):
        """Las fichas se renuevan: se embarran, se sacan el casco."""
        pid = partido_cargado.id
        db.guardar_ficha(pid, Ficha(
            jugador_numero=4, equipo="Club A", t_segundos=100,
            rasgos={"casco": "sí, oscuro"}, confianza=0.9,
        ))
        db.guardar_ficha(pid, Ficha(
            jugador_numero=4, equipo="Club A", t_segundos=2000,
            rasgos={"casco": "no"}, confianza=0.8,
        ))
        a_los_500 = db.fichas_vigentes(pid, "Club A", 500)
        assert a_los_500[4].rasgos["casco"] == "sí, oscuro"
        al_final = db.fichas_vigentes(pid, "Club A", 3000)
        assert al_final[4].rasgos["casco"] == "no"

    def test_confirmar_sube_la_confianza_a_uno(self, db, partido_cargado):
        fid = db.guardar_ficha(partido_cargado.id, Ficha(
            jugador_numero=9, equipo="Club A", t_segundos=50, confianza=0.6,
        ))
        db.confirmar_ficha(fid, {"color_botines": "amarillos"})
        ficha = db.fichas(partido_cargado.id, "Club A")[0]
        assert ficha.confirmada
        assert ficha.confianza == 1.0
        assert ficha.rasgos == {"color_botines": "amarillos"}

    def test_descripcion_para_el_prompt(self):
        f = Ficha(
            jugador_numero=4, equipo="X", t_segundos=0,
            rasgos={"casco": "no", "color_botines": "blancos", "pelo": ""},
        )
        texto = f.descripcion()
        assert "casco: no" in texto
        assert "color botines: blancos" in texto
        assert "pelo" not in texto  # los vacíos no entran


class TestEventos:
    def test_guardar_y_leer(self, db, partido_cargado):
        pid = partido_cargado.id
        db.guardar_evento(pid, Evento(
            t_segundos=10.5, accion="Tackle", resultado="positivo",
            equipo="Club A", jugador_numero=7, confianza=0.9,
            jugadores_secundarios=[8],
        ))
        e = db.eventos(pid)[0]
        assert e.accion == "Tackle"
        assert e.jugador_numero == 7
        assert e.jugadores_secundarios == [8]

    def test_la_cola_pone_lo_mas_dudoso_primero(self, db, partido_cargado):
        """Es el orden en que conviene gastar el tiempo de una persona."""
        pid = partido_cargado.id
        db.guardar_evento(pid, Evento(
            t_segundos=1, accion="Tackle", resultado="positivo",
            jugador_numero=1, confianza=0.95,
        ))
        db.guardar_evento(pid, Evento(
            t_segundos=2, accion="Carry", resultado="avanza",
            jugador_numero=2, confianza=0.40,
        ))
        db.guardar_evento(pid, Evento(
            t_segundos=3, accion="Tackle", jugador_numero=None,
            confianza=0.70, pendiente=True,
        ))
        cola = db.cola_revision(pid)
        assert cola[0].pendiente is True          # primero los sin clasificar
        assert cola[1].confianza == 0.40          # después, confianza creciente
        assert cola[2].confianza == 0.95

    def test_lo_revisado_sale_de_la_cola(self, db, partido_cargado):
        pid = partido_cargado.id
        eid = db.guardar_evento(pid, Evento(
            t_segundos=1, accion="Tackle", confianza=0.2,
        ))
        e = db.evento(eid)
        e.revisado = True
        db.guardar_evento(pid, e)
        assert db.cola_revision(pid) == []

    def test_borrar_por_tramo(self, db, partido_cargado):
        pid = partido_cargado.id
        for t in (10, 50, 200):
            db.guardar_evento(pid, Evento(t_segundos=t, accion="Carry"))
        db.borrar_eventos(pid, desde_s=0, hasta_s=100)
        assert [e.t_segundos for e in db.eventos(pid)] == [200]

    def test_la_propuesta_original_se_guarda(self, db, partido_cargado):
        """Hace falta para medir precisión: qué propuso el modelo."""
        pid = partido_cargado.id
        db.guardar_evento(pid, Evento(
            t_segundos=1, accion="Tackle", jugador_numero=23,
            propuesta_original={"numero": 23, "confianza": 0.5},
        ))
        assert db.eventos(pid)[0].propuesta_original["numero"] == 23


class TestTramos:
    def test_juego_y_tiempo_muerto(self, db, partido_cargado):
        pid = partido_cargado.id
        db.guardar_tramos(pid, [
            Tramo(inicio_s=0, fin_s=30, tipo="juego", confianza=0.9),
            Tramo(inicio_s=30, fin_s=90, tipo="muerto", confianza=0.8),
        ])
        assert len(db.tramos(pid)) == 2
        assert len(db.tramos(pid, "juego")) == 1
        assert db.tramos(pid, "muerto")[0].duracion_s == 60


class TestProgreso:
    def test_marcar_y_leer(self, db, partido_cargado):
        pid = partido_cargado.id
        db.marcar_progreso(pid, "gruesa", "listo", "12 tramos")
        assert db.progreso(pid)["gruesa"]["estado"] == "listo"
        assert db.progreso(pid)["gruesa"]["detalle"] == "12 tramos"

    def test_volver_a_marcar_actualiza(self, db, partido_cargado):
        pid = partido_cargado.id
        db.marcar_progreso(pid, "eventos", "corriendo")
        db.marcar_progreso(pid, "eventos", "listo", "40 eventos")
        assert len(db.progreso(pid)) == 1
        assert db.progreso(pid)["eventos"]["estado"] == "listo"


class TestCorrecciones:
    def test_se_guardan_para_medir_precision(self, db, partido_cargado):
        pid = partido_cargado.id
        db.guardar_correccion(pid, Correccion(
            evento_id=1, campo="jugador_numero",
            valor_modelo="23", valor_persona="13",
        ))
        c = db.correcciones(pid)[0]
        assert c.campo == "jugador_numero"
        assert c.valor_modelo == "23"
        assert c.momento  # se sella con la fecha solo


class TestHilos:
    """Streamlit corre cada clic en un hilo distinto del que abrió la base."""

    def test_se_puede_usar_desde_otro_hilo(self, db, partido):
        import threading

        pid = db.guardar_partido(partido)
        resultado: dict = {}

        def desde_otro_hilo():
            try:
                resultado["partidos"] = len(db.partidos())
                db.guardar_plantel(pid, hacer_plantel("Club A"))
                resultado["jugadores"] = len(db.plantel(pid))
            except Exception as exc:  # noqa: BLE001
                resultado["error"] = exc

        h = threading.Thread(target=desde_otro_hilo)
        h.start()
        h.join()
        assert "error" not in resultado, resultado.get("error")
        assert resultado == {"partidos": 1, "jugadores": 23}

    def test_varios_hilos_escribiendo_a_la_vez_no_pierden_nada(self, db, partido):
        import threading

        pid = db.guardar_partido(partido)
        errores: list = []

        def escribir(n):
            try:
                for i in range(20):
                    db.guardar_evento(pid, Evento(t_segundos=n * 100 + i, accion="Pase"))
            except Exception as exc:  # noqa: BLE001
                errores.append(exc)

        hilos = [threading.Thread(target=escribir, args=(n,)) for n in range(5)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        assert errores == []
        assert len(db.eventos(pid)) == 100
