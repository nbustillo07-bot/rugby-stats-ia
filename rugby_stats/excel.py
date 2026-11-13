"""Exportación a Excel.

La hoja "Jugadores" no trae números pegados: trae **fórmulas** que cuentan
desde la hoja "Eventos". Así, si en el Excel se corrige una fila de un evento
—o se borra una que no correspondía— las stats por jugador se recalculan
solas. Los puntos por try, conversión y penal también son celdas editables en
la hoja "Notas".

Lo que queda claro en la hoja "Notas": qué está medido y qué es estimado.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from .analisis import reglas
from .config import Config
from .modelos import Evento, Jugador, Partido
from .stats import COLUMNAS

# --- estilos ---------------------------------------------------------------

AZUL = "1F3A5F"
GRIS = "F2F2F2"
AMARILLO = "FFF2CC"
ROJO = "FFC7CE"

F_TITULO = Font(bold=True, size=14, color="FFFFFF")
F_CABECERA = Font(bold=True, size=10, color="FFFFFF")
F_NEGRITA = Font(bold=True)
R_CABECERA = PatternFill("solid", fgColor=AZUL)
R_TOTAL = PatternFill("solid", fgColor=GRIS)
R_EDITABLE = PatternFill("solid", fgColor=AMARILLO)
R_PENDIENTE = PatternFill("solid", fgColor=ROJO)
BORDE = Border(*[Side(style="thin", color="BFBFBF")] * 4)
CENTRO = Alignment(horizontal="center", vertical="center")
ENVUELTO = Alignment(wrap_text=True, vertical="top")

# --- hoja Eventos: columnas ------------------------------------------------

COLS_EVENTOS = (
    ("N.º", 6),
    ("Reloj", 8),
    ("Equipo", 20),
    ("Jugador", 24),
    ("Acción", 18),
    ("Resultado", 16),
    ("Metros", 9),
    ("Confianza", 10),
    ("Fuente", 10),
    ("Revisado", 10),
    ("Pendiente", 10),
    ("Nota", 44),
)
# Letras que usan las fórmulas de la hoja Jugadores.
C_NUM, C_EQUIPO, C_ACCION, C_RESULTADO, C_METROS = "A", "C", "E", "F", "G"

# Dónde quedan las celdas de puntos editables en la hoja Notas.
# La hoja reserva las filas 3 y 4 para el aviso de modo simulado (queden
# vacías o no), así que esta posición es siempre la misma y las fórmulas de
# la hoja Jugadores nunca apuntan mal.
FILA_AVISO = 3
FILA_TITULO_PUNTOS = 5
FILA_PUNTOS = 6  # primera fila de la tabla de puntos
CELDA_TRY = f"$B${FILA_PUNTOS}"
CELDA_CONV = f"$B${FILA_PUNTOS + 1}"
CELDA_PENAL = f"$B${FILA_PUNTOS + 2}"
CELDA_DROP = f"$B${FILA_PUNTOS + 3}"


def exportar(
    ruta: Path | str,
    partido: Partido,
    jugadores: list[Jugador],
    eventos: list[Evento],
    cfg: Config,
    simulado: bool = False,
    precision: dict[str, dict[str, float]] | None = None,
) -> Path:
    """Escribe el Excel y devuelve la ruta."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    hoja_eventos = wb.active
    hoja_eventos.title = "Eventos"
    _hoja_eventos(hoja_eventos, partido, jugadores, eventos)

    n_eventos = len(eventos)
    quienes = cfg.jugadores_de
    if quienes == "ambos":
        a_listar = jugadores
    else:
        a_listar = [j for j in jugadores if j.equipo == partido.equipo_propio.nombre]

    hoja_jug = wb.create_sheet("Jugadores", 0)
    _hoja_jugadores(hoja_jug, partido, a_listar, n_eventos, cfg)

    _hoja_notas(wb.create_sheet("Notas"), partido, cfg, eventos, simulado, precision)

    wb.save(ruta)
    return ruta


# ---------------------------------------------------------------------------
# Hoja Eventos
# ---------------------------------------------------------------------------


