"""La cola de revisión y la medición de precisión.

Son las dos piezas que convierten propuestas en datos: sin revisión no hay
medición, y sin medición no se sabe si el sistema sirve.
"""

from __future__ import annotations

from rugby_stats import precision as mod_precision
from rugby_stats import revision
from rugby_stats.modelos import Evento


class TestColaDeRevision:
    def test_las_tarjetas_salen_en_orden_de_duda(self, db, partido_cargado, cfg):
        pid = partido_cargado.id
        db.guardar_evento(pid, Evento(
            t_segundos=10, accion="Tackle", resultado="positivo",
            equipo="Club A", jugador_numero=7, confianza=0.95,
        ))
        db.guardar_evento(pid, Evento(
            t_segundos=20, accion="Tackle", resultado="",
            equipo="Club A", jugador_numero=None, confianza=0.4, pendiente=True,
        ))
        tarjetas = revision.armar_cola(db, pid, cfg)
        assert tarjetas[0].evento.pendiente is True
        assert "sin jugador" in tarjetas[0].motivo

    def test_la_tarjeta_centra_el_clip_en_el_evento(self, db, partido_cargado, cfg):
        pid = partido_cargado.id
        db.guardar_evento(pid, Evento(
            t_segundos=100, accion="Tackle", equipo="Club A", confianza=0.5,
        ))
        t = revision.armar_cola(db, pid, cfg)[0]
        largo = t.fin_clip_s - t.inicio_clip_s
        assert largo == cfg.clip_revision_s
        assert t.inicio_clip_s < 100 < t.fin_clip_s

    def test_la_tarjeta_ofrece_solo_jugadores_de_ese_equipo(
        self, db, partido_cargado, cfg
    ):
        pid = partido_cargado.id
        db.guardar_evento(pid, Evento(
            t_segundos=10, accion="Tackle", equipo="Club A", confianza=0.5,
        ))
        t = revision.armar_cola(db, pid, cfg)[0]
        assert all(j.equipo == "Club A" for j in t.opciones_jugador)
        assert len(t.opciones_jugador) == 23

    def test_la_tarjeta_ofrece_solo_resultados_de_esa_accion(
        self, db, partido_cargado, cfg
    ):
        pid = partido_cargado.id
        db.guardar_evento(pid, Evento(
            t_segundos=10, accion="Tackle", equipo="Club A", confianza=0.5,
        ))
        t = revision.armar_cola(db, pid, cfg)[0]
        assert set(t.opciones_resultado) == {"positivo", "negativo", "neutro"}

    def test_con_youtube_arma_el_embed(self, db, partido_cargado, cfg):
        pid = partido_cargado.id
        db.guardar_evento(pid, Evento(
            t_segundos=100, accion="Tackle", equipo="Club A", confianza=0.5,
        ))
        t = revision.armar_cola(db, pid, cfg)[0]
        assert t.embed_url and "start=" in t.embed_url

    def test_el_titulo_dice_quien_o_que_no_se_sabe(self, db, partido_cargado, cfg):
        pid = partido_cargado.id
        db.guardar_evento(pid, Evento(
            t_segundos=10, accion="Tackle", equipo="Club A",
            jugador_numero=None, confianza=0.3,
        ))
        assert "sin identificar" in revision.armar_cola(db, pid, cfg)[0].titulo


