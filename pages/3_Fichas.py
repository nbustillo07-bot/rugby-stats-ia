"""Fichas de apariencia: cómo se ve cada jugador en este partido.

En el contacto el número de la espalda casi nunca se ve. Lo que se ve es el
casco, la vincha, las vendas y el color de los botines. Estas fichas son lo
que permite identificar sin el número, así que una ficha mal confirmada
arrastra el error a todos los eventos de ese jugador: vale el rato que lleva
revisarlas.
"""

from __future__ import annotations

import streamlit as st

from pathlib import Path

from rugby_stats import revision, ui
from rugby_stats.analisis import pipeline
from rugby_stats.gemini import video as video_mod
from rugby_stats.gemini.cliente import CupoDiarioAgotado
from rugby_stats.proveedor_claude import SinSaldo, TopeDeGastoAlcanzado
from rugby_stats.modelos import RASGOS

st.set_page_config(page_title="Fichas · rugby-stats-ia", page_icon="🏉", layout="wide")
ui.barra_lateral()
partido = ui.exigir_partido()
base = ui.db()
cfg = ui.config()

ui.encabezado(
    "Fichas de apariencia",
    "El sistema busca cuadros donde se lea el número en la espalda y anota "
    "cómo se ve el jugador. Vos confirmás o corregís.",
)

equipos = {
    f"{partido.equipo_propio.nombre} (propio)": partido.equipo_propio,
    f"{partido.equipo_rival.nombre} (rival)": partido.equipo_rival,
}
etiqueta = st.radio("Equipo", list(equipos), horizontal=True)
equipo = equipos[etiqueta]

jugadores = base.plantel(partido.id, equipo.nombre)
if not jugadores:
    st.warning(f"No hay plantel cargado para {equipo.nombre}. Andá a **Plantel**.")
    st.stop()

if equipo.numero_legible == "alta":
    st.info(
        "Marcaste que el número de esta camiseta se lee bien. Las fichas "
        "ayudan igual, pero no son imprescindibles."
    )

# -- correr la pasada ------------------------------------------------------

col1, col2, col3 = st.columns([1, 1, 2])
if col1.button("Buscar fichas", type="primary"):
    cliente = ui.cliente()
    tramos_muertos = [t for t in base.tramos(partido.id) if t.tipo == "muerto"]
    if not tramos_muertos:
        st.caption(
            "Todavía no corriste la pasada gruesa, así que busco por todo el "
            "video. Si la corrés primero, esto va a mirar directo las pausas y "
            "sale más rápido y más barato."
        )
    barra = st.progress(0.0, text="Preparando el video…")

    def avance(texto: str, fraccion: float) -> None:
        barra.progress(min(1.0, max(0.0, fraccion)), text=texto)

    try:
        # Por el pipeline: baja solo el tramo que hace falta, cuenta la plata
        # contra el tope y recorta las fotos de cada jugador.
        res = pipeline.correr(
            base, cliente, partido.id, cfg,
            pasos=["fichas"], forzar=True, progreso=avance,
        )
        barra.progress(1.0, text="Listo")
        st.success(res.resumen())
        st.rerun()
    except (CupoDiarioAgotado, TopeDeGastoAlcanzado) as exc:
        barra.empty()
        st.warning(str(exc))
    except SinSaldo as exc:
        barra.empty()
        st.error(str(exc))
    except Exception as exc:  # noqa: BLE001 - se muestra el error, no se cae la app
        barra.empty()
        st.error(f"No pude completar la pasada: {exc}")

if col2.button("Borrar las fichas de este equipo"):
    for f in base.fichas(partido.id, equipo.nombre):
        base.borrar_ficha(f.id)
    st.rerun()

filas = revision.fichas_para_confirmar(base, partido.id, equipo.nombre)
con_ficha = [f for f in filas if not f["sin_ficha"]]
confirmadas = [f for f in filas if f["confirmada"]]
col3.caption(
    f"{len(con_ficha)} de {len(filas)} jugadores con ficha · "
    f"{len(confirmadas)} confirmadas"
)

if not con_ficha:
    st.info("Todavía no hay fichas. Apretá **Buscar fichas**.")
    st.stop()

st.divider()
st.caption(
    "Confirmá lo que esté bien y corregí lo que no. Si la ficha no se "
    "corresponde con el jugador, borrá la lectura: es mejor no tener ficha que "
    "tener una equivocada."
)

solo_sin_confirmar = st.toggle("Mostrar solo las que faltan confirmar", value=True)

for item in filas:
    j = item["jugador"]
    if item["sin_ficha"]:
        continue
    if solo_sin_confirmar and item["confirmada"]:
        continue
    mejor = item["mejor"]

    with st.expander(
        f"{'✅ ' if item['confirmada'] else ''}{j.etiqueta} — "
        f"{len(item['lecturas'])} lectura(s), mejor confianza "
        f"{mejor.confianza:.2f}",
        expanded=not item["confirmada"],
    ):
        cola, colb = st.columns([2, 3])

        with cola:
            st.markdown(f"**Leído en {ui.mm_ss(mejor.t_segundos)}**")
            if mejor.imagen and Path(mejor.imagen).exists():
                st.image(
                    mejor.imagen,
                    caption="Así lo va a buscar en el contacto",
                    width=180,
                )
            if partido.video.tipo == "youtube":
                st.video(
                    partido.video.referencia,
                    start_time=int(max(0, mejor.t_segundos - 2)),
                )
                st.caption(
                    f"[Abrir en YouTube en ese momento]"
                    f"({video_mod.url_youtube_con_tiempo(partido.video.referencia, mejor.t_segundos - 2)})"
                )
            elif partido.video.ruta_local:
                st.caption(f"Momento {ui.mm_ss(mejor.t_segundos)} del archivo.")
            if mejor.nota:
                st.caption(f"Nota del modelo: {mejor.nota}")

        with colb:
            nuevos: dict[str, str] = {}
            for rasgo in RASGOS:
                nuevos[rasgo] = st.text_input(
                    rasgo.replace("_", " ").capitalize(),
                    value=mejor.rasgos.get(rasgo, ""),
                    key=f"r_{mejor.id}_{rasgo}",
                    placeholder="(no se ve)",
                )

            c1, c2, c3 = st.columns(3)
            if c1.button("Confirmar", key=f"ok_{mejor.id}", type="primary"):
                base.confirmar_ficha(
                    mejor.id, {k: v.strip() for k, v in nuevos.items() if v.strip()}
                )
                st.rerun()
            if c2.button("Borrar lectura", key=f"del_{mejor.id}"):
                base.borrar_ficha(mejor.id)
                st.rerun()
            if len(item["lecturas"]) > 1:
                c3.caption(
                    "Otras lecturas en: "
                    + ", ".join(
                        ui.mm_ss(f.t_segundos) for f in item["lecturas"][1:5]
                    )
                )

sin_ficha = [f["jugador"] for f in filas if f["sin_ficha"]]
if sin_ficha:
    st.divider()
    st.warning(
        "Sin ficha todavía: "
        + ", ".join(f"{j.numero} ({j.nombre})" for j in sin_ficha)
    )
    st.caption(
        "Para estos, el sistema solo va a poder identificarlos cuando se les "
        "lea el número. Si son muchos, probá correr la pasada gruesa primero: "
        "las fichas se buscan en las pausas, que es donde se ven los números."
    )
