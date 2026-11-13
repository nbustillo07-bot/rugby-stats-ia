"""rugby-stats-ia — pantalla de inicio.

Se corre con:  streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="rugby-stats-ia",
    page_icon="🏉",
    layout="wide",
    initial_sidebar_state="expanded",
)

from rugby_stats import ui  # noqa: E402  (después de set_page_config)
from rugby_stats.analisis import pipeline
from rugby_stats.stats import resumen_partido

partido = ui.barra_lateral()
cfg = ui.config()

st.title("🏉 rugby-stats-ia")
st.caption(
    "Stats por jugador a partir del video del partido. Sirve para cualquier "
    "equipo: los planteles y las camisetas se cargan por partido."
)

if partido is None:
    st.info(
        "**Primer paso:** andá a **Partido** en el menú de la izquierda y cargá "
        "los dos equipos y el video."
    )
    st.markdown(
        """
### Cómo es el flujo

1. **Partido** — los dos equipos, cómo es cada camiseta y de dónde sale el video.
2. **Plantel** — números de camiseta y nombres. A mano o por CSV. Es el paso
   que fija todo: esos son los únicos números válidos.
3. **Fichas** — el sistema busca cuadros donde se lea el número en la espalda
   y anota cómo se ve cada jugador (casco, vincha, vendas, botines). Vos
   confirmás o corregís.
4. **Análisis** — las tres velocidades: una pasada gruesa para separar juego
   de tiempo muerto, una pasada de juego para encontrar las acciones, y un
   re-examen del contacto a muchos cuadros por segundo.
5. **Revisión** — la cola de eventos dudosos, los más flojos primero. Un clip,
   una propuesta, y confirmar o corregir.
6. **Precisión** — qué porcentaje de las propuestas confirmaste sin cambios,
   por tipo de evento. Sin esta pantalla no se sabe si el sistema sirve.
7. **Exportar** — el Excel, con fórmulas que cuentan desde la hoja de eventos.
"""
    )
    st.stop()

base = ui.db()
eventos = base.eventos(partido.id)
propios = base.plantel(partido.id, partido.equipo_propio.nombre)
rivales = base.plantel(partido.id, partido.equipo_rival.nombre)
fichas = base.fichas(partido.id, partido.equipo_propio.nombre)

st.subheader(partido.nombre)
col1, col2 = st.columns([2, 1])
with col1:
    st.markdown(
        f"**{partido.equipo_propio.nombre}** (propio) vs "
        f"**{partido.equipo_rival.nombre}**"
        + (f" · {partido.competencia}" if partido.competencia else "")
        + (f" · {partido.fecha}" if partido.fecha else "")
    )
    st.caption(
        f"Video: {partido.video.tipo} — {partido.video.referencia[:80]}"
        + (
            f" · dura {ui.mm_ss(partido.video.duracion_s)}"
            if partido.video.duracion_s
            else ""
        )
    )
with col2:
    desde = partido.analizar_desde_s
    hasta = partido.analizar_hasta_s
    if hasta:
        st.info(f"Tramo a analizar: {ui.mm_ss(desde)} a {ui.mm_ss(hasta)}")
    else:
        st.info("Tramo a analizar: todo el video")

res = resumen_partido(eventos, cfg)
ui.metrica_fila(
    {
        "Jugadores propios": len([j for j in propios if j.nombre]),
        "Fichas": len({f.jugador_numero for f in fichas}),
        "Eventos": res["eventos"],
        "Pendientes": res["pendientes"],
        "Revisados": f"{res['porcentaje_revisado']:.0f}%",
    }
)

st.divider()
st.markdown("### Estado de los pasos")
for paso in pipeline.estado(base, partido.id):
    icono = {"listo": "✅", "corriendo": "⏳"}.get(paso["estado"], "⬜")
    detalle = f" — {paso['detalle']}" if paso["detalle"] else ""
    st.markdown(f"{icono} **{paso['nombre']}**{detalle}")

if not propios or not any(j.nombre for j in propios):
    st.warning(
        "Falta el plantel del equipo propio. Sin números de camiseta no hay a "
        "quién atribuirle las acciones: andá a **Plantel**."
    )
elif not fichas and partido.equipo_propio.numero_legible == "baja":
    st.warning(
        "La camiseta propia tiene el número poco legible y todavía no hay "
        "fichas. Corré **Fichas** antes de analizar, o el sistema no va a "
        "poder identificar a casi nadie."
    )
elif res["eventos"] == 0:
    st.info("Siguiente paso: **Análisis**.")
elif res["pendientes"] or res["porcentaje_revisado"] < 100:
    st.info(
        f"Siguiente paso: **Revisión** — hay {res['pendientes']} eventos "
        f"pendientes y {res['eventos'] - res['revisados']} sin confirmar."
    )
else:
    st.success("Todo revisado. Podés **Exportar** el Excel.")
