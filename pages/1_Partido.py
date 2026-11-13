"""Crear o editar un partido: los dos equipos y el video."""

from __future__ import annotations

import streamlit as st

from rugby_stats import ui
from rugby_stats.gemini import video as video_mod
from rugby_stats.modelos import Equipo, Partido

st.set_page_config(page_title="Partido · rugby-stats-ia", page_icon="🏉", layout="wide")
partido = ui.barra_lateral()
base = ui.db()
cfg = ui.config()

ui.encabezado(
    "Partido",
    "Los dos equipos y el video. Describir bien la camiseta de cada uno "
    "importa: es lo que le permite al modelo distinguir un equipo del otro.",
)

modo = st.radio(
    "Qué querés hacer",
    ["Crear un partido nuevo", "Editar el partido elegido"],
    horizontal=True,
    index=0 if partido is None else 1,
)
editando = modo.startswith("Editar") and partido is not None
p = partido if editando else None

LEGIBILIDAD = ["alta", "media", "baja"]
AYUDA_LEGIBLE = (
    "Qué tan fácil es leer el número en la espalda. Una camiseta a rayas o de "
    "color claro con número blanco casi no se lee: poné **baja** y el sistema "
    "va a depender de las fichas de apariencia."
)

with st.form("partido"):
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### Equipo propio")
        st.caption("El que querés analizar. Las stats salen de este plantel.")
        propio_nombre = st.text_input(
            "Nombre del club", value=p.equipo_propio.nombre if p else "",
            placeholder="Mi club",
        )
        propio_kit = st.text_input(
            "Cómo es la camiseta",
            value=p.equipo_propio.kit if p else "",
            placeholder="rayas bordó y blancas verticales, número blanco",
            help="Descripción en palabras, como se la ve en el video.",
        )
        propio_legible = st.select_slider(
            "¿Se lee el número en la espalda?",
            options=LEGIBILIDAD,
            value=p.equipo_propio.numero_legible if p else "media",
            help=AYUDA_LEGIBLE,
            key="legible_propio",
        )

    with col2:
        st.markdown("#### Equipo rival")
        rival_nombre = st.text_input(
            "Nombre del club rival", value=p.equipo_rival.nombre if p else "",
            placeholder="Club rival",
        )
        rival_kit = st.text_input(
            "Cómo es la camiseta rival",
            value=p.equipo_rival.kit if p else "",
            placeholder="bordó liso, número blanco",
        )
        rival_legible = st.select_slider(
            "¿Se lee el número en la espalda? (rival)",
            options=LEGIBILIDAD,
            value=p.equipo_rival.numero_legible if p else "media",
            help=AYUDA_LEGIBLE,
            key="legible_rival",
        )

    st.divider()
    col3, col4, col5 = st.columns([2, 1, 1])
    nombre = col3.text_input(
        "Nombre del partido", value=p.nombre if p else "",
        placeholder="Mi club vs Club rival — semifinal",
    )
    fecha = col4.text_input("Fecha", value=p.fecha if p else "", placeholder="13/11/2026")
    competencia = col5.text_input(
        "Competencia", value=p.competencia if p else "", placeholder="URBA Primera C"
    )

    st.markdown("#### Video")
    referencia = st.text_input(
        "Link de YouTube, otra URL, o la ruta de un archivo",
        value=p.video.referencia if p else "",
        placeholder="https://www.youtube.com/watch?v=...",
        help="YouTube se le pasa directo al modelo (solo videos públicos). "
             "Un archivo local se sube por la Files API. Cualquier otra URL se "
             "baja primero con yt-dlp.",
    )

    st.markdown("##### Tramo a analizar")
    st.caption(
        "Para la primera prueba conviene un tramo corto: cinco o diez minutos "
        "alcanzan para medir la precisión y no se gasta la cuota del día."
    )
    col6, col7 = st.columns(2)
    desde_txt = col6.text_input(
        "Desde (mm:ss del video)",
        value=ui.mm_ss(p.analizar_desde_s) if p and p.analizar_desde_s else "",
        placeholder="00:00",
    )
    hasta_txt = col7.text_input(
        "Hasta (mm:ss del video, vacío = hasta el final)",
        value=ui.mm_ss(p.analizar_hasta_s) if p and p.analizar_hasta_s else "",
        placeholder="",
    )

    notas = st.text_area(
        "Notas", value=p.notas if p else "",
        placeholder="Cámara con zoom, marcador en pantalla, hay relato.",
        height=70,
    )

    guardar = st.form_submit_button(
        "Guardar partido" if editando else "Crear partido", type="primary"
    )

