"""Exportar el Excel.

La hoja de jugadores trae fórmulas, no números pegados: si se corrige una fila
en la hoja de eventos, las stats se recalculan solas.
"""

from __future__ import annotations

import re
from datetime import datetime

import pandas as pd
import streamlit as st

from rugby_stats import excel, precision as mod_precision, ui
from rugby_stats.analisis import reglas
from rugby_stats.stats import por_jugador, totales

st.set_page_config(page_title="Exportar · rugby-stats-ia", page_icon="🏉", layout="wide")
ui.barra_lateral()
partido = ui.exigir_partido()
base = ui.db()
cfg = ui.config()

ui.encabezado("Exportar", "El Excel de stats por jugador.")

eventos = base.eventos(partido.id)
if not eventos:
    st.info("Todavía no hay eventos. Corré el **Análisis**.")
    st.stop()

propios = base.plantel(partido.id, partido.equipo_propio.nombre)
rivales = base.plantel(partido.id, partido.equipo_rival.nombre)
jugadores = propios + rivales

pendientes = [e for e in eventos if e.pendiente]
sin_revisar = [e for e in eventos if not e.revisado]

if pendientes:
    st.warning(
        f"Hay {len(pendientes)} eventos sin clasificar. **No se cuentan** en "
        "las stats (quedan en rojo en la hoja de eventos, para que los puedas "
        "resolver a mano en el Excel)."
    )
if sin_revisar:
    st.info(
        f"Hay {len(sin_revisar)} eventos sin confirmar por una persona. Van a "
        "la planilla, pero la precisión de esos es la del modelo, no la tuya."
    )

# -- vista previa ----------------------------------------------------------

st.markdown("### Vista previa")
solo_revisados = st.toggle(
    "Contar solo los eventos que confirmé",
    value=False,
    help="La vista honesta: deja afuera todo lo que el modelo propuso y vos "
         "todavía no miraste.",
)

filas = por_jugador(propios, eventos, cfg, solo_revisados=solo_revisados)
df = pd.DataFrame([f.como_dict() for f in filas])
st.dataframe(df, width="stretch", hide_index=True, height=420)

tot = totales(filas)
destacados = {
    k: tot[k]
    for k in ("Tackles", "Tackles positivos", "Carries", "Metros ganados (est.)")
    if k in tot
}
destacados["Puntos"] = tot.get("Puntos", 0)
ui.metrica_fila(destacados)

st.caption(reglas.descripcion_criterio(cfg))
st.caption(
    "Los metros ganados son **estimados**: la cámara panea y hace zoom, así "
    "que salen de las líneas de la cancha y son un orden de magnitud."
)

# -- generar ---------------------------------------------------------------

st.divider()
st.markdown("### Generar el archivo")

col1, col2 = st.columns([1, 2])
incluir_rival = col2.toggle(
    "Incluir también una fila por jugador del rival",
    value=cfg.jugadores_de == "ambos",
)
informe = mod_precision.calcular(base, partido.id, cfg)
simulado = any(
    (e.nota or "").find("simulado") >= 0 or (e.propuesta_original or {}).get("simulado")
    for e in eventos
) or st.session_state.get("forzar_simulado", False)

if simulado:
    st.error(
        "Estos datos salieron del **modo simulado**: son inventados. El Excel "
        "va a salir con el aviso adelante."
    )

if col1.button("Generar el Excel", type="primary"):
    slug = re.sub(r"[^\w\-]+", "_", partido.nombre).strip("_").lower()[:60]
    nombre = f"{slug}_{datetime.now():%Y%m%d_%H%M}.xlsx"
    ruta = cfg.dir_salidas / nombre
    if incluir_rival:
        cfg._d.setdefault("salida", {})["jugadores_de"] = "ambos"
    try:
        excel.exportar(
            ruta, partido, jugadores, eventos, cfg,
            simulado=simulado,
            precision=informe.como_dict() if informe.total_revisados else None,
        )
    except Exception as exc:  # noqa: BLE001
        st.error(f"No pude generar el Excel: {exc}")
    else:
        st.success(f"Listo: {ruta.name}")
        with open(ruta, "rb") as f:
            st.download_button(
                "Bajar el Excel",
                data=f.read(),
                file_name=nombre,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
            )
        st.caption(f"También quedó guardado en `{ruta}`.")

st.divider()
with st.expander("Qué trae el Excel"):
    st.markdown(
        """
**Hoja Jugadores** — una fila por jugador, con **fórmulas** que cuentan desde
la hoja Eventos. Si corregís o borrás una fila de Eventos, las stats se
recalculan solas. Los puntos por try, conversión, penal y drop son celdas
editables en la hoja Notas.

**Hoja Eventos** — una fila por acción, con la confianza, de dónde salió el
dato y si lo revisó una persona. Las filas pendientes van en rojo y las
fórmulas las excluyen.

**Hoja Notas** — qué está medido, qué es estimado y qué es calculado; los
criterios usados (incluido el de tackle positivo); el estado de la revisión; y
la precisión medida por tipo de evento, si ya revisaste algo.
"""
    )
