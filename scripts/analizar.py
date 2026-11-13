#!/usr/bin/env python3
"""Correr un análisis desde la terminal, sin interfaz.

Sirve para dejarlo corriendo con un partido largo, o para automatizarlo.

    # crear el partido y analizar un tramo
    python scripts/analizar.py nuevo \
        --propio "Mi Club" --kit-propio "rayas bordó y blancas" --legible-propio baja \
        --rival "Club Rival" --kit-rival "bordó liso" \
        --video "https://www.youtube.com/watch?v=XXXX" \
        --plantel data/ejemplos/plantel_ejemplo.csv \
        --desde 22:48 --hasta 28:40

    # listar partidos
    python scripts/analizar.py lista

    # seguir con un partido que ya existe
    python scripts/analizar.py correr --partido 1 --pasos gruesa,eventos,contactos

    # exportar
    python scripts/analizar.py exportar --partido 1
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rugby_stats import excel, plantel as mod_plantel, precision as mod_precision  # noqa: E402
from rugby_stats.analisis import pipeline  # noqa: E402
from rugby_stats.analisis.reglas import segundos_desde_reloj  # noqa: E402
from rugby_stats.config import cargar_config  # noqa: E402
from rugby_stats.db import BaseDatos  # noqa: E402
from rugby_stats.gemini import video as video_mod  # noqa: E402
from rugby_stats.gemini.cliente import crear_cliente  # noqa: E402
from rugby_stats.modelos import Equipo, Partido  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent


def _base(cfg) -> BaseDatos:
    return BaseDatos(RAIZ / "data" / "partidos.db")


def _barra(texto: str, fraccion: float) -> None:
    ancho = 30
    lleno = int(ancho * max(0.0, min(1.0, fraccion)))
    print(
        f"\r[{'█' * lleno}{'·' * (ancho - lleno)}] {fraccion * 100:5.1f}% {texto[:50]:50}",
        end="",
        flush=True,
    )


def cmd_lista(args, cfg) -> int:
    base = _base(cfg)
    partidos = base.partidos()
    if not partidos:
        print("No hay partidos todavía.")
        return 0
    for p in partidos:
        eventos = base.eventos(p.id)
        revisados = len([e for e in eventos if e.revisado])
        print(
            f"{p.id:3}  {p.nombre[:45]:45}  "
            f"{p.equipo_propio.nombre} vs {p.equipo_rival.nombre}  "
            f"{len(eventos)} eventos ({revisados} revisados)"
        )
    return 0


def cmd_nuevo(args, cfg) -> int:
    base = _base(cfg)
    try:
        fuente = video_mod.detectar_fuente(args.video)
    except video_mod.ErrorVideo as exc:
        print(f"✗ {exc}")
        return 1

    desde = segundos_desde_reloj(args.desde) or 0.0
    hasta = segundos_desde_reloj(args.hasta) if args.hasta else None

    p = Partido(
        nombre=args.nombre or f"{args.propio} vs {args.rival}",
        fecha=args.fecha or "",
        competencia=args.competencia or "",
        equipo_propio=Equipo(
            nombre=args.propio, kit=args.kit_propio or "",
            numero_legible=args.legible_propio, es_propio=True,
        ),
        equipo_rival=Equipo(
            nombre=args.rival, kit=args.kit_rival or "",
            numero_legible=args.legible_rival,
        ),
        video=fuente,
        analizar_desde_s=desde,
        analizar_hasta_s=hasta,
    )
    pid = base.guardar_partido(p)
    print(f"✓ Partido {pid} creado: {p.nombre}")

    if args.plantel:
        jugadores, problemas = mod_plantel.cargar_csv(args.plantel, args.propio)
        base.guardar_plantel(pid, jugadores)
        print(f"✓ Plantel propio: {len(jugadores)} jugadores")
        for pr in problemas:
            print(f"  ⚠ {pr}")
        for aviso in mod_plantel.validar(jugadores):
            print(f"  ⚠ {aviso}")
    if args.plantel_rival:
        jugadores, problemas = mod_plantel.cargar_csv(args.plantel_rival, args.rival)
        base.guardar_plantel(pid, jugadores)
        print(f"✓ Plantel rival: {len(jugadores)} jugadores")

    if args.correr:
        args.partido = pid
        return cmd_correr(args, cfg)
    print(f"\nPara analizar: python scripts/analizar.py correr --partido {pid}")
    return 0


def cmd_correr(args, cfg) -> int:
    base = _base(cfg)
    p = base.partido(args.partido)
    if p is None:
        print(f"✗ No existe el partido {args.partido}.")
        return 1

    cliente = crear_cliente(cfg, forzar_simulado=args.simulado)
    if getattr(cliente, "modelo", "") == "simulado":
        print("⚠ MODO SIMULADO: los datos van a ser inventados.\n")

    pasos = args.pasos.split(",") if args.pasos else None
    print(f"Analizando: {p.nombre}")
    try:
        res = pipeline.correr(
            base, cliente, p.id, cfg,
            pasos=pasos, forzar=args.forzar, progreso=_barra,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"\n✗ Se cortó: {exc}")
        print("  Lo hecho quedó guardado: volvé a correr el mismo comando.")
        return 1

    print("\n")
    print(res.resumen())
    if res.contadores:
        print(res.contadores.resumen())
    if res.aviso:
        print(f"⚠ {res.aviso}")
    if res.eventos_pendientes:
        print(
            f"⚠ {res.eventos_pendientes} eventos sin clasificar: no se cuentan "
            "hasta resolverlos en la pantalla de revisión."
        )
    print("\nPara revisar: streamlit run app.py")
    return 0


def cmd_exportar(args, cfg) -> int:
    base = _base(cfg)
    p = base.partido(args.partido)
    if p is None:
        print(f"✗ No existe el partido {args.partido}.")
        return 1
    eventos = base.eventos(p.id)
    if not eventos:
        print("✗ No hay eventos. Corré el análisis primero.")
        return 1
    jugadores = base.plantel(p.id)
    informe = mod_precision.calcular(base, p.id, cfg)

    import re

    slug = re.sub(r"[^\w\-]+", "_", p.nombre).strip("_").lower()[:60]
    ruta = Path(args.salida) if args.salida else (
        cfg.dir_salidas / f"{slug}_{datetime.now():%Y%m%d_%H%M}.xlsx"
    )
    excel.exportar(
        ruta, p, jugadores, eventos, cfg,
        precision=informe.como_dict() if informe.total_revisados else None,
    )
    print(f"✓ {ruta}")
    print(informe.resumen(cfg.umbral_precision_objetivo))
    return 0


def cmd_estado(args, cfg) -> int:
    base = _base(cfg)
    p = base.partido(args.partido)
    if p is None:
        print(f"✗ No existe el partido {args.partido}.")
        return 1
    print(f"{p.nombre}\n")
    for paso in pipeline.estado(base, p.id):
        icono = {"listo": "✓", "corriendo": "…"}.get(paso["estado"], " ")
        print(f" [{icono}] {paso['nombre']:52} {paso['detalle']}")
    informe = mod_precision.calcular(base, p.id, cfg)
    print(f"\n{informe.resumen(cfg.umbral_precision_objetivo)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="Ruta a config.yaml")
    ap.add_argument("-v", "--verboso", action="store_true")
    sub = ap.add_subparsers(dest="comando", required=True)

    sub.add_parser("lista", help="Listar los partidos cargados.")

    n = sub.add_parser("nuevo", help="Crear un partido.")
    n.add_argument("--propio", required=True, help="Nombre del club propio.")
    n.add_argument("--rival", required=True, help="Nombre del club rival.")
    n.add_argument("--video", required=True, help="URL de YouTube, otra URL o ruta.")
    n.add_argument("--kit-propio", help='Ej: "rayas bordó y blancas, número blanco".')
    n.add_argument("--kit-rival", help='Ej: "bordó liso, número blanco".')
    n.add_argument(
        "--legible-propio", choices=["alta", "media", "baja"], default="media",
        help="¿Se lee el número en la espalda?",
    )
    n.add_argument(
        "--legible-rival", choices=["alta", "media", "baja"], default="media"
    )
    n.add_argument("--nombre", help="Nombre del partido.")
    n.add_argument("--fecha")
    n.add_argument("--competencia")
    n.add_argument("--plantel", help="CSV del plantel propio.")
    n.add_argument("--plantel-rival", help="CSV del plantel rival.")
    n.add_argument("--desde", help="mm:ss del video donde arrancar.")
    n.add_argument("--hasta", help="mm:ss del video donde terminar.")
    n.add_argument("--correr", action="store_true", help="Analizar al terminar.")
    n.add_argument("--pasos", help="Pasos a correr, separados por coma.")
    n.add_argument("--forzar", action="store_true")
    n.add_argument("--simulado", action="store_true")

    c = sub.add_parser("correr", help="Analizar un partido.")
    c.add_argument("--partido", type=int, required=True)
    c.add_argument(
        "--pasos",
        help=f"Separados por coma. Disponibles: {','.join(pipeline.PASOS)}",
    )
    c.add_argument("--forzar", action="store_true", help="Rehacer pasos ya listos.")
    c.add_argument(
        "--simulado", action="store_true",
        help="Datos inventados, sin tocar la API. Para probar el flujo.",
    )

    e = sub.add_parser("exportar", help="Generar el Excel.")
    e.add_argument("--partido", type=int, required=True)
    e.add_argument("--salida", help="Ruta del .xlsx.")

    s = sub.add_parser("estado", help="Ver en qué está un partido.")
    s.add_argument("--partido", type=int, required=True)

    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verboso else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    cfg = cargar_config(args.config)

    return {
        "lista": cmd_lista,
        "nuevo": cmd_nuevo,
        "correr": cmd_correr,
        "exportar": cmd_exportar,
        "estado": cmd_estado,
    }[args.comando](args, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
