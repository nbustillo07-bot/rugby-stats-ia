"""El pipeline de punta a punta, con el cliente simulado.

Esto es lo que se puede verificar sin clave de API: que los pasos corran en
orden, que todo quede guardado, que se pueda retomar, y que los eventos sin
clasificar no se cuelen a las stats.
"""

from __future__ import annotations

from rugby_stats.analisis import pasadas, pipeline
from rugby_stats.gemini.cliente import ClienteSimulado
from rugby_stats.modelos import Evento, Ficha


class TestCorrer:
    def test_corre_todos_los_pasos_y_guarda(self, db, partido_cargado, cfg):
        res = pipeline.correr(
            db, ClienteSimulado(), partido_cargado.id, cfg
        )
        assert set(res.pasos_corridos) == set(pipeline.PASOS)
        assert res.simulado is True
        assert "simulado" in res.aviso.lower()
        assert db.tramos(partido_cargado.id)
        assert db.eventos(partido_cargado.id)
        assert db.fichas(partido_cargado.id, "Club A")

    def test_el_estado_queda_marcado(self, db, partido_cargado, cfg):
        pipeline.correr(db, ClienteSimulado(), partido_cargado.id, cfg)
        estados = {p["paso"]: p["estado"] for p in pipeline.estado(db, partido_cargado.id)}
        assert all(e == "listo" for e in estados.values())

    def test_se_puede_retomar_sin_repetir(self, db, partido_cargado, cfg):
        """Si se agota la cuota a mitad de camino, al volver sigue de ahí."""
        pipeline.correr(
            db, ClienteSimulado(), partido_cargado.id, cfg, pasos=["gruesa"]
        )
        segunda = pipeline.correr(db, ClienteSimulado(), partido_cargado.id, cfg)
        assert "gruesa" in segunda.pasos_salteados
        assert "eventos" in segunda.pasos_corridos

    def test_forzar_rehace_un_paso_listo(self, db, partido_cargado, cfg):
        pipeline.correr(
            db, ClienteSimulado(), partido_cargado.id, cfg, pasos=["gruesa"]
        )
        res = pipeline.correr(
            db, ClienteSimulado(), partido_cargado.id, cfg,
            pasos=["gruesa"], forzar=True,
        )
        assert "gruesa" in res.pasos_corridos

    def test_correr_solo_algunos_pasos(self, db, partido_cargado, cfg):
        res = pipeline.correr(
            db, ClienteSimulado(), partido_cargado.id, cfg, pasos=["gruesa", "relato"]
        )
        assert set(res.pasos_corridos) == {"gruesa", "relato"}
        assert db.eventos(partido_cargado.id) == []

    def test_sin_plantel_no_arranca(self, db, partido, cfg):
        """El plantel es el paso 0: sin números no hay a quién atribuir nada."""
        pid = db.guardar_partido(partido)
        try:
            pipeline.correr(db, ClienteSimulado(), pid, cfg)
        except ValueError as exc:
            assert "plantel" in str(exc)
        else:
            raise AssertionError("Tendría que haber avisado que falta el plantel.")

    def test_partido_que_no_existe(self, db, cfg):
        try:
            pipeline.correr(db, ClienteSimulado(), 999, cfg)
        except ValueError as exc:
            assert "999" in str(exc)
        else:
            raise AssertionError("Tendría que haber avisado que no existe.")

    def test_la_barra_de_progreso_recibe_avisos(self, db, partido_cargado, cfg):
        avisos: list[tuple[str, float]] = []
        pipeline.correr(
            db, ClienteSimulado(), partido_cargado.id, cfg,
            pasos=["gruesa"],
            progreso=lambda t, f: avisos.append((t, f)),
        )
        assert avisos
        assert all(0.0 <= f <= 1.0 for _, f in avisos)

    def test_una_barra_que_explota_no_frena_el_analisis(self, db, partido_cargado, cfg):
        def mala(texto, fraccion):
            raise RuntimeError("la interfaz se cayó")

        res = pipeline.correr(
            db, ClienteSimulado(), partido_cargado.id, cfg,
            pasos=["gruesa"], progreso=mala,
        )
        assert "gruesa" in res.pasos_corridos


