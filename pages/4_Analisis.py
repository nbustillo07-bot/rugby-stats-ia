"""Correr el análisis: las tres velocidades.

Se puede correr todo de una o paso por paso. Cada paso queda guardado, así que
si se agota la cuota del día, al volver arranca donde quedó.
"""

from __future__ import annotations

import streamlit as st

from rugby_stats import ui
from rugby_stats.analisis import pipeline

st.set_page_config(page_title="Análisis · rugby-stats-ia", page_icon="🏉", layout="wide")
ui.barra_lateral()
partido = ui.exigir_partido()
base = ui.db()
cfg = ui.config()

ui.encabezado(
    "Análisis",
    "Una pasada gruesa para separar juego de tiempo muerto, una pasada de "
    "juego para encontrar las acciones, y un re-examen del contacto a muchos "
    "cuadros por segundo.",
)

propios = base.plantel(partido.id, partido.equipo_propio.nombre)
if not propios:
    st.warning("Falta el plantel del equipo propio. Andá a **Plantel**.")
    st.stop()

# -- qué tan caro sale -----------------------------------------------------

duracion = partido.video.duracion_s
desde = partido.analizar_desde_s
hasta = partido.analizar_hasta_s or duracion
with st.expander("Cuánto video se va a analizar y qué cuesta", expanded=True):
    if hasta is None:
        st.warning(
            "No sé cuánto dura el video. Poné el tramo a analizar en "
            "**Partido**, o instalá yt-dlp/ffmpeg para que lo averigüe solo."
        )
    else:
        largo = hasta - desde
        st.markdown(
            f"Tramo: **{ui.mm_ss(desde)} a {ui.mm_ss(hasta)}** "
            f"({largo / 60:.1f} minutos)."
        )
        # Aproximado: resolución baja ≈ 100 tokens por segundo de video,
        # alta ≈ 300. La pasada gruesa y la de juego van en baja; el
        # re-examen de contactos, en alta pero sobre pocos segundos.
        gruesa = largo * 100
        juego = largo * 0.35 * 100  # solo el juego real, ~1/3 del total
        contactos = largo * 0.35 / 25 * 4 * 300  # un contacto cada ~25 s, 4 s cada uno
        total = gruesa + juego + contactos
        st.caption(
            f"Estimación muy gruesa de tokens de entrada: {total / 1_000_000:.1f}M "
            "en total. Es una cuenta de servilleta sobre los 100 tokens por "
            "segundo de video en resolución baja (300 en alta), no una medición: "
            "el número real lo ves en Google AI Studio."
        )
        if largo > 1200:
            st.info(
                "Para la primera corrida conviene un tramo de cinco o diez "
                "minutos: alcanza para medir la precisión y no te comés la "
                "cuota del día."
            )

# -- correr ----------------------------------------------------------------

st.divider()
st.markdown("### Qué correr")

NOMBRES = pipeline.NOMBRES_PASOS
hechos = {p["paso"]: p for p in pipeline.estado(base, partido.id)}

elegidos: list[str] = []
cols = st.columns(2)
for i, paso in enumerate(pipeline.PASOS):
    col = cols[i % 2]
    estado = hechos[paso]["estado"]
    icono = {"listo": "✅", "corriendo": "⏳"}.get(estado, "⬜")
    detalle = hechos[paso]["detalle"]
    if col.checkbox(
        f"{icono} {NOMBRES[paso]}",
        value=estado != "listo",
        key=f"paso_{paso}",
        help=detalle or None,
    ):
        elegidos.append(paso)

col1, col2 = st.columns([1, 3])
forzar = col2.toggle(
    "Rehacer los pasos ya terminados",
    value=False,
    help="Por defecto los pasos listos se saltean. Con esto se vuelven a "
         "correr; la caché del cliente igual evita repetir pedidos idénticos.",
)

if col1.button("Correr análisis", type="primary", disabled=not elegidos):
    cliente = ui.cliente()
    barra = st.progress(0.0, text="Arrancando…")
    log = st.empty()

    def avance(texto: str, fraccion: float) -> None:
        barra.progress(min(1.0, max(0.0, fraccion)), text=texto)

    try:
        with st.spinner("Analizando…"):
            res = pipeline.correr(
                base, cliente, partido.id, cfg,
                pasos=elegidos, forzar=forzar, progreso=avance,
            )
    except Exception as exc:  # noqa: BLE001
        barra.empty()
        st.error(f"Se cortó: {exc}")
        st.caption(
            "Lo que alcanzó a hacer quedó guardado: podés volver a apretar "
            "**Correr análisis** y sigue desde ahí."
        )
    else:
        barra.progress(1.0, text="Listo")
        if res.aviso:
            st.warning(res.aviso)
        st.success(res.resumen())
        if res.contadores:
            st.caption(res.contadores.resumen())
        if res.pasos_salteados:
            st.caption(
                "Salteados (ya estaban listos o no los elegiste): "
                + ", ".join(NOMBRES[p] for p in res.pasos_salteados if p in NOMBRES)
            )
        if res.eventos_pendientes:
            st.info(
                f"Quedaron {res.eventos_pendientes} eventos sin clasificar. "
                "No se cuentan en las stats hasta que los resuelvas en "
                "**Revisión**."
            )

# -- lo que hay ------------------------------------------------------------

st.divider()
tramos = base.tramos(partido.id)
eventos = base.eventos(partido.id)
fichas = base.fichas(partido.id, partido.equipo_propio.nombre)

if tramos:
    juego_s = sum(t.duracion_s for t in tramos if t.tipo == "juego")
    total_s = sum(t.duracion_s for t in tramos) or 1
    ui.metrica_fila(
        {
            "Tramos": len(tramos),
            "Juego real": f"{juego_s / 60:.1f} min",
            "Del total": f"{100 * juego_s / total_s:.0f}%",
            "Eventos": len(eventos),
            "Fichas": len({f.jugador_numero for f in fichas}),
        }
    )
    st.caption(
        "El porcentaje de juego real es lo que hace barato el resto: las "
        "pasadas finas corren solo sobre esos tramos."
    )

if eventos:
    import pandas as pd

    st.markdown("### Eventos detectados")
    df = pd.DataFrame(
        [
            {
                "Reloj": e.reloj,
                "Equipo": e.equipo,
                "N.º": e.jugador_numero,
                "Acción": e.accion,
                "Resultado": e.resultado,
                "Metros": e.metros,
                "Confianza": round(e.confianza, 2),
                "Fuente": e.fuente,
                "Revisado": "sí" if e.revisado else "no",
                "Pendiente": "sí" if e.pendiente else "no",
                "Nota": e.nota,
            }
            for e in eventos
        ]
    )
    st.dataframe(df, width="stretch", hide_index=True, height=360)
    st.caption(
        "Esta tabla es lo que propuso el modelo. Nada de esto está medido "
        "hasta que lo confirmes en **Revisión**."
    )
