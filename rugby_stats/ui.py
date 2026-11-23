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
            nombre = "Claude" if cfg.proveedor == "claude" else "Gemini"
            st.success(f"Clave de {nombre} cargada")
            if not st.session_state.get("forzar_simulado"):
                if cfg.proveedor == "claude":
                    mostrar_gasto(partido)
                else:
                    mostrar_cupos()
        else:
            st.error(f"Sin {cfg.nombre_variable_clave}")
            if cfg.proveedor == "claude":
                st.caption(
                    "Hacé doble clic en `2_poner_clave.bat` y pegá la clave de "
                    "[la consola de Claude](https://platform.claude.com/settings/keys)."
                )
            else:
                st.caption(
                    "Copiá `.env.example` a `.env` y poné tu clave de "
                    "[Google AI Studio](https://aistudio.google.com/apikey)."
                )
        st.session_state.forzar_simulado = st.toggle(
            "Modo simulado",
            value=st.session_state.get("forzar_simulado", not cfg.hay_api_key),
            help="Corre todo el flujo con datos inventados, sin gastar plata ni cuota. "
                 "Sirve para probar la interfaz y el Excel; los números no "
                 "miden nada.",
        )
        if st.session_state.forzar_simulado:
            st.warning("Simulado: los datos son inventados.")

    return partido


def mostrar_gasto(partido) -> None:
    """Cuánto lleva gastado el partido elegido contra el tope."""
    cfg = config()
    tope = cfg.tope_usd_por_partido
    if partido is None or partido.id is None:
        st.caption(f"Tope por partido: US$ {tope:.2f}")
        return
    gastado = db().gasto_partido(partido.id)
    if tope > 0:
        st.progress(
            min(1.0, gastado / tope),
            text=f"Gastado en este partido: US$ {gastado:.2f} de {tope:.2f}",
        )
    else:
        st.caption(f"Gastado en este partido: US$ {gastado:.2f} (sin tope)")


def estado_cupos() -> list[dict[str, Any]]:
    """Cuánto del cupo gratis de hoy lleva gastado cada modelo."""
    from .gemini.cliente import Cupos

    cfg = config()
    cupos = Cupos(cfg.archivo_cupos)
    topes = cfg.cupo_diario
    filas = []
    for nivel, modelos in (cfg.niveles or {}).items():
        for m in modelos:
            falta = cupos.proxima_renovacion([m]) if cupos.agotado(m) else None
            filas.append(
                {
                    "nivel": nivel,
                    "modelo": m,
                    "usados": cupos.usados(m),
                    "tope": topes.get(m),
                    "agotado": falta is not None,
                    "vuelve_en_h": round(falta / 3600, 1) if falta else None,
                }
            )
    return filas


def mostrar_cupos() -> None:
    """Resumen del cupo de hoy, por nivel."""
    filas = estado_cupos()
    if not filas:
        return
    for nivel, titulo in (("fuerte", "Modelos fuertes"), ("liviano", "Modelos livianos")):
        del_nivel = [f for f in filas if f["nivel"] == nivel]
        if not del_nivel:
            continue
        tope = sum(f["tope"] or 0 for f in del_nivel)
        # Un modelo agotado cuenta como usado entero, haya pasado por acá o no.
        usados = sum(
            (f["tope"] or f["usados"]) if f["agotado"] else f["usados"]
            for f in del_nivel
        )
        quedan = max(0, tope - usados)
        st.progress(
            min(1.0, usados / tope) if tope else 0.0,
            text=f"{titulo}: quedan ~{quedan} de {tope} pedidos de hoy",
        )
        agotados = [f for f in del_nivel if f["agotado"]]
        if agotados and len(agotados) == len(del_nivel):
            st.caption(
                f"Sin cupo por hoy. Vuelve en ~{min(f['vuelve_en_h'] for f in agotados):.0f} h."
            )
    st.caption(
        "Es una cuenta aproximada llevada por este programa; la oficial está en "
        "[ai.dev/rate-limit](https://ai.dev/rate-limit)."
    )


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
