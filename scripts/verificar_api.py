#!/usr/bin/env python3
"""Chequea que la clave de Gemini ande y que el modelo exista.

Correlo antes de gastar cuota en un partido entero:

    python scripts/verificar_api.py

Con --video además prueba que el modelo pueda mirar un video, que es lo que
realmente importa acá:

    python scripts/verificar_api.py --video "https://www.youtube.com/watch?v=..."
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# La consola de Windows no usa UTF-8 por defecto y se cae al imprimir
# tildes o tildes de verificación: se fuerza acá.
for _flujo in (sys.stdout, sys.stderr):
    try:
        _flujo.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from rugby_stats.config import cargar_config  # noqa: E402
from rugby_stats.gemini.cliente import ClienteGemini, ErrorGemini, Pedido  # noqa: E402
from rugby_stats.gemini import video as video_mod  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Verifica la clave y el modelo.")
    ap.add_argument("--video", help="URL de YouTube o ruta de archivo para probar.")
    ap.add_argument(
        "--desde", type=float, default=0.0, help="Segundo de inicio de la prueba."
    )
    ap.add_argument(
        "--largo", type=float, default=20.0, help="Cuántos segundos mirar."
    )
    ap.add_argument("--config", help="Ruta a config.yaml.")
    args = ap.parse_args()

    cfg = cargar_config(args.config)

    print("Configuración")
    print(f"  proveedor        : {cfg.proveedor}")
    niveles = cfg.niveles or {"fuerte": [cfg.modelo] + cfg.modelos_alternativos}
    for nivel, modelos in niveles.items():
        print(f"  modelos {nivel:8}: {', '.join(modelos)}")
    print(f"  caché            : {cfg.dir_cache} ({'sí' if cfg.usar_cache else 'no'})")
    print(f"  ffmpeg / yt-dlp  : {video_mod.hay_ffmpeg()} / {video_mod.hay_ytdlp()}")
    print()

    if not cfg.hay_api_key:
        print(f"✗ No hay {cfg.nombre_variable_clave} en el archivo .env.")
        print("  Hacé doble clic en 2_poner_clave.bat y pegala ahí.")
        return 1
    print("✓ Hay clave cargada.")

    from rugby_stats.gemini.cliente import crear_cliente

    cliente = crear_cliente(cfg)
    cliente.cache.activa = False  # para verificar queremos ir de verdad a la API
    cliente.al_avisar = lambda texto: print(f"    ({texto})")

    print("\nProbando una respuesta de texto…")
    try:
        info = cliente.verificar()
    except ErrorGemini as exc:
        print(f"✗ La API no contestó: {exc}")
        return 1
    print(f"✓ Contestó el modelo {info['modelo']}: {info['respuesta']}")
    if info.get("costo_usd"):
        print(f"  (costó US$ {info['costo_usd']:.5f})")

    if cfg.proveedor == "claude":
        if not args.video:
            print("\nListo: la clave anda. Para probar con video y medir cuánto "
                  "cuesta, usá 6_prueba_real.bat.")
            return 0
        return _probar_video_claude(cfg, cliente, args)

    if not args.video:
        print("\nListo. Para probar la parte de video, agregá --video.")
        return 0

    print(f"\nProbando video: {args.video}")
    try:
        fuente = video_mod.detectar_fuente(args.video)
    except video_mod.ErrorVideo as exc:
        print(f"✗ {exc}")
        return 1
    print(f"  tipo: {fuente.tipo}")

    try:
        fuente = video_mod.preparar(
            fuente, cliente, cfg.dir_video, cfg.descargar_si_dura_mas_de_s
        )
    except video_mod.ErrorVideo as exc:
        print(f"✗ No pude preparar el video: {exc}")
        return 1
    if fuente.duracion_s:
        print(f"  dura: {fuente.duracion_s / 60:.1f} min")
    print(f"  uri que usa el modelo: {str(fuente.uri_subida)[:70]}")

    desde, hasta = args.desde, args.desde + args.largo
    print(f"  mirando del segundo {desde:.0f} al {hasta:.0f} a 2 cuadros/s…")
    try:
        r = cliente.pedir(
            Pedido(
                prompt=(
                    "Mirá este fragmento de un partido de rugby y respondé "
                    'solo con este JSON: {"hay_rugby": true/false, '
                    '"cuantos_jugadores_se_ven": entero, '
                    '"se_leen_numeros_de_camiseta": true/false, '
                    '"hay_marcador_en_pantalla": true/false, '
                    '"que_pasa": "una frase corta"}'
                ),
                video_uri=fuente.uri_subida,
                inicio_s=desde,
                fin_s=hasta,
                fps=2.0,
                resolucion="MEDIA_RESOLUTION_LOW",
                esquema={
                    "type": "object",
                    "properties": {
                        "hay_rugby": {"type": "boolean"},
                        "cuantos_jugadores_se_ven": {"type": "integer"},
                        "se_leen_numeros_de_camiseta": {"type": "boolean"},
                        "hay_marcador_en_pantalla": {"type": "boolean"},
                        "que_pasa": {"type": "string"},
                    },
                },
                etiqueta="verificación de video",
                nivel="liviano",
            )
        )
    except ErrorGemini as exc:
        print(f"✗ Falló el pedido de video: {exc}")
        return 1

    datos = r.json()
    print("✓ El modelo miró el video y contestó:")
    for k, v in (datos or {}).items():
        print(f"    {k}: {v}")
    if r.tokens_entrada:
        print(f"  tokens de entrada: {r.tokens_entrada:,}")
        por_seg = r.tokens_entrada / max(1.0, hasta - desde)
        print(f"  ≈ {por_seg:.0f} tokens por segundo de video")
        if fuente.tipo == "youtube" and por_seg > 400:
            print(
                "  ⚠ Son muchos más de los 100/seg esperados en resolución "
                "baja. Con URLs de YouTube el audio del video entero viaja en "
                "cada pedido: para un partido completo conviene bajarlo y "
                "subirlo (lo hace solo si pasa de "
                f"{cfg.descargar_si_dura_mas_de_s / 60:.0f} min y hay yt-dlp)."
            )
    print("\nListo: la API anda y puede mirar video.")
    return 0


def _probar_video_claude(cfg, cliente, args) -> int:
    """Baja unos segundos del video, saca cuadros y le pregunta a Claude."""
    from rugby_stats.gemini.cliente import Pedido

    print(f"\nProbando video: {args.video}")
    try:
        fuente = video_mod.detectar_fuente(args.video)
        desde = args.desde or 600.0
        hasta = desde + max(10.0, args.largo)
        print(f"  bajando del segundo {desde:.0f} al {hasta:.0f}…")
        fuente = video_mod.preparar(
            fuente, cliente, cfg.dir_video, 0, desde_s=desde, hasta_s=hasta,
            calidad=cfg.calidad_descarga,
        )
    except video_mod.ErrorVideo as exc:
        print(f"✗ No pude preparar el video: {exc}")
        return 1
    try:
        r = cliente.pedir(
            Pedido(
                prompt=(
                    "Mirá estos cuadros de un partido de rugby y respondé con el "
                    'JSON: hay_rugby, cuantos_jugadores_se_ven, '
                    "se_leen_numeros_de_camiseta, hay_marcador_en_pantalla, "
                    "que_pasa (una frase corta)."
                ),
                video_uri=fuente.uri_subida,
                inicio_s=desde,
                fin_s=hasta,
                fps=1.0,
                ancho=768,
                esquema={
                    "type": "object",
                    "properties": {
                        "hay_rugby": {"type": "boolean"},
                        "cuantos_jugadores_se_ven": {"type": "integer"},
                        "se_leen_numeros_de_camiseta": {"type": "boolean"},
                        "hay_marcador_en_pantalla": {"type": "boolean"},
                        "que_pasa": {"type": "string"},
                    },
                    "required": ["hay_rugby", "que_pasa"],
                },
                etiqueta="verificación de video",
                nivel="liviano",
            )
        )
    except ErrorGemini as exc:
        print(f"✗ Falló el pedido de video: {exc}")
        return 1
    print("✓ Claude miró los cuadros y contestó:")
    for k, v in (r.json() or {}).items():
        print(f"    {k}: {v}")
    print(f"  tokens de entrada: {r.tokens_entrada:,} · costó US$ {getattr(r, 'costo_usd', 0):.4f}")
    print("\nListo: la clave anda y Claude puede mirar el video.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