class TestValidacionEnElPipeline:
    def test_los_eventos_sin_clasificar_quedan_pendientes(self, db, partido_cargado, cfg):
        """Regla de Nico: sin jugador o sin resultado, no vale."""
        cliente = ClienteSimulado()
        cliente.respuestas["eventos"] = {
            "eventos": [
                {
                    "t_segundos": 10, "accion": "Tackle", "resultado": "positivo",
                    "equipo": "propio", "numero": 7, "confianza": 0.9,
                },
                {   # sin número: tiene que quedar pendiente
                    "t_segundos": 20, "accion": "Tackle", "resultado": "positivo",
                    "equipo": "propio", "numero": None, "confianza": 0.9,
                },
                {   # número que no está en el plantel
                    "t_segundos": 30, "accion": "Carry", "resultado": "avanza",
                    "equipo": "propio", "numero": 77, "confianza": 0.95,
                },
                {   # sin resultado
                    "t_segundos": 40, "accion": "Pase", "resultado": "",
                    "equipo": "propio", "numero": 9, "confianza": 0.9,
                },
            ]
        }
        pipeline.correr(
            db, cliente, partido_cargado.id, cfg, pasos=["eventos"]
        )
        eventos = {e.t_segundos: e for e in db.eventos(partido_cargado.id)}
        assert eventos[10].pendiente is False
        assert eventos[20].pendiente is True
        assert "sin jugador" in eventos[20].nota
        assert eventos[30].pendiente is True
        assert "77" in eventos[30].nota
        assert eventos[40].pendiente is True
        assert "sin resultado" in eventos[40].nota


class TestUnirTramos:
    def test_pega_los_contiguos_del_mismo_tipo(self):
        from rugby_stats.modelos import Tramo

        unidos = pasadas._unir_tramos([
            Tramo(inicio_s=0, fin_s=30, tipo="juego", confianza=0.9),
            Tramo(inicio_s=30, fin_s=60, tipo="juego", confianza=0.8),
            Tramo(inicio_s=60, fin_s=90, tipo="muerto", confianza=0.9),
        ])
        assert len(unidos) == 2
        assert unidos[0].fin_s == 60
        assert unidos[0].confianza == 0.8  # se queda con la peor

    def test_resuelve_solapes_entre_tipos_distintos(self):
        from rugby_stats.modelos import Tramo

        unidos = pasadas._unir_tramos([
            Tramo(inicio_s=0, fin_s=50, tipo="juego"),
            Tramo(inicio_s=40, fin_s=90, tipo="muerto"),
        ])
        assert unidos[0].fin_s == 50
        assert unidos[1].inicio_s == 50  # no quedan solapados
        assert all(t.fin_s > t.inicio_s for t in unidos)

    def test_lista_vacia(self):
        assert pasadas._unir_tramos([]) == []


class TestQuitarDuplicados:
    def test_el_solape_no_duplica_eventos(self):
        """Las ventanas se solapan: el mismo evento puede salir dos veces."""
        eventos = [
            Evento(t_segundos=100.0, accion="Tackle", equipo="A",
                   jugador_numero=7, confianza=0.8),
            Evento(t_segundos=100.5, accion="Tackle", equipo="A",
                   jugador_numero=7, confianza=0.9),
        ]
        sin_dup = pasadas._quitar_duplicados(eventos)
        assert len(sin_dup) == 1
        assert sin_dup[0].confianza == 0.9  # se queda con el de más confianza

    def test_no_junta_eventos_distintos(self):
        eventos = [
            Evento(t_segundos=100.0, accion="Tackle", equipo="A", jugador_numero=7),
            Evento(t_segundos=100.2, accion="Tackle", equipo="A", jugador_numero=8),
            Evento(t_segundos=100.3, accion="Carry", equipo="B", jugador_numero=7),
        ]
        assert len(pasadas._quitar_duplicados(eventos)) == 3

    def test_eventos_lejanos_no_son_duplicados(self):
        eventos = [
            Evento(t_segundos=100, accion="Tackle", equipo="A", jugador_numero=7),
            Evento(t_segundos=130, accion="Tackle", equipo="A", jugador_numero=7),
        ]
        assert len(pasadas._quitar_duplicados(eventos)) == 2


