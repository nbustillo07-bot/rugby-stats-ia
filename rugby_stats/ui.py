"""Ayudantes compartidos por las pantallas de Streamlit."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from .config import Config, cargar_config
from .db import BaseDatos, ruta_por_defecto
from .gemini.cliente import crear_cliente
from .modelos import Partido

RAIZ = Path(__file__).resolve().parent.parent


def encabezado(titulo: str, ayuda: str = "") -> None:
    st.title(titulo)
    if ayuda:
        st.caption(ayuda)


@st.cache_resource
def _config() -> Config:
    return cargar_config(RAIZ / "config.yaml")


def config() -> Config:
    return _config()


def db() -> BaseDatos:
    """Una conexión por sesión de Streamlit."""
    if "db" not in st.session_state:
        st.session_state.db = BaseDatos(ruta_por_defecto())
    return st.session_state.db


def cliente(forzar_simulado: bool | None = None) -> Any:
    cfg = config()
    simulado = (
        st.session_state.get("forzar_simulado", False)
        if forzar_simulado is None
        else forzar_simulado
    )
    clave = f"cliente_{bool(simulado)}"
    if clave not in st.session_state:
        st.session_state[clave] = crear_cliente(cfg, forzar_simulado=bool(simulado))
    return st.session_state[clave]


def partido_actual() -> Partido | None:
    pid = st.session_state.get("partido_id")
    if pid is None:
        return None
    return db().partido(int(pid))


def barra_lateral() -> Partido | None:
    """Selector de partido y estado de la API. Va en todas las pantallas."""
    cfg = config()
    base = db()
    partidos = base.partidos()

    with st.sidebar:
        st.markdown("### Partido")
        if not partidos:
            st.info("Todavía no hay partidos. Creá uno en **Partido**.")
            partido = None
        else:
            etiquetas = {
                p.id: f"{p.nombre}" + (f" · {p.fecha}" if p.fecha else "")
                for p in partidos
            }
            actual = st.session_state.get("partido_id") or partidos[0].id
            if actual not in etiquetas:
                actual = partidos[0].id
            elegido = st.selectbox(
                "Elegí el partido",
                options=list(etiquetas),
                format_func=lambda i: etiquetas[i],
                index=list(etiquetas).index(actual),
                label_visibility="collapsed",
            )
            st.session_state.partido_id = elegido
            partido = base.partido(int(elegido))

        st.divider()
        st.markdown("### API")
        if cfg.hay_api_key:
            st.success(f"Clave cargada · {cfg.modelo}")
        else:
            st.error("Sin GEMINI_API_KEY")
            st.caption(
                "Copiá `.env.example` a `.env` y poné tu clave de "
                "[Google AI Studio](https://aistudio.google.com/apikey)."
            )
        st.session_state.forzar_simulado = st.toggle(
            "Modo simulado",
            value=st.session_state.get("forzar_simulado", not cfg.hay_api_key),
            help="Corre todo el flujo con datos inventados, sin gastar cuota. "
                 "Sirve para probar la interfaz y el Excel; los números no "
                 "miden nada.",
        )
        if st.session_state.forzar_simulado:
            st.warning("Simulado: los datos son inventados.")

    return partido


def exigir_partido() -> Partido:
    p = partido_actual()
    if p is None:
        st.warning("Primero creá o elegí un partido en la pantalla **Partido**.")
        st.stop()
    return p


def mm_ss(segundos: float) -> str:
    m, s = divmod(int(round(max(0.0, segundos))), 60)
    return f"{m:02d}:{s:02d}"


def metrica_fila(datos: dict[str, Any]) -> None:
    cols = st.columns(len(datos))
    for col, (etiqueta, valor) in zip(cols, datos.items()):
        col.metric(etiqueta, valor)