if guardar:
    from rugby_stats.analisis.reglas import segundos_desde_reloj

    errores = []
    if not propio_nombre.strip():
        errores.append("Falta el nombre del equipo propio.")
    if not rival_nombre.strip():
        errores.append("Falta el nombre del equipo rival.")
    if propio_nombre.strip() == rival_nombre.strip() and propio_nombre.strip():
        errores.append("Los dos equipos tienen el mismo nombre.")
    if not referencia.strip():
        errores.append("Falta el video.")

    fuente = None
    if referencia.strip():
        try:
            fuente = video_mod.detectar_fuente(referencia.strip())
        except video_mod.ErrorVideo as exc:
            errores.append(str(exc))

    desde_s = segundos_desde_reloj(desde_txt) or 0.0
    hasta_s = segundos_desde_reloj(hasta_txt)
    if hasta_s is not None and hasta_s <= desde_s:
        errores.append("El 'hasta' tiene que ser mayor que el 'desde'.")

    if errores:
        for e in errores:
            st.error(e)
    else:
        if editando and p is not None:
            # Se conserva lo ya preparado del video si la referencia no cambió.
            if p.video.referencia != fuente.referencia:
                p.video = fuente
            p.nombre = nombre.strip() or f"{propio_nombre} vs {rival_nombre}"
            p.fecha = fecha.strip()
            p.competencia = competencia.strip()
            p.notas = notas.strip()
            p.analizar_desde_s = desde_s
            p.analizar_hasta_s = hasta_s
            p.equipo_propio = Equipo(
                nombre=propio_nombre.strip(), kit=propio_kit.strip(),
                numero_legible=propio_legible, es_propio=True,
            )
            p.equipo_rival = Equipo(
                nombre=rival_nombre.strip(), kit=rival_kit.strip(),
                numero_legible=rival_legible, es_propio=False,
            )
            base.guardar_partido(p)
            st.success("Guardado.")
        else:
            nuevo = Partido(
                nombre=nombre.strip() or f"{propio_nombre} vs {rival_nombre}",
                fecha=fecha.strip(),
                competencia=competencia.strip(),
                notas=notas.strip(),
                equipo_propio=Equipo(
                    nombre=propio_nombre.strip(), kit=propio_kit.strip(),
                    numero_legible=propio_legible, es_propio=True,
                ),
                equipo_rival=Equipo(
                    nombre=rival_nombre.strip(), kit=rival_kit.strip(),
                    numero_legible=rival_legible, es_propio=False,
                ),
                video=fuente,
                analizar_desde_s=desde_s,
                analizar_hasta_s=hasta_s,
            )
            pid = base.guardar_partido(nuevo)
            st.session_state.partido_id = pid
            st.success("Partido creado. Siguiente paso: **Plantel**.")
        st.rerun()

# -- herramientas ----------------------------------------------------------

if partido is not None:
    st.divider()
    with st.expander("Herramientas del video"):
        col1, col2 = st.columns(2)
        if col1.button("Averiguar cuánto dura"):
            with st.spinner("Preguntando…"):
                d = video_mod.duracion_s(
                    partido.video.ruta_local or partido.video.referencia
                )
            if d:
                partido.video.duracion_s = d
                base.guardar_partido(partido)
                st.success(f"Dura {ui.mm_ss(d)}.")
                st.rerun()
            else:
                st.warning(
                    "No pude averiguarlo. Instalá `yt-dlp` (para URLs) o "
                    "`ffmpeg` (para archivos), o poné el tramo a mano arriba."
                )
        col2.caption(
            f"ffmpeg: {'sí' if video_mod.hay_ffmpeg() else 'no'} · "
            f"yt-dlp: {'sí' if video_mod.hay_ytdlp() else 'no'}"
        )

        st.markdown("##### Desfase del reloj")
        st.caption(
            "Si el cartel de pantalla marca 22:48 cuando el video va por el "
            "minuto 5, el desfase es la diferencia. El sistema lo detecta solo "
            "en la pasada gruesa, pero lo podés fijar acá."
        )
        col3, col4 = st.columns(2)
        reloj_visto = col3.text_input("El cartel marca", placeholder="22:48")
        t_video = col4.text_input("y el video va por", placeholder="05:00")
        if st.button("Calcular desfase"):
            from rugby_stats.analisis.reglas import calcular_desfase, segundos_desde_reloj

            tv = segundos_desde_reloj(t_video)
            d = calcular_desfase(reloj_visto, tv) if tv is not None else None
            if d is None:
                st.error("No entendí los tiempos. Usá mm:ss.")
            else:
                partido.video.desfase_s = d
                base.guardar_partido(partido)
                st.success(f"Desfase: {d:+.0f} s.")

    with st.expander("Borrar este partido"):
        st.caption("Se borra todo: plantel, fichas, eventos y correcciones.")
        if st.text_input("Escribí el nombre del partido para confirmar") == partido.nombre:
            if st.button("Borrar", type="primary"):
                base.borrar_partido(partido.id)
                st.session_state.pop("partido_id", None)
                st.success("Borrado.")
                st.rerun()