def _hoja_eventos(
    h: Worksheet, partido: Partido, jugadores: list[Jugador], eventos: list[Evento]
) -> None:
    nombres = {(j.equipo, j.numero): j for j in jugadores}

    for i, (titulo, ancho) in enumerate(COLS_EVENTOS, 1):
        c = h.cell(row=1, column=i, value=titulo)
        c.font = F_CABECERA
        c.fill = R_CABECERA
        c.alignment = CENTRO
        h.column_dimensions[get_column_letter(i)].width = ancho

    for fila, e in enumerate(sorted(eventos, key=lambda x: x.t_segundos), start=2):
        j = nombres.get((e.equipo, e.jugador_numero)) if e.jugador_numero else None
        nombre = j.nombre if j else ("" if e.jugador_numero is None else "?")
        valores = [
            e.jugador_numero,
            e.reloj or reglas.reloj_desde_video(e.t_segundos, partido.video.desfase_s),
            e.equipo,
            nombre,
            e.accion,
            e.resultado,
            e.metros,
            round(e.confianza, 2),
            e.fuente,
            "sí" if e.revisado else "no",
            "sí" if e.pendiente else "no",
            e.nota,
        ]
        for col, valor in enumerate(valores, 1):
            c = h.cell(row=fila, column=col, value=valor)
            c.border = BORDE
            if col in (1, 2, 7, 8, 10, 11):
                c.alignment = CENTRO
            if col == 12:
                c.alignment = ENVUELTO
        if e.pendiente:
            for col in range(1, len(valores) + 1):
                h.cell(row=fila, column=col).fill = R_PENDIENTE

    h.freeze_panes = "A2"
    if eventos:
        h.auto_filter.ref = f"A1:{get_column_letter(len(COLS_EVENTOS))}{len(eventos) + 1}"


# ---------------------------------------------------------------------------
# Hoja Jugadores
# ---------------------------------------------------------------------------


def _rango(col: str, n: int) -> str:
    """Rango de la hoja Eventos para las fórmulas, con filas fijas."""
    ultima = max(2, n + 1)
    return f"Eventos!${col}$2:${col}${ultima}"


def _hoja_jugadores(
    h: Worksheet,
    partido: Partido,
    jugadores: list[Jugador],
    n_eventos: int,
    cfg: Config,
) -> None:
    titulos = ["N.º", "Jugador", "Equipo"]
    titulos += [t for t, _, _, _ in COLUMNAS]
    titulos += ["Puntos", "Minutos jugados", "Sin revisar"]

    h.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(titulos))
    c = h.cell(row=1, column=1, value=f"{partido.nombre} — stats por jugador")
    c.font = F_TITULO
    c.fill = R_CABECERA
    c.alignment = CENTRO
    h.row_dimensions[1].height = 24

    for i, titulo in enumerate(titulos, 1):
        c = h.cell(row=2, column=i, value=titulo)
        c.font = F_CABECERA
        c.fill = R_CABECERA
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        h.column_dimensions[get_column_letter(i)].width = (
            6 if i == 1 else 24 if i == 2 else 20 if i == 3 else 12
        )
    h.row_dimensions[2].height = 34

    r_num = _rango(C_NUM, n_eventos)
    r_eq = _rango(C_EQUIPO, n_eventos)
    r_ac = _rango(C_ACCION, n_eventos)
    r_re = _rango(C_RESULTADO, n_eventos)
    r_me = _rango(C_METROS, n_eventos)
    r_rev = _rango("J", n_eventos)
    r_pen = _rango("K", n_eventos)

    fila0 = 3
    for n, j in enumerate(sorted(jugadores, key=lambda x: (x.equipo, x.numero))):
        fila = fila0 + n
        h.cell(row=fila, column=1, value=j.numero).alignment = CENTRO
        h.cell(row=fila, column=2, value=j.etiqueta)
        h.cell(row=fila, column=3, value=j.equipo)
        cn = f"$A{fila}"
        ce = f"$C{fila}"

        col = 4
        for titulo, accion, resultado, tipo in COLUMNAS:
            # Solo se cuentan los eventos que no quedaron pendientes.
            cond = (
                f'{r_num},{cn},{r_eq},{ce},{r_ac},"{accion}",{r_pen},"no"'
            )
            if resultado is not None:
                cond += f',{r_re},"{resultado}"'
            if tipo == "sumar":
                formula = f"=SUMIFS({r_me},{cond})"
            else:
                formula = f"=COUNTIFS({cond})"
            c = h.cell(row=fila, column=col, value=formula)
            c.alignment = CENTRO
            c.border = BORDE
            col += 1

        # Puntos: desde las celdas editables de la hoja Notas.
        puntos = (
            f'=COUNTIFS({r_num},{cn},{r_eq},{ce},{r_ac},"Try",{r_pen},"no")*Notas!{CELDA_TRY}'
            f'+COUNTIFS({r_num},{cn},{r_eq},{ce},{r_ac},"Conversión",{r_re},"adentro",{r_pen},"no")*Notas!{CELDA_CONV}'
            f'+COUNTIFS({r_num},{cn},{r_eq},{ce},{r_ac},"Penal a los palos",{r_re},"adentro",{r_pen},"no")*Notas!{CELDA_PENAL}'
            f'+COUNTIFS({r_num},{cn},{r_eq},{ce},{r_ac},"Drop",{r_re},"adentro",{r_pen},"no")*Notas!{CELDA_DROP}'
        )
        c = h.cell(row=fila, column=col, value=puntos)
        c.alignment = CENTRO
        c.font = F_NEGRITA
        c.border = BORDE
        col += 1

        h.cell(
            row=fila, column=col,
            value=reglas.minutos_jugados(j),
        ).alignment = CENTRO
        col += 1

        c = h.cell(
            row=fila, column=col,
            value=f'=COUNTIFS({r_num},{cn},{r_eq},{ce},{r_rev},"no")',
        )
        c.alignment = CENTRO
        c.border = BORDE

    # Fila de total.
    if jugadores:
        fila_total = fila0 + len(jugadores)
        c = h.cell(row=fila_total, column=1, value="")
        h.cell(row=fila_total, column=2, value="TOTAL").font = F_NEGRITA
        for col in range(4, len(titulos) + 1):
            letra = get_column_letter(col)
            c = h.cell(
                row=fila_total, column=col,
                value=f"=SUM({letra}{fila0}:{letra}{fila_total - 1})",
            )
            c.font = F_NEGRITA
            c.alignment = CENTRO
        for col in range(1, len(titulos) + 1):
            h.cell(row=fila_total, column=col).fill = R_TOTAL

    h.freeze_panes = "D3"


