"""La cola de revisión: un clip, una propuesta, confirmar o corregir.

Los eventos salen ordenados con los más dudosos primero, así las primeras
tarjetas son las que más corrigen. Cada corrección queda guardada y alimenta
la pantalla de precisión.
"""

from __future__ import annotations

import streamlit as st

from rugby_stats import revision, ui
from rugby_stats.modelos import ACCIONES, RESULTADOS, Evento

st.set_page_config(page_title="Revisión · rugby-stats-ia", page_icon="🏉", layout="wide")
ui.barra_lateral()
partido = ui.exigir_partido()
base = ui.db()
cfg = ui.config()

ui.encabezado(
    "Revisión",
    "La precisión final del sistema es la de quien confirma. Esto no es un "
    "trámite: es el paso que convierte propuestas en datos.",
)

eventos = base.eventos(partido.id)
if not eventos:
    st.info("Todavía no hay eventos. Corré el **Análisis** primero.")
    st.stop()

sin_revisar = [e for e in eventos if not e.revisado]
pendientes = [e for e in eventos if e.pendiente]
ui.metrica_fila(
    {
        "Eventos": len(eventos),
        "Sin revisar": len(sin_revisar),
        "Pendientes": len(pendientes),
        "Revisados": f"{100 * (len(eventos) - len(sin_revisar)) / len(eventos):.0f}%",
    }
)

if not sin_revisar:
    st.success("Revisaste todo. Andá a **Precisión** o a **Exportar**.")

cuantas = st.slider("Cuántas tarjetas mostrar", 1, 25, 5)
tarjetas = revision.armar_cola(base, partido.id, cfg, limite=cuantas)

jugadores_por_equipo = {
    partido.equipo_propio.nombre: base.plantel(
        partido.id, partido.equipo_propio.nombre
    ),
    partido.equipo_rival.nombre: base.plantel(partido.id, partido.equipo_rival.nombre),
}

st.divider()

for t in tarjetas:
    e = t.evento
    with st.container(border=True):
        cab1, cab2 = st.columns([3, 1])
        cab1.markdown(f"#### {t.titulo}")
        cab2.markdown(
            f"<div style='text-align:right'>{'🔴 pendiente' if e.pendiente else '🟡'} "
            f"{t.motivo}</div>",
            unsafe_allow_html=True,
        )

        col_video, col_form = st.columns([3, 4])

        with col_video:
            if t.embed_url and partido.video.tipo == "youtube":
                st.video(
                    partido.video.referencia, start_time=int(t.inicio_clip_s)
                )
            elif partido.video.ruta_local:
                ruta = revision.preparar_clip(partido, t, cfg)
                if ruta:
                    st.video(ruta)
                else:
                    st.caption(
                        f"Momento {ui.mm_ss(e.t_segundos)}. "
                        "Instalá ffmpeg para ver el clip acá."
                    )
            else:
                st.caption(f"Momento {ui.mm_ss(e.t_segundos)} del video.")
            if e.nota:
                st.caption(f"**Nota:** {e.nota}")
            if e.jugadores_secundarios:
                st.caption(
                    "También involucrados: "
                    + ", ".join(str(n) for n in e.jugadores_secundarios)
                )

        with col_form:
            equipo = st.selectbox(
                "Equipo",
                options=list(jugadores_por_equipo),
                index=list(jugadores_por_equipo).index(e.equipo)
                if e.equipo in jugadores_por_equipo
                else 0,
                key=f"eq_{e.id}",
            )
            opciones = jugadores_por_equipo.get(equipo, [])
            etiquetas = {None: "— no sé —"}
            etiquetas.update({j.numero: j.etiqueta for j in opciones})
            numeros = list(etiquetas)
            indice = (
                numeros.index(e.jugador_numero)
                if e.jugador_numero in numeros
                else 0
            )
            jugador = st.selectbox(
                "Jugador",
                options=numeros,
                index=indice,
                format_func=lambda n: etiquetas[n],
                key=f"jug_{e.id}",
            )

            c1, c2 = st.columns(2)
            accion = c1.selectbox(
                "Acción",
                options=list(ACCIONES),
                index=list(ACCIONES).index(e.accion) if e.accion in ACCIONES else 0,
                key=f"ac_{e.id}",
            )
            resultados = list(RESULTADOS.get(accion, ()))
            resultado = c2.selectbox(
                "Resultado",
                options=[""] + resultados,
                index=resultados.index(e.resultado) + 1
                if e.resultado in resultados
                else 0,
                key=f"re_{e.id}",
            )

            metros = None
            if accion == "Carry":
                metros = st.number_input(
                    "Metros (estimado)",
                    value=float(e.metros) if e.metros is not None else 0.0,
                    step=1.0,
                    key=f"me_{e.id}",
                    help="Es una estimación: la cámara panea y hace zoom.",
                )

            b1, b2, b3 = st.columns(3)
            sin_cambios = (
                jugador == e.jugador_numero
                and accion == e.accion
                and resultado == e.resultado
                and equipo == e.equipo
            )
            if b1.button(
                "Confirmar" if sin_cambios else "Guardar corrección",
                key=f"ok_{e.id}",
                type="primary",
            ):
                if sin_cambios:
                    revision.confirmar(base, partido.id, e)
                else:
                    revision.corregir(
                        base, partido.id, e,
                        {
                            "jugador_numero": jugador,
                            "accion": accion,
                            "resultado": resultado,
                            "equipo": equipo,
                            **({"metros": metros} if metros is not None else {}),
                        },
                        cfg,
                    )
                st.rerun()

            if b2.button("No existió", key=f"desc_{e.id}"):
                revision.descartar(base, partido.id, e)
                st.rerun()

            b3.caption(
                "**Confirmar** cuenta para la precisión del modelo. "
                "**Guardar corrección** la baja, que es justamente lo que "
                "queremos saber."
            )

