#!/usr/bin/env python3
"""Prueba real con Claude: cuánto cuesta y qué contesta, con 2 minutos.

Corre el análisis completo (todos los pasos) sobre un pedazo corto del
partido cargado, en una base de datos aparte —no toca nada del partido de
verdad—, mide cuánto costó de verdad y lo proyecta al partido entero.

    python scripts/prueba_claude.py               # el último partido cargado
    python scripts/prueba_claude.py --minutos 3   # un pedazo más largo
    python scripts/prueba_claude.py --partido 1

Deja un informe en ``salidas/diagnostico/`` con cada pedido: el modelo, los
tokens, lo que costó y lo que contestó. La clave nunca se escribe ahí.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

for _flujo in (sys.stdout, sys.stderr):
    try:
        _flujo.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from rugby_stats import costos  # noqa: E402
from rugby_stats.analisis import pipeline  # noqa: E402
from rugby_stats.config import cargar_config  # noqa: E402
from rugby_stats.db import BaseDatos, ruta_por_defecto  # noqa: E402
from rugby_stats.gemini.cliente import crear_cliente  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Prueba real con Claude.")
    ap.add_argument("--partido", type=int, help="Partido a usar (por defecto, el último).")
    ap.add_argument("--minutos", type=float, default=2.0, help="Cuánto video analizar.")
    ap.add_argument("--config")
    # parse_known_args: 6_prueba_real.bat puede pasar --video, que acá no se usa.
    args, _ = ap.parse_known_args()

    cfg = cargar_config(args.config)
    print("=" * 62)
    print("  rugby-stats-ia — prueba real con Claude")
    print("=" * 62)

    if cfg.proveedor != "claude":
        print("\nconfig.yaml dice 'proveedor: gemini'. Esta prueba es para Claude.")
        return 1
    if not cfg.clave_anthropic:
        print("\n✗ No hay ANTHROPIC_API_KEY en el archivo .env.")
        print("  Hacé doble clic en 2_poner_clave.bat y volvé a probar.")
        return 1

    base_real = BaseDatos(ruta_por_defecto())
    partido = base_real.partido(args.partido) if args.partido else None
    if partido is None:
        partidos = base_real.partidos()
        partido = partidos[0] if partidos else None
    if partido is None:
        print("\n✗ No hay ningún partido cargado. Cargalo en la aplicación primero.")
        return 1
    plantel = base_real.plantel(partido.id)
    if not [j for j in plantel if j.equipo == partido.equipo_propio.nombre]:
        print("\n✗ El partido no tiene plantel del equipo propio.")
        return 1

    # Un pedazo del tramo elegido, empezando un poco adentro (los primeros
    # segundos del tramo suelen ser la formación o una pausa).
    desde = (partido.analizar_desde_s or 0.0) + 30.0
    hasta = desde + args.minutos * 60.0
    if partido.analizar_hasta_s and hasta > partido.analizar_hasta_s:
        hasta = partido.analizar_hasta_s
        desde = max(partido.analizar_desde_s or 0.0, hasta - args.minutos * 60.0)

    print(f"\nPartido : {partido.nombre}")
    print(f"Pedazo  : del segundo {desde:.0f} al {hasta:.0f} del video "
          f"({(hasta - desde) / 60:.1f} min)")
    print(f"Modelos : fuerte {cfg.niveles_claude['fuerte'][0]}, "
          f"liviano {cfg.niveles_claude['liviano'][0]}")
    est = costos.estimar(hasta - desde, cfg)
    print(f"Costo estimado de la prueba: US$ {est.usd:.2f}\n")

    # Base aparte: la prueba no toca el partido de verdad.
    carpeta_tmp = Path(tempfile.mkdtemp(prefix="prueba_claude_"))
    base = BaseDatos(carpeta_tmp / "prueba.db")
    copia = partido
    copia.id = None
    copia.analizar_desde_s = desde
    copia.analizar_hasta_s = hasta
    copia.nombre = f"[prueba] {partido.nombre}"
    pid = base.guardar_partido(copia)
    for j in plantel:
        j.id = None
    base.guardar_plantel(pid, [j for j in plantel if j.equipo == partido.equipo_propio.nombre])
    rivales = [j for j in plantel if j.equipo == partido.equipo_rival.nombre]
    if rivales:
        base.guardar_plantel(pid, rivales)

    cliente = crear_cliente(cfg)
    cliente.cache.activa = False  # la prueba siempre va de verdad a la API
    cliente.al_avisar = lambda texto: print(f"      ({texto})", flush=True)
    ultimo = {"texto": ""}

    def progreso(texto: str, fraccion: float) -> None:
        if texto != ultimo["texto"]:
            ultimo["texto"] = texto
            print(f"  · {texto}", flush=True)

    t0 = time.monotonic()
    error = None
    res = None
    try:
        res = pipeline.correr(base, cliente, pid, cfg, progreso=progreso)
    except Exception as exc:  # noqa: BLE001 - se informa
        error = exc
        print(f"\n✗ Se cortó: {exc}")
    segundos = time.monotonic() - t0

    gasto = float(getattr(cliente, "gasto_usd", 0.0) or 0.0) or base.gasto_partido(pid)
    minutos = (hasta - desde) / 60
    entero = costos.estimar(partido.video.duracion_s or 80 * 60, cfg)
    factor = gasto / est.usd if est.usd else 1.0
    proyectado = entero.usd * factor

    eventos = base.eventos(pid)
    fichas = base.fichas(pid)
    resumen = {
        "cuando": datetime.now().isoformat(timespec="seconds"),
        "partido": partido.nombre,
        "pedazo_s": [desde, hasta],
        "modelos": cfg.niveles_claude,
        "python": platform.python_version(),
        "sistema": platform.platform(),
        "segundos": round(segundos, 1),
        "error": f"{type(error).__name__}: {error}" if error else None,
        "gasto_usd": round(gasto, 4),
        "estimado_usd": round(est.usd, 4),
        "factor_real_sobre_estimado": round(factor, 3),
        "partido_entero_estimado_usd": round(entero.usd, 2),
        "partido_entero_proyectado_usd": round(proyectado, 2),
        "eventos": len(eventos),
        "eventos_pendientes": len([e for e in eventos if e.pendiente]),
        "fichas": len(fichas),
        "fichas_con_imagen": len([f for f in fichas if f.imagen]),
        "tramos": len(base.tramos(pid)),
        "relato": len(base.transcripcion(pid)),
        "gasto_por_modelo": cliente.resumen_gasto() if hasattr(cliente, "resumen_gasto") else {},
        "pedidos": getattr(cliente, "registro", []),
        "errores_de_la_api": getattr(cliente, "errores_vistos", []),
        "eventos_detalle": [
            {"t": e.t_segundos, "reloj": e.reloj, "n": e.jugador_numero, "accion": e.accion,
             "resultado": e.resultado, "confianza": e.confianza, "pendiente": e.pendiente,
             "nota": e.nota}
            for e in eventos
        ],
        "fichas_detalle": [
            {"n": f.jugador_numero, "t": f.t_segundos, "confianza": f.confianza,
             "rasgos": f.rasgos, "imagen": Path(f.imagen).name if f.imagen else ""}
            for f in fichas
        ],
    }

    carpeta = cfg.dir_salidas / "diagnostico"
    carpeta.mkdir(parents=True, exist_ok=True)
    sello = datetime.now().strftime("%Y%m%d_%H%M%S")
    clave = cfg.clave_anthropic or ""
    texto_json = json.dumps(resumen, ensure_ascii=False, indent=2, default=str)
    if clave:
        texto_json = texto_json.replace(clave, "<CLAVE>")
    (carpeta / f"claude_{sello}.json").write_text(texto_json, encoding="utf-8")
    # Las fichas visuales, para poder mirarlas.
    destino_fichas = carpeta / f"claude_{sello}_fichas"
    for f in fichas:
        if f.imagen and Path(f.imagen).exists():
            destino_fichas.mkdir(exist_ok=True)
            shutil.copy(f.imagen, destino_fichas / Path(f.imagen).name)

    lineas = [
        "",
        "=" * 62,
        f"Costó        : US$ {gasto:.3f} ({minutos:.1f} min de video, {segundos / 60:.1f} min de espera)",
        f"Estimado era : US$ {est.usd:.3f}",
        f"Partido entero, proyectado con lo medido: US$ {proyectado:.2f}",
        f"Tope por partido en config.yaml: US$ {cfg.tope_usd_por_partido:.2f}",
        "",
        f"Encontró     : {len(eventos)} eventos ({resumen['eventos_pendientes']} pendientes), "
        f"{len(fichas)} fichas ({resumen['fichas_con_imagen']} con foto), "
        f"{resumen['relato']} fragmentos de relato",
        "=" * 62,
        f"Informe: {carpeta / f'claude_{sello}.json'}",
    ]
    if proyectado > cfg.tope_usd_por_partido > 0:
        lineas.insert(5, "ATENCIÓN: el partido entero se pasaría del tope configurado.")
    print("\n".join(lineas))
    return 0 if error is None else 2


if __name__ == "__main__":
    raise SystemExit(main())
