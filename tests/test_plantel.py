"""Carga del plantel: el paso que fija los números válidos de todo el sistema."""

from __future__ import annotations

import pytest

from rugby_stats import plantel as mod


class TestCSV:
    def test_csv_minimo(self):
        jugadores = mod.desde_csv("numero,nombre\n9,Juan Pérez\n10,Pedro Gómez", "X")
        assert len(jugadores) == 2
        assert jugadores[0].numero == 9
        assert jugadores[0].nombre == "Juan Pérez"
        assert jugadores[0].equipo == "X"

    def test_columnas_completas(self):
        jugadores = mod.desde_csv(
            "numero,nombre,apodo,puesto,titular\n"
            "9,Juan Pérez,Juancho,medio scrum,si\n"
            "17,Luis Díaz,,hooker,no",
            "X",
        )
        assert jugadores[0].apodo == "Juancho"
        assert jugadores[0].titular is True
        assert jugadores[1].titular is False

    @pytest.mark.parametrize(
        "cabecera",
        [
            "Número,Nombre",
            "NUMERO,JUGADOR",
            "nro,apellido",
            "camiseta,nombre y apellido",
            "#,name",
        ],
    )
    def test_acepta_alias_de_columnas(self, cabecera):
        """Con o sin tilde, en mayúsculas, y con varios nombres posibles."""
        jugadores = mod.desde_csv(f"{cabecera}\n9,Juan Pérez", "X")
        assert jugadores[0].numero == 9

    def test_orden_de_columnas_indistinto(self):
        jugadores = mod.desde_csv("nombre,apodo,numero\nJuan Pérez,Juancho,9", "X")
        assert jugadores[0].numero == 9
        assert jugadores[0].apodo == "Juancho"

    @pytest.mark.parametrize("sep", [",", ";", "\t"])
    def test_separadores(self, sep):
        texto = f"numero{sep}nombre\n9{sep}Juan Pérez"
        assert mod.desde_csv(texto, "X")[0].nombre == "Juan Pérez"

    def test_titular_por_numero_si_no_viene_la_columna(self):
        jugadores = mod.desde_csv("numero,nombre\n15,A\n16,B", "X")
        por_numero = {j.numero: j for j in jugadores}
        assert por_numero[15].titular is True
        assert por_numero[16].titular is False

    def test_puesto_por_numero_si_no_viene(self):
        jugadores = mod.desde_csv("numero,nombre\n9,A\n10,B", "X")
        assert jugadores[0].puesto == "medio scrum"
        assert jugadores[1].puesto == "apertura"

    def test_bytes_con_bom(self):
        contenido = "numero,nombre\n9,Juan Pérez".encode("utf-8-sig")
        assert mod.desde_csv(contenido, "X")[0].nombre == "Juan Pérez"

    def test_latin1(self):
        contenido = "numero,nombre\n9,Martín Gómez".encode("latin-1")
        assert mod.desde_csv(contenido, "X")[0].nombre == "Martín Gómez"

    def test_falta_una_columna_obligatoria(self):
        with pytest.raises(mod.ErrorPlantel) as exc:
            mod.desde_csv("jersey,player\n9,Juan", "X")
        # El error dice qué falta y da un ejemplo que funciona.
        assert "número" in str(exc.value)
        assert "numero,nombre" in str(exc.value)

    def test_archivo_vacio(self):
        with pytest.raises(mod.ErrorPlantel):
            mod.desde_csv("", "X")

    def test_filas_malas_se_saltean_y_se_avisa(self):
        """Se carga lo que se pudo; no se frena todo por una fila."""
        with pytest.raises(mod.AvisoPlantel) as exc:
            mod.desde_csv(
                "numero,nombre\n9,Juan\nabc,Pedro\n10,\n9,Repetido\n11,Luis", "X"
            )
        aviso = exc.value
        assert [j.numero for j in aviso.jugadores] == [9, 11]
        assert len(aviso.problemas) == 3

    def test_numero_fuera_de_rango(self):
        with pytest.raises(mod.AvisoPlantel) as exc:
            mod.desde_csv("numero,nombre\n0,A\n9,B", "X")
        assert "fuera de" in exc.value.problemas[0]

    def test_cargar_csv_no_lanza(self, tmp_path):
        f = tmp_path / "p.csv"
        f.write_text("numero,nombre\n9,Juan\nabc,Mal", encoding="utf-8")
        jugadores, problemas = mod.cargar_csv(f, "X")
        assert len(jugadores) == 1
        assert len(problemas) == 1

    def test_ida_y_vuelta(self):
        original = mod.desde_csv(
            "numero,nombre,apodo,puesto,titular\n9,Juan Pérez,Juancho,medio scrum,si",
            "X",
        )
        devuelta = mod.desde_csv(mod.a_csv(original), "X")
        assert devuelta[0].numero == original[0].numero
        assert devuelta[0].apodo == original[0].apodo
        assert devuelta[0].titular == original[0].titular

    def test_el_csv_de_ejemplo_del_repo_se_lee(self):
        from pathlib import Path

        ruta = Path(__file__).resolve().parent.parent / "data" / "ejemplos" / "plantel_ejemplo.csv"
        jugadores, problemas = mod.cargar_csv(ruta, "X")
        assert len(jugadores) == 23
        assert problemas == []


