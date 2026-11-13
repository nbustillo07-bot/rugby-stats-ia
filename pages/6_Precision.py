"""La pantalla de precisión: ¿esto sirve o no?

De las propuestas que ya revisaste, qué porcentaje confirmaste sin cambiarle
nada. Eso es la precisión del modelo para cada tipo de evento. Cuando un tipo
pasa el umbral, se puede dejar de revisar.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from rugby_stats import precision as mod_precision
from rugby_stats import ui

st.set_page_config(page_title="Precisión · rugby-stats-ia", page_icon="🏉", layout="wide")
ui.barra_lateral()
partido = ui.exigir_partido()
base = ui.db()
cfg = ui.config()

ui.encabezado(
    "Precisión",
    "Sin esta pantalla no se sabe si el sistema sirve. El objetivo es 95%: un "
    "70% no alcanza.",
)

informe = mod_precision.calcular(base, partido.id, cfg)

if informe.total_eventos == 0:
    st.info("Todavía no hay eventos. Corré el **Análisis**.")
    st.stop()

if informe.total_revisados == 0:
    st.warning(informe.resumen(cfg.umbral_precision_objetivo))
    st.markdown(
        """
Hay eventos detectados pero ninguno confirmado, así que **la precisión es
desconocida**. Que una tabla de eventos "se vea bien" no es una medición:
hasta que no se corrija fila por fila, no hay número.

Andá a **Revisión** y confirmá o corregí al menos
"""
        f"{mod_precision.MUESTRA_MINIMA} eventos de cada tipo."
    )
    st.stop()

ui.metrica_fila(
    {
        "Eventos": informe.total_eventos,
        "Revisados": informe.total_revisados,
        "Del total": f"{informe.porcentaje_revisado:.0f}%",
        "Precisión global": f"{informe.precision_global * 100:.0f}%",
        "Objetivo": f"{cfg.umbral_precision_objetivo * 100:.0f}%",
    }
)

st.caption(
    "**Precisión global** es el porcentaje de propuestas que confirmaste sin "
    "tocar nada. Es la precisión del *modelo*, no la del sistema: si revisás "
    "todo, lo que sale a la planilla está al 100% y este número mide cuánto "
    "trabajo te ahorra el modelo."
)

st.divider()
st.markdown("### Por tipo de evento")

umbral = cfg.umbral_precision_objetivo
filas = []
for p in informe.por_tipo:
    filas.append(
        {
            "Tipo": p.accion,
            "Revisados": p.revisados,
            "Confirmados sin cambios": p.confirmados_sin_cambios,
            "Corregidos": p.corregidos,
            "Descartados": p.descartados,
            "Precisión": p.precision,
            "Lo que más falla": {
                "jugador_numero": "quién fue",
                "accion": "qué acción",
                "resultado": "el resultado",
                "equipo": "el equipo",
                "metros": "los metros",
                "descartado": "eventos inventados",
            }.get(p.campo_que_mas_falla, p.campo_que_mas_falla or "—"),
            "Veredicto": p.veredicto(umbral),
        }
    )

st.dataframe(
    pd.DataFrame(filas),
    width="stretch",
    hide_index=True,
    column_config={
        "Precisión": st.column_config.ProgressColumn(
            "Precisión", min_value=0.0, max_value=1.0, format="%.0f%%"
        ),
    },
)

listos = [p.accion for p in informe.por_tipo
          if p.muestra_suficiente and p.precision >= umbral]
if listos:
    st.success(
        "Pasan el umbral y se puede dejar de revisarlos: " + ", ".join(listos)
    )

flojos = [
    p for p in informe.por_tipo
    if p.muestra_suficiente and p.precision < umbral
]
if flojos:
    st.warning(
        "Siguen abajo del umbral: "
        + ", ".join(f"{p.accion} ({p.precision * 100:.0f}%)" for p in flojos)
    )

chicos = [p for p in informe.por_tipo if not p.muestra_suficiente]
if chicos:
    st.info(
        "Muestra chica, el porcentaje todavía es ruido: "
        + ", ".join(f"{p.accion} ({p.revisados})" for p in chicos)
    )

sugerencias = mod_precision.donde_falla(informe)
if sugerencias:
    st.divider()
    st.markdown("### Qué conviene mejorar")
    st.caption(
        "No alcanza con saber el porcentaje: lo que importa es si falla *que "
        "hubo un tackle* o *quién lo hizo*, porque se arreglan de maneras "
        "distintas."
    )
    for s in sugerencias:
        st.markdown(f"- {s}")

# -- detalle de las correcciones ------------------------------------------

st.divider()
with st.expander("Todas las correcciones, una por una"):
    correcciones = base.correcciones(partido.id)
    if not correcciones:
        st.caption("Confirmaste todo sin cambiar nada: no hay correcciones.")
    else:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Evento": c.evento_id,
                        "Campo": c.campo,
                        "Propuso el modelo": c.valor_modelo,
                        "Pusiste vos": c.valor_persona,
                        "Cuándo": c.momento[:19].replace("T", " "),
                    }
                    for c in correcciones
                ]
            ),
            width="stretch",
            hide_index=True,
        )