# ---------------------------------------------------------------------------
# Hoja Notas
# ---------------------------------------------------------------------------


def _hoja_notas(
    h: Worksheet,
    partido: Partido,
    cfg: Config,
    eventos: list[Evento],
    simulado: bool,
    precision: dict[str, dict[str, float]] | None,
) -> None:
    h.column_dimensions["A"].width = 38
    h.column_dimensions["B"].width = 16
    h.column_dimensions["C"].width = 62

    h.merge_cells(start_row=1, start_column=1, end_row=1, end_column=3)
    c = h.cell(row=1, column=1, value="Cómo leer esta planilla")
    c.font = F_TITULO
    c.fill = R_CABECERA
    c.alignment = CENTRO

    # Fila 3 reservada para el aviso de modo simulado, esté o no.
    if simulado:
        h.merge_cells(
            start_row=FILA_AVISO, start_column=1, end_row=FILA_AVISO, end_column=3
        )
        c = h.cell(
            row=FILA_AVISO, column=1,
            value="ATENCIÓN: esta planilla salió del MODO SIMULADO, sin clave de "
                  "API. Los datos son inventados y no miden nada. Sirve solo "
                  "para ver el formato.",
        )
        c.font = Font(bold=True, color="9C0006")
        c.fill = R_PENDIENTE
        c.alignment = ENVUELTO
        h.row_dimensions[FILA_AVISO].height = 32

    # Puntos editables, siempre en FILA_PUNTOS: las fórmulas de la hoja
    # Jugadores apuntan acá por referencia fija.
    h.cell(row=FILA_TITULO_PUNTOS, column=1, value="Puntos (editables)").font = F_NEGRITA
    fila = FILA_PUNTOS
    p = cfg.puntos
    for etiqueta, valor in (
        ("Try", p["try"]),
        ("Conversión", p["conversion"]),
        ("Penal a los palos", p["penal"]),
        ("Drop", p["drop"]),
    ):
        h.cell(row=fila, column=1, value=etiqueta)
        c = h.cell(row=fila, column=2, value=valor)
        c.fill = R_EDITABLE
        c.alignment = CENTRO
        c.border = BORDE
        h.cell(
            row=fila, column=3,
            value="Cambiá el número y la columna Puntos se recalcula sola.",
        ).alignment = ENVUELTO
        fila += 1
    fila += 1

    bloques: list[tuple[str, list[tuple[str, str]]]] = [
        (
            "Qué está medido y qué es estimado",
            [
                ("Medido",
                 "Tackles, tackles errados, carries, pases, offloads, patadas, "
                 "rucks, pelotas perdidas y recuperadas, penales, tries, "
                 "conversiones, penales a los palos y lines: salen de mirar el "
                 "video."),
                ("Estimado",
                 "Metros ganados. La cámara panea y hace zoom, así que la "
                 "estimación sale de las líneas de la cancha y es gruesa. "
                 "Tomalos como orden de magnitud, no como medición."),
                ("Calculado",
                 "Minutos jugados: sale de los minutos de entrada y salida "
                 "cargados o detectados, no de medir presencia en cancha."),
                ("Puntos",
                 "Calculados desde los tries, conversiones, penales y drops con "
                 "los valores de arriba."),
            ],
        ),
        (
            "Criterios usados",
            [
                ("Tackle positivo / negativo", reglas.descripcion_criterio(cfg)),
                ("Tackle neutro",
                 "El avance del portador quedó dentro de la tolerancia, o no se "
                 "pudo determinar con los metros."),
                ("Un tackle por tackleador",
                 "Si entran dos jugadores al mismo tackle, cada uno suma un "
                 "tackle y el otro queda listado como secundario."),
                ("Sin clasificar no vale",
                 "Un evento sin jugador o sin resultado válido NO se cuenta: "
                 "queda marcado como pendiente (en rojo en la hoja Eventos) y "
                 "las fórmulas lo excluyen. Resolvelo en la cola de revisión o "
                 "a mano."),
                ("Confianza",
                 f"Va de 0 a 1. Debajo de {cfg.umbral_confianza_revision:.2f} el "
                 "evento entra obligatoriamente a la cola de revisión."),
            ],
        ),
        (
            "De dónde salió cada dato (columna Fuente)",
            [
                ("video", "Se vio en las imágenes."),
                ("relato", "Lo dijo el relator y el video no lo contradice."),
                ("marcador", "Salió del cartel de pantalla (reloj o tanteador)."),
                ("arbitro", "Salió de la seña del árbitro."),
                ("manual", "Lo cargó o lo corrigió una persona."),
            ],
        ),
    ]

    for titulo, filas_bloque in bloques:
        h.merge_cells(start_row=fila, start_column=1, end_row=fila, end_column=3)
        c = h.cell(row=fila, column=1, value=titulo)
        c.font = F_CABECERA
        c.fill = R_CABECERA
        fila += 1
        for etiqueta, texto in filas_bloque:
            h.cell(row=fila, column=1, value=etiqueta).font = F_NEGRITA
            h.merge_cells(start_row=fila, start_column=2, end_row=fila, end_column=3)
            c = h.cell(row=fila, column=2, value=texto)
            c.alignment = ENVUELTO
            h.row_dimensions[fila].height = max(15, 13 * (1 + len(texto) // 70))
            fila += 1
        fila += 1

    # Estado de la revisión.
    h.merge_cells(start_row=fila, start_column=1, end_row=fila, end_column=3)
    c = h.cell(row=fila, column=1, value="Estado de la revisión")
    c.font = F_CABECERA
    c.fill = R_CABECERA
    fila += 1
    total = len(eventos)
    revisados = len([e for e in eventos if e.revisado])
    pendientes = len([e for e in eventos if e.pendiente])
    for etiqueta, valor in (
        ("Eventos detectados", total),
        ("Confirmados por una persona", revisados),
        ("Pendientes (no se cuentan)", pendientes),
        (
            "Porcentaje revisado",
            f"{100 * revisados / total:.0f}%" if total else "—",
        ),
    ):
        h.cell(row=fila, column=1, value=etiqueta)
        h.cell(row=fila, column=2, value=valor).alignment = CENTRO
        fila += 1
    fila += 1

    if precision:
        h.merge_cells(start_row=fila, start_column=1, end_row=fila, end_column=3)
        c = h.cell(
            row=fila, column=1,
            value="Precisión medida (propuestas confirmadas sin cambios)",
        )
        c.font = F_CABECERA
        c.fill = R_CABECERA
        fila += 1
        h.cell(row=fila, column=1, value="Tipo de evento").font = F_NEGRITA
        h.cell(row=fila, column=2, value="Precisión").font = F_NEGRITA
        h.cell(row=fila, column=3, value="Revisados").font = F_NEGRITA
        fila += 1
        for accion, d in sorted(precision.items()):
            h.cell(row=fila, column=1, value=accion)
            h.cell(
                row=fila, column=2,
                value=f"{d.get('precision', 0) * 100:.0f}%",
            ).alignment = CENTRO
            h.cell(row=fila, column=3, value=int(d.get("revisados", 0))).alignment = CENTRO
            fila += 1
        fila += 1

    h.cell(
        row=fila, column=1,
        value=f"Generado el {datetime.now():%d/%m/%Y %H:%M} con rugby-stats-ia.",
    ).font = Font(italic=True, size=9)
    fila += 1
    h.merge_cells(start_row=fila, start_column=1, end_row=fila, end_column=3)
    c = h.cell(
        row=fila, column=1,
        value="La hoja Jugadores no tiene números pegados: son fórmulas que "
              "cuentan desde la hoja Eventos. Si corregís o borrás una fila de "
              "Eventos, las stats se actualizan solas.",
    )
    c.alignment = ENVUELTO
    h.row_dimensions[fila].height = 32