class TestValidar:
    def test_plantel_completo_sin_avisos_graves(self):
        jugadores = mod.desde_csv(
            "numero,nombre,apodo\n"
            + "\n".join(f"{n},Jugador {n},Apodo{n}" for n in range(1, 16)),
            "X",
        )
        avisos = mod.validar(jugadores)
        assert not any("titulares cargados" in a for a in avisos)

    def test_avisa_si_faltan_titulares(self):
        """El descarte (son 15 en cancha) es lo que hace funcionar la ID."""
        jugadores = mod.desde_csv("numero,nombre\n9,A\n10,B", "X")
        avisos = mod.validar(jugadores)
        assert any("titulares" in a and "descarte" in a for a in avisos)

    def test_avisa_si_no_hay_apodos(self):
        jugadores = mod.desde_csv(
            "numero,nombre\n" + "\n".join(f"{n},Jugador {n}" for n in range(1, 16)),
            "X",
        )
        assert any("apodo" in a for a in mod.validar(jugadores))

    def test_plantel_vacio(self):
        assert "ningún jugador" in mod.validar([])[0]


class TestPlantelVacio:
    def test_arma_quince_y_ocho(self):
        jugadores = mod.plantel_vacio("X")
        assert len(jugadores) == 23
        assert len([j for j in jugadores if j.titular]) == 15

    def test_los_titulares_vienen_con_puesto(self):
        por_numero = {j.numero: j for j in mod.plantel_vacio("X")}
        assert por_numero[2].puesto == "hooker"
        assert por_numero[15].puesto == "fullback"


class TestEtiquetasYRelato:
    def test_etiqueta_con_y_sin_apodo(self):
        from rugby_stats.modelos import Jugador

        con = Jugador(numero=9, nombre="Juan Pérez", equipo="X", apodo="Juancho")
        assert con.etiqueta == "9. Juan Pérez (Juancho)"
        sin = Jugador(numero=10, nombre="Pedro Gómez", equipo="X")
        assert sin.etiqueta == "10. Pedro Gómez"

    def test_nombres_para_el_relato_incluyen_apellido_y_apodo(self):
        """El relator usa el apellido o el apodo, casi nunca el nombre completo."""
        from rugby_stats.modelos import Jugador

        j = Jugador(numero=9, nombre="Juan Pérez", equipo="X", apodo="Juancho")
        formas = j.nombres_para_relato
        assert "Juan Pérez" in formas
        assert "Pérez" in formas
        assert "Juancho" in formas