class TestConfirmarYCorregir:
    def test_confirmar_no_genera_correcciones(self, db, partido_cargado, cfg):
        """Confirmar sin cambios es lo que cuenta como acierto del modelo."""
        pid = partido_cargado.id
        eid = db.guardar_evento(pid, Evento(
            t_segundos=10, accion="Tackle", resultado="positivo",
            equipo="Club A", jugador_numero=7, confianza=0.9,
        ))
        revision.confirmar(db, pid, db.evento(eid))
        e = db.evento(eid)
        assert e.revisado and e.confianza == 1.0
        assert db.correcciones(pid) == []

    def test_corregir_registra_cada_campo_que_cambio(self, db, partido_cargado, cfg):
        pid = partido_cargado.id
        eid = db.guardar_evento(pid, Evento(
            t_segundos=10, accion="Tackle", resultado="positivo",
            equipo="Club A", jugador_numero=23, confianza=0.6,
        ))
        revision.corregir(
            db, pid, db.evento(eid),
            {"jugador_numero": 13, "resultado": "negativo"}, cfg,
        )
        campos = {c.campo: c for c in db.correcciones(pid)}
        assert campos["jugador_numero"].valor_modelo == "23"
        assert campos["jugador_numero"].valor_persona == "13"
        assert campos["resultado"].valor_persona == "negativo"

    def test_corregir_no_registra_lo_que_no_cambio(self, db, partido_cargado, cfg):
        pid = partido_cargado.id
        eid = db.guardar_evento(pid, Evento(
            t_segundos=10, accion="Tackle", resultado="positivo",
            equipo="Club A", jugador_numero=7, confianza=0.6,
        ))
        revision.corregir(
            db, pid, db.evento(eid),
            {"jugador_numero": 7, "resultado": "negativo"}, cfg,
        )
        assert [c.campo for c in db.correcciones(pid)] == ["resultado"]

    def test_corregir_un_pendiente_lo_completa(self, db, partido_cargado, cfg):
        """Poner el jugador que faltaba saca el evento de pendiente."""
        pid = partido_cargado.id
        eid = db.guardar_evento(pid, Evento(
            t_segundos=10, accion="Tackle", resultado="positivo",
            equipo="Club A", jugador_numero=None, confianza=0.3, pendiente=True,
        ))
        revision.corregir(db, pid, db.evento(eid), {"jugador_numero": 6}, cfg)
        e = db.evento(eid)
        assert e.pendiente is False
        assert e.revisado is True
        assert e.jugador_numero == 6

    def test_corregir_con_un_numero_invalido_lo_deja_pendiente(
        self, db, partido_cargado, cfg
    ):
        pid = partido_cargado.id
        eid = db.guardar_evento(pid, Evento(
            t_segundos=10, accion="Tackle", resultado="positivo",
            equipo="Club A", jugador_numero=None, confianza=0.3, pendiente=True,
        ))
        revision.corregir(db, pid, db.evento(eid), {"jugador_numero": 99}, cfg)
        assert db.evento(eid).pendiente is True

    def test_descartar_saca_el_evento_de_las_cuentas(self, db, partido_cargado, cfg):
        pid = partido_cargado.id
        eid = db.guardar_evento(pid, Evento(
            t_segundos=10, accion="Tackle", resultado="positivo",
            equipo="Club A", jugador_numero=7, confianza=0.5,
        ))
        revision.descartar(db, pid, db.evento(eid), "era un knock-on")
        e = db.evento(eid)
        assert e.revisado is True
        assert e.pendiente is True  # pendiente = no entra a las fórmulas
        assert "descartado" in e.nota
        assert db.correcciones(pid)[0].campo == "descartado"

    def test_agregar_a_mano(self, db, partido_cargado, cfg):
        pid = partido_cargado.id
        eid = revision.agregar_a_mano(db, pid, Evento(
            t_segundos=200, accion="Try", resultado="convertido",
            equipo="Club A", jugador_numero=11,
        ))
        e = db.evento(eid)
        assert e.fuente == "manual"
        assert e.revisado is True
        assert e.confianza == 1.0


class TestFichasParaConfirmar:
    def test_lista_todos_los_jugadores_con_y_sin_ficha(self, db, partido_cargado):
        from rugby_stats.modelos import Ficha

        pid = partido_cargado.id
        db.guardar_ficha(pid, Ficha(
            jugador_numero=4, equipo="Club A", t_segundos=50,
            rasgos={"casco": "sí"}, confianza=0.8,
        ))
        filas = revision.fichas_para_confirmar(db, pid, "Club A")
        assert len(filas) == 23
        con_ficha = [f for f in filas if not f["sin_ficha"]]
        assert len(con_ficha) == 1
        assert con_ficha[0]["jugador"].numero == 4

    def test_la_mejor_lectura_es_la_de_mas_confianza(self, db, partido_cargado):
        from rugby_stats.modelos import Ficha

        pid = partido_cargado.id
        db.guardar_ficha(pid, Ficha(
            jugador_numero=4, equipo="Club A", t_segundos=50,
            rasgos={"casco": "no"}, confianza=0.5,
        ))
        db.guardar_ficha(pid, Ficha(
            jugador_numero=4, equipo="Club A", t_segundos=80,
            rasgos={"casco": "sí, oscuro"}, confianza=0.95,
        ))
        fila = next(
            f for f in revision.fichas_para_confirmar(db, pid, "Club A")
            if f["jugador"].numero == 4
        )
        assert fila["mejor"].confianza == 0.95
        assert len(fila["lecturas"]) == 2