class TestContactoAEventos:
    def test_un_contacto_se_abre_en_carry_y_tackles(self, partido, cfg):
        datos = {
            "portador": 10, "portador_equipo": "rival",
            "tackleadores": [6, 7], "tackleadores_equipo": "propio",
            "avance_portador": "se_detiene", "metros_avance": -1.0,
            "tackle_completado": True, "pelota_adelante": False,
            "confianza": 0.9, "confianza_identificacion": 0.85,
        }
        eventos = pasadas._contacto_a_eventos(
            datos, 100.0, partido, cfg, {1, 6, 7}, {10}
        )
        acciones = [(e.accion, e.jugador_numero, e.equipo) for e in eventos]
        assert ("Carry", 10, "Club B") in acciones
        assert ("Tackle", 6, "Club A") in acciones
        assert ("Tackle", 7, "Club A") in acciones
        # Portador detenido => tackle positivo con el criterio por defecto.
        tackles = [e for e in eventos if e.accion == "Tackle"]
        assert all(e.resultado == "positivo" for e in tackles)
        # Cada tackleador lista al otro como secundario.
        assert tackles[0].jugadores_secundarios == [7]

    def test_sin_tackleador_identificado_queda_pendiente(self, partido, cfg):
        """No se descarta el contacto: se manda a la cola."""
        datos = {
            "portador": 10, "portador_equipo": "rival",
            "tackleadores": [], "avance_portador": "se_detiene",
            "tackle_completado": True, "confianza": 0.9,
        }
        eventos = pasadas._contacto_a_eventos(datos, 100.0, partido, cfg, set(), set())
        tackles = [e for e in eventos if e.accion.startswith("Tackle")]
        assert len(tackles) == 1
        assert tackles[0].jugador_numero is None
        assert tackles[0].pendiente is True
        assert "no se pudo identificar" in tackles[0].nota

    def test_el_equipo_del_tackle_se_deduce_del_portador(self, partido, cfg):
        datos = {
            "portador": 10, "portador_equipo": "propio",
            "tackleadores": [5], "avance_portador": "avanza",
            "confianza": 0.8,
        }
        eventos = pasadas._contacto_a_eventos(datos, 50.0, partido, cfg, {10}, {5})
        tackle = next(e for e in eventos if e.accion == "Tackle")
        assert tackle.equipo == "Club B"  # el otro equipo
        assert tackle.resultado == "negativo"  # el portador avanzó

    def test_pelota_adelante_genera_un_knock_on(self, partido, cfg):
        datos = {
            "portador": 12, "portador_equipo": "propio",
            "tackleadores": [4], "avance_portador": "retrocede",
            "pelota_adelante": True, "confianza": 0.85,
            "confianza_identificacion": 0.9,
        }
        eventos = pasadas._contacto_a_eventos(datos, 70.0, partido, cfg, {12}, {4})
        perdida = next(e for e in eventos if e.accion == "Pelota perdida")
        assert perdida.resultado == "knock-on"
        assert perdida.jugador_numero == 12

    def test_la_confianza_del_tackle_es_la_menor_de_las_dos(self, partido, cfg):
        """Ver el tackle y saber quién fue son dos cosas distintas."""
        datos = {
            "portador": 10, "portador_equipo": "rival",
            "tackleadores": [6], "avance_portador": "se_detiene",
            "confianza": 0.95, "confianza_identificacion": 0.3,
        }
        eventos = pasadas._contacto_a_eventos(datos, 100.0, partido, cfg, {6}, {10})
        tackle = next(e for e in eventos if e.accion == "Tackle")
        assert tackle.confianza == 0.3


class TestContactoDudoso:
    def test_un_tackle_sin_jugador_es_dudoso(self, cfg):
        e = Evento(t_segundos=1, accion="Tackle", confianza=0.95)
        assert pasadas._es_contacto_dudoso(e, cfg)

    def test_un_tackle_con_poca_confianza_es_dudoso(self, cfg):
        e = Evento(t_segundos=1, accion="Tackle", jugador_numero=7, confianza=0.5)
        assert pasadas._es_contacto_dudoso(e, cfg)

    def test_un_tackle_claro_no_es_dudoso(self, cfg):
        e = Evento(t_segundos=1, accion="Tackle", jugador_numero=7, confianza=0.95)
        assert not pasadas._es_contacto_dudoso(e, cfg)

    def test_un_try_no_pasa_por_el_re_examen_de_contactos(self, cfg):
        """El re-examen a 10 fps es caro: solo para contactos."""
        e = Evento(t_segundos=1, accion="Try", jugador_numero=None, confianza=0.5)
        assert not pasadas._es_contacto_dudoso(e, cfg)


class TestFichasVigentes:
    def test_la_pasada_de_fichas_corta_cuando_ya_tiene_a_todos(
        self, db, partido_cargado, cfg
    ):
        """No tiene sentido seguir mirando video si ya están todos fichados."""
        cliente = ClienteSimulado()
        jugadores = db.plantel(partido_cargado.id, "Club A")
        previas = {
            j.numero: Ficha(
                jugador_numero=j.numero, equipo="Club A", t_segundos=0,
                rasgos={"casco": "no"}, confianza=1.0, confirmada=True,
            )
            for j in jugadores
        }
        nuevas = pasadas.pasada_fichas(
            cliente, partido_cargado, jugadores,
            partido_cargado.equipo_propio, cfg, None, previas,
        )
        assert nuevas == []
        assert cliente.contadores.pedidos == 0