# -- agregar a mano --------------------------------------------------------

st.divider()
with st.expander("Agregar un evento que el sistema no vio"):
    with st.form("manual"):
        c1, c2, c3 = st.columns(3)
        reloj = c1.text_input("Momento (mm:ss del video)", placeholder="23:40")
        equipo_m = c2.selectbox("Equipo", list(jugadores_por_equipo))
        opciones_m = jugadores_por_equipo.get(equipo_m, [])
        etiquetas_m = {j.numero: j.etiqueta for j in opciones_m}
        jugador_m = c3.selectbox(
            "Jugador",
            options=list(etiquetas_m) or [0],
            format_func=lambda n: etiquetas_m.get(n, "—"),
        )
        c4, c5, c6 = st.columns(3)
        accion_m = c4.selectbox("Acción", list(ACCIONES))
        resultado_m = c5.selectbox(
            "Resultado", [""] + list(RESULTADOS.get(accion_m, ()))
        )
        metros_m = c6.number_input("Metros", value=0.0, step=1.0)
        nota_m = st.text_input("Nota", placeholder="Lo cargué yo mirando el video")

        if st.form_submit_button("Agregar", type="primary"):
            from rugby_stats.analisis.reglas import (
                reloj_desde_video,
                segundos_desde_reloj,
            )

            t_s = segundos_desde_reloj(reloj)
            if t_s is None:
                st.error("No entendí el momento. Usá mm:ss.")
            elif not resultado_m:
                st.error(
                    "Falta el resultado: un evento sin resultado no se cuenta."
                )
            else:
                revision.agregar_a_mano(
                    base, partido.id,
                    Evento(
                        t_segundos=t_s,
                        accion=accion_m,
                        resultado=resultado_m,
                        equipo=equipo_m,
                        jugador_numero=jugador_m,
                        metros=metros_m or None,
                        reloj=reloj_desde_video(t_s, partido.video.desfase_s),
                        nota=nota_m,
                    ),
                )
                st.success("Agregado.")
                st.rerun()
