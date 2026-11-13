"""Cargar el plantel: números de camiseta y nombres.

Es el paso que fija todo lo demás. Los números que se carguen acá son los
únicos válidos: si el modelo devuelve otro, el evento se marca pendiente en
lugar de contarse.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from rugby_stats import plantel as mod_plantel
from rugby_stats import ui
from rugby_stats.modelos import Jugador

st.set_page_config(page_title="Plantel · rugby-stats-ia", page_icon="🏉", layout="wide")
ui.barra_lateral()
partido = ui.exigir_partido()
base = ui.db()

ui.encabezado(
    "Plantel",
    "Número de camiseta y nombre. El apodo no es un adorno: el relato usa más "
    "los apodos que los apellidos, y se cruza con el video.",
)

equipos = {
    f"{partido.equipo_propio.nombre} (propio)": partido.equipo_propio.nombre,
    f"{partido.equipo_rival.nombre} (rival)": partido.equipo_rival.nombre,
}
etiqueta = st.radio("Equipo", list(equipos), horizontal=True)
equipo = equipos[etiqueta]
es_propio = equipo == partido.equipo_propio.nombre

if not es_propio:
    st.info(
        "El plantel del rival es opcional. Cargalo si querés stats del rival o "
        "si el relato lo nombra seguido; si no, con el propio alcanza."
    )

actual = base.plantel(partido.id, equipo)

tab_tabla, tab_csv = st.tabs(["Cargar a mano", "Importar un CSV"])

with tab_tabla:
    st.caption(
        "Editá la tabla y apretá **Guardar**. Las filas sin nombre se ignoran. "
        "Del 1 al 15 son titulares, del 16 al 23 suplentes."
    )
    if actual:
        datos = [
            {
                "N.º": j.numero,
                "Nombre": j.nombre,
                "Apodo": j.apodo,
                "Puesto": j.puesto,
                "Titular": j.titular,
            }
            for j in actual
        ]
    else:
        datos = [
            {
                "N.º": j.numero,
                "Nombre": "",
                "Apodo": "",
                "Puesto": j.puesto,
                "Titular": j.titular,
            }
            for j in mod_plantel.plantel_vacio(equipo)
        ]

    editada = st.data_editor(
        pd.DataFrame(datos),
        num_rows="dynamic",
        width="stretch",
        hide_index=True,
        column_config={
            "N.º": st.column_config.NumberColumn(
                "N.º", min_value=1, max_value=99, step=1, width="small", required=True
            ),
            "Nombre": st.column_config.TextColumn("Nombre", width="medium"),
            "Apodo": st.column_config.TextColumn(
                "Apodo", width="small", help="Como lo nombra el relator."
            ),
            "Puesto": st.column_config.TextColumn("Puesto", width="medium"),
            "Titular": st.column_config.CheckboxColumn("Titular", width="small"),
        },
        key=f"editor_{equipo}",
    )

    col1, col2 = st.columns([1, 4])
    if col1.button("Guardar plantel", type="primary"):
        jugadores: list[Jugador] = []
        vistos: set[int] = set()
        problemas: list[str] = []
        for _, fila in editada.iterrows():
            nombre = str(fila.get("Nombre") or "").strip()
            if not nombre:
                continue
            try:
                numero = int(fila["N.º"])
            except (TypeError, ValueError):
                problemas.append(f"{nombre}: el número no se entiende")
                continue
            if numero in vistos:
                problemas.append(f"{nombre}: el número {numero} está repetido")
                continue
            vistos.add(numero)
            jugadores.append(
                Jugador(
                    numero=numero,
                    nombre=nombre,
                    equipo=equipo,
                    apodo=str(fila.get("Apodo") or "").strip(),
                    puesto=str(fila.get("Puesto") or "").strip(),
                    titular=bool(fila.get("Titular", numero <= 15)),
                )
            )
        if not jugadores:
            st.error("No hay ningún jugador con nombre.")
        else:
            base.guardar_plantel(partido.id, jugadores)
            for p in problemas:
                st.warning(p)
            st.success(f"Guardados {len(jugadores)} jugadores.")
            for aviso in mod_plantel.validar(jugadores):
                st.info(aviso)
            st.rerun()

    if actual:
        col2.download_button(
            "Bajar como CSV",
            data=mod_plantel.a_csv(actual),
            file_name=f"plantel_{equipo.replace(' ', '_')}.csv",
            mime="text/csv",
        )

with tab_csv:
    st.caption(
        "Hace falta una columna de número y una de nombre. El resto es "
        "opcional, el orden no importa y los nombres de columna pueden ir con "
        "o sin tilde."
    )
    st.code(
        "numero,nombre,apodo,puesto,titular\n"
        "9,Nombre Apellido,Apodo,medio scrum,si\n"
        "10,Otro Nombre,,apertura,si\n"
        "17,Un Suplente,,hooker,no",
        language="csv",
    )
    subido = st.file_uploader("Elegí el CSV", type=["csv", "txt"])
    if subido is not None:
        try:
            jugadores = mod_plantel.desde_csv(subido.getvalue(), equipo)
            problemas: list[str] = []
        except mod_plantel.AvisoPlantel as aviso:
            jugadores, problemas = aviso.jugadores, aviso.problemas
        except mod_plantel.ErrorPlantel as exc:
            st.error(str(exc))
            jugadores, problemas = [], []

        if jugadores:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "N.º": j.numero,
                            "Nombre": j.nombre,
                            "Apodo": j.apodo,
                            "Puesto": j.puesto,
                            "Titular": "sí" if j.titular else "no",
                        }
                        for j in jugadores
                    ]
                ),
                width="stretch",
                hide_index=True,
            )
            for p in problemas:
                st.warning(p)
            if st.button(
                f"Importar estos {len(jugadores)} jugadores", type="primary"
            ):
                base.guardar_plantel(partido.id, jugadores)
                st.success("Importado.")
                st.rerun()

# -- estado ----------------------------------------------------------------

st.divider()
propios = base.plantel(partido.id, partido.equipo_propio.nombre)
rivales = base.plantel(partido.id, partido.equipo_rival.nombre)
ui.metrica_fila(
    {
        f"{partido.equipo_propio.nombre}": len(propios),
        f"{partido.equipo_rival.nombre}": len(rivales),
        "Titulares propios": len([j for j in propios if j.titular]),
    }
)

if propios:
    for aviso in mod_plantel.validar(propios):
        st.info(aviso)
    st.success("Siguiente paso: **Fichas**.")