class TestPrecision:
    def _eventos(self, cuantos: int, accion: str = "Tackle") -> list[Evento]:
        return [
            Evento(
                id=i, t_segundos=i * 10, accion=accion, resultado="positivo",
                equipo="Club A", jugador_numero=7, confianza=0.9, revisado=True,
            )
            for i in range(1, cuantos + 1)
        ]

    def test_sin_revisar_nada_no_hay_precision(self, cfg):
        eventos = [
            Evento(id=1, t_segundos=10, accion="Tackle", revisado=False),
        ]
        informe = mod_precision.calcular_desde(eventos, [], cfg)
        assert informe.total_revisados == 0
        assert "no hay precisión medida" in informe.resumen(0.95)

    def test_todo_confirmado_es_cien_por_ciento(self, cfg):
        informe = mod_precision.calcular_desde(self._eventos(12), [], cfg)
        p = informe.por_tipo[0]
        assert p.precision == 1.0
        assert p.muestra_suficiente
        assert "se puede dejar de revisar" in p.veredicto(0.95)

    def test_una_correccion_baja_la_precision(self, cfg):
        from rugby_stats.modelos import Correccion

        eventos = self._eventos(10)
        correcciones = [
            Correccion(id=1, evento_id=1, campo="jugador_numero",
                       valor_modelo="23", valor_persona="13"),
        ]
        informe = mod_precision.calcular_desde(eventos, correcciones, cfg)
        p = informe.por_tipo[0]
        assert p.precision == 0.9
        assert p.corregidos == 1
        assert p.campo_que_mas_falla == "jugador_numero"

    def test_varias_correcciones_del_mismo_evento_cuentan_una_vez(self, cfg):
        from rugby_stats.modelos import Correccion

        eventos = self._eventos(10)
        correcciones = [
            Correccion(id=1, evento_id=1, campo="jugador_numero",
                       valor_modelo="23", valor_persona="13"),
            Correccion(id=2, evento_id=1, campo="resultado",
                       valor_modelo="positivo", valor_persona="negativo"),
        ]
        informe = mod_precision.calcular_desde(eventos, correcciones, cfg)
        assert informe.por_tipo[0].corregidos == 1
        assert informe.por_tipo[0].precision == 0.9

    def test_muestra_chica_no_concluye(self, cfg):
        """Con 3 revisados, el porcentaje es ruido."""
        informe = mod_precision.calcular_desde(self._eventos(3), [], cfg)
        p = informe.por_tipo[0]
        assert not p.muestra_suficiente
        assert "muestra chica" in p.veredicto(0.95)

    def test_separa_por_tipo_de_evento(self, cfg):
        from rugby_stats.modelos import Correccion

        eventos = self._eventos(10, "Tackle") + [
            Evento(id=100 + i, t_segundos=i, accion="Carry", resultado="avanza",
                   jugador_numero=10, revisado=True)
            for i in range(10)
        ]
        correcciones = [
            Correccion(id=1, evento_id=100, campo="metros",
                       valor_modelo="20", valor_persona="8"),
        ]
        informe = mod_precision.calcular_desde(eventos, correcciones, cfg)
        por_accion = {p.accion: p for p in informe.por_tipo}
        assert por_accion["Tackle"].precision == 1.0
        assert por_accion["Carry"].precision == 0.9

    def test_los_descartados_se_cuentan_aparte(self, cfg):
        from rugby_stats.modelos import Correccion

        eventos = self._eventos(10)
        correcciones = [
            Correccion(id=1, evento_id=1, campo="descartado",
                       valor_modelo="Tackle de 7", valor_persona="no existió"),
        ]
        informe = mod_precision.calcular_desde(eventos, correcciones, cfg)
        assert informe.por_tipo[0].descartados == 1
        assert informe.por_tipo[0].corregidos == 0

    def test_dice_donde_falla_y_que_hacer(self, cfg):
        """No alcanza el porcentaje: hay que saber qué campo falla."""
        from rugby_stats.modelos import Correccion

        eventos = self._eventos(20)
        correcciones = [
            Correccion(id=i, evento_id=i, campo="jugador_numero",
                       valor_modelo="23", valor_persona="13")
            for i in range(1, 6)
        ]
        informe = mod_precision.calcular_desde(eventos, correcciones, cfg)
        sugerencias = mod_precision.donde_falla(informe)
        assert sugerencias
        texto = " ".join(sugerencias)
        assert "identificación del jugador" in texto
        assert "cuadros por segundo" in texto

    def test_avisa_cuando_el_modelo_inventa_eventos(self, cfg):
        from rugby_stats.modelos import Correccion

        eventos = self._eventos(20)
        correcciones = [
            Correccion(id=i, evento_id=i, campo="descartado",
                       valor_modelo="x", valor_persona="no existió")
            for i in range(1, 8)
        ]
        informe = mod_precision.calcular_desde(eventos, correcciones, cfg)
        texto = " ".join(mod_precision.donde_falla(informe))
        assert "inventando eventos" in texto

    def test_el_informe_se_puede_meter_en_el_excel(self, cfg):
        informe = mod_precision.calcular_desde(self._eventos(12), [], cfg)
        d = informe.como_dict()
        assert d["Tackle"]["precision"] == 1.0
        assert d["Tackle"]["revisados"] == 12
