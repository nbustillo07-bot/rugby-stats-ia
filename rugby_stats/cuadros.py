"""Cuadros sueltos del video, para los proveedores que no reciben video.

Claude no mira video: mira imágenes. Así que para cada pedido se sacan
cuadros del tramo que hace falta, a los cuadros por segundo y al ancho que
diga la configuración, y se guardan en disco para no volver a sacarlos.

Cada cuadro sale con su momento exacto en el video, y ese momento se le dice
al modelo al lado de la imagen. Por eso los tiempos que devuelve son más
precisos que los que devolvía Gemini mirando el video entero.

ffmpeg: se usa el que haya instalado y, si no hay, el que trae la librería
``imageio-ffmpeg`` (se instala con pip y anda en Windows sin hacer nada).
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


class ErrorCuadros(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# ffmpeg
# ---------------------------------------------------------------------------


def ffmpeg_exe() -> str | None:
    """Ruta a un ffmpeg que funcione, o None."""
    instalado = shutil.which("ffmpeg")
    if instalado:
        return instalado
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - si no está, no está
        return None


def _correr(cmd: list[str], que: str, timeout: int = 900) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=True,
            encoding="utf-8", errors="replace",
        )
    except subprocess.CalledProcessError as exc:
        raise ErrorCuadros(f"ffmpeg falló al {que}: {(exc.stderr or '')[-400:]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ErrorCuadros(f"ffmpeg tardó demasiado al {que}.") from exc


def duracion_video(ruta: Path | str) -> float | None:
    """Largo de un archivo de video, leyendo lo que informa ffmpeg."""
    exe = ffmpeg_exe()
    if not exe or not Path(ruta).exists():
        return None
    res = subprocess.run(
        [exe, "-hide_banner", "-i", str(ruta)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", res.stderr or "")
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


# ---------------------------------------------------------------------------
# Tokens de imagen
# ---------------------------------------------------------------------------


def alto_para(ancho: int, relacion: float = 16 / 9) -> int:
    """Alto de un cuadro de ese ancho, redondeado a par (lo pide ffmpeg)."""
    alto = int(round(ancho / relacion))
    return alto + (alto % 2)


def tokens_imagen(ancho: int, alto: int) -> int:
    """Lo que cobra Claude por una imagen: un token por cada bloque de 28x28."""
    return math.ceil(ancho / 28) * math.ceil(alto / 28)


# ---------------------------------------------------------------------------
# Sacar cuadros
# ---------------------------------------------------------------------------


@dataclass
class Cuadro:
    t: float  # segundo del video completo
    ruta: Path
    ancho: int
    alto: int

    @property
    def tokens(self) -> int:
        return tokens_imagen(self.ancho, self.alto)


def ajustar_fps(inicio_s: float, fin_s: float, fps: float, maximo: int) -> float:
    """Baja los cuadros por segundo si la ventana pediría más de ``maximo``."""
    largo = max(0.0, fin_s - inicio_s)
    if largo <= 0 or fps <= 0:
        return fps
    if largo * fps > maximo:
        return maximo / largo
    return fps


def extraer(
    video: Path | str,
    inicio_s: float,
    fin_s: float,
    fps: float,
    ancho: int,
    dir_cache: Path | str,
    desfase_archivo_s: float = 0.0,
    maximo: int = 80,
) -> list[Cuadro]:
    """Saca cuadros de ``[inicio_s, fin_s]`` (segundos del video completo).

    ``desfase_archivo_s`` es en qué segundo del video completo arranca el
    archivo, por si se bajó solo un pedazo: el segundo 0 del archivo es el
    segundo ``desfase_archivo_s`` del partido.
    """
    video = Path(video)
    if not video.exists():
        raise ErrorCuadros(f"No encuentro el video {video}.")
    exe = ffmpeg_exe()
    if exe is None:
        raise ErrorCuadros(
            "Falta ffmpeg para sacar cuadros del video. Volvé a correr "
            "1_instalar.bat: se instala solo con imageio-ffmpeg."
        )
    fps = ajustar_fps(inicio_s, fin_s, fps, maximo)
    if fin_s <= inicio_s or fps <= 0:
        return []

    clave = hashlib.sha256(
        f"{video.resolve()}|{video.stat().st_size}|{video.stat().st_mtime_ns}|"
        f"{inicio_s:.3f}|{fin_s:.3f}|{fps:.4f}|{ancho}|{desfase_archivo_s:.3f}".encode()
    ).hexdigest()[:20]
    carpeta = Path(dir_cache) / clave
    listo = carpeta / ".listo"
    if not listo.exists():
        carpeta.mkdir(parents=True, exist_ok=True)
        for viejo in carpeta.glob("*.jpg"):
            viejo.unlink()
        desde_archivo = max(0.0, inicio_s - desfase_archivo_s)
        filtro = f"fps={fps:.6f}"
        if ancho:
            filtro += f",scale={int(ancho)}:-2"
        _correr(
            [
                exe, "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{desde_archivo:.3f}", "-i", str(video),
                "-t", f"{fin_s - inicio_s:.3f}",
                "-vf", filtro, "-q:v", "4",
                str(carpeta / "c_%05d.jpg"),
            ],
            "sacar cuadros",
        )
        listo.write_text("ok", encoding="utf-8")

    archivos = sorted(carpeta.glob("c_*.jpg"))
    if not archivos:
        return []
    ancho_real, alto_real = _medidas(archivos[0], ancho)
    return [
        Cuadro(t=round(inicio_s + i / fps, 3), ruta=f, ancho=ancho_real, alto=alto_real)
        for i, f in enumerate(archivos)
        if inicio_s + i / fps <= fin_s + 1e-6
    ]


def _medidas(ruta: Path, ancho_pedido: int) -> tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(ruta) as im:
            return im.size
    except Exception:  # noqa: BLE001
        return ancho_pedido or 640, alto_para(ancho_pedido or 640)


def un_cuadro(
    video: Path | str,
    t: float,
    destino: Path | str,
    ancho: int = 1280,
    desfase_archivo_s: float = 0.0,
) -> Path:
    """Un solo cuadro, en alta, del segundo ``t`` del video completo."""
    exe = ffmpeg_exe()
    if exe is None:
        raise ErrorCuadros("Falta ffmpeg.")
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    if not destino.exists():
        _correr(
            [
                exe, "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{max(0.0, t - desfase_archivo_s):.3f}", "-i", str(video),
                "-frames:v", "1", "-vf", f"scale={int(ancho)}:-2", "-q:v", "2",
                str(destino),
            ],
            "sacar un cuadro",
        )
    return destino


def recortar(
    imagen: Path | str,
    caja: list[float] | tuple[float, ...],
    destino: Path | str,
    alto_final: int = 384,
    margen: float = 0.15,
) -> Path | None:
    """Recorta un jugador de un cuadro.

    ``caja`` viene del modelo en coordenadas relativas: ``[x1, y1, x2, y2]``
    entre 0 y 1. Se agranda un poco (``margen``) porque las cajas que
    devuelven los modelos suelen quedar justas.
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        x1, y1, x2, y2 = (float(v) for v in caja)
    except (TypeError, ValueError):
        return None
    # Se le pide al modelo 0 a 1. Si contesta en escala 0-1000 (es la
    # costumbre de algunos modelos), se corrige; si contesta otra cosa, no se
    # adivina: mejor sin recorte que con el jugador equivocado.
    escala = max(x1, y1, x2, y2)
    if escala > 1.5:
        if escala > 1000:
            return None
        x1, y1, x2, y2 = x1 / 1000, y1 / 1000, x2 / 1000, y2 / 1000
    with Image.open(imagen) as im:
        w, h = im.size
        x1, x2 = sorted((x1, x2))
        y1, y2 = sorted((y1, y2))
        if x2 - x1 < 0.01 or y2 - y1 < 0.01:
            return None
        mx, my = (x2 - x1) * margen, (y2 - y1) * margen
        caja_px = (
            max(0, int((x1 - mx) * w)),
            max(0, int((y1 - my) * h)),
            min(w, int((x2 + mx) * w)),
            min(h, int((y2 + my) * h)),
        )
        if caja_px[2] - caja_px[0] < 8 or caja_px[3] - caja_px[1] < 8:
            return None
        recorte = im.crop(caja_px)
        rw, rh = recorte.size
        if rh != alto_final:
            recorte = recorte.resize((max(1, int(rw * alto_final / rh)), alto_final))
        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        recorte.convert("RGB").save(destino, "JPEG", quality=88)
    return destino


# ---------------------------------------------------------------------------
# Zoom que sigue al contacto
# ---------------------------------------------------------------------------


def caja_valida(caja) -> tuple[float, float, float, float] | None:
    """Una caja ``[x1, y1, x2, y2]`` entre 0 y 1 (o 0-1000), o None."""
    if not isinstance(caja, (list, tuple)) or len(caja) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(v) for v in caja)
    except (TypeError, ValueError):
        return None
    escala = max(x1, y1, x2, y2)
    if escala > 1.5:
        if escala > 1000:
            return None
        x1, y1, x2, y2 = x1 / 1000, y1 / 1000, x2 / 1000, y2 / 1000
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    if min(x1, y1) < -0.05 or max(x2, y2) > 1.05 or x2 - x1 < 0.005 or y2 - y1 < 0.005:
        return None
    return (max(0.0, x1), max(0.0, y1), min(1.0, x2), min(1.0, y2))


def interpolar_caja(
    pista: list[tuple[float, tuple[float, float, float, float]]], t: float
) -> tuple[float, float, float, float] | None:
    """Dónde está el contacto en el segundo ``t``, entre los puntos conocidos."""
    if not pista:
        return None
    pista = sorted(pista, key=lambda p: p[0])
    if t <= pista[0][0]:
        return pista[0][1]
    if t >= pista[-1][0]:
        return pista[-1][1]
    for (t0, c0), (t1, c1) in zip(pista, pista[1:]):
        if t0 <= t <= t1:
            f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return tuple(a + (b - a) * f for a, b in zip(c0, c1))  # type: ignore[return-value]
    return pista[-1][1]


def ventana_zoom(
    caja: tuple[float, float, float, float],
    escala: float = 2.2,
    minimo: float = 0.3,
    relacion: float = 16 / 9,
) -> tuple[float, float, float, float]:
    """La zona a recortar alrededor de la caja, en proporción de pantalla.

    Se agranda ``escala`` veces (para que entren los que llegan al contacto y
    se levantan después) y nunca baja de ``minimo`` del ancho del cuadro.
    """
    x1, y1, x2, y2 = caja
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    # Ancho relativo; el alto relativo sale de la proporción (el cuadro es 16:9
    # y el recorte también, así que en relativo son iguales).
    lado = max((x2 - x1) * escala, (y2 - y1) * escala, minimo)
    lado = min(1.0, lado)
    _ = relacion
    a = min(max(cx - lado / 2, 0.0), 1.0 - lado)
    b = min(max(cy - lado / 2, 0.0), 1.0 - lado)
    return (a, b, a + lado, b + lado)


def extraer_zoom(
    video: Path | str,
    inicio_s: float,
    fin_s: float,
    fps: float,
    pista: list[tuple[float, tuple[float, float, float, float]]],
    ancho_salida: int,
    dir_cache: Path | str,
    desfase_archivo_s: float = 0.0,
    maximo: int = 80,
    ancho_fuente: int = 1920,
    escala: float = 2.2,
    minimo: float = 0.3,
) -> list[Cuadro]:
    """Cuadros recortados alrededor del contacto, que siguen la ``pista``.

    Se sacan los cuadros en alta (``ancho_fuente``), y de cada uno se recorta
    la zona donde está el contacto en ese momento, ampliada a
    ``ancho_salida``. Así los botines, el casco y el número se ven varias
    veces más grandes que en el cuadro entero, por el mismo costo.
    """
    from PIL import Image

    base = extraer(video, inicio_s, fin_s, fps, ancho_fuente, dir_cache,
                   desfase_archivo_s, maximo)
    if not base or not pista:
        return base
    alto_salida = alto_para(ancho_salida)
    salida: list[Cuadro] = []
    for c in base:
        caja = interpolar_caja(pista, c.t)
        if caja is None:
            continue
        zx1, zy1, zx2, zy2 = ventana_zoom(caja, escala, minimo)
        nombre = hashlib.sha256(
            f"{c.ruta}|{zx1:.3f}|{zy1:.3f}|{zx2:.3f}|{zy2:.3f}|{ancho_salida}".encode()
        ).hexdigest()[:16]
        destino = Path(dir_cache) / "zoom" / f"{nombre}.jpg"
        if not destino.exists():
            destino.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(c.ruta) as im:
                w, h = im.size
                recorte = im.crop((int(zx1 * w), int(zy1 * h), int(zx2 * w), int(zy2 * h)))
                recorte = recorte.resize((ancho_salida, alto_salida))
                recorte.convert("RGB").save(destino, "JPEG", quality=85)
        salida.append(Cuadro(t=c.t, ruta=destino, ancho=ancho_salida, alto=alto_salida))
    return salida


# ---------------------------------------------------------------------------
# Hojas con las fotos de cada jugador
# ---------------------------------------------------------------------------


def _fuente(tamano: int):
    from PIL import ImageFont

    for nombre in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(nombre, tamano)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=tamano)
    except TypeError:  # Pillow viejo
        return ImageFont.load_default()


def hoja_referencias(
    jugadores: list[tuple[str, list[Path | str]]],
    destino: Path | str,
    alto_foto: int = 200,
    max_fotos: int = 3,
) -> Path | None:
    """Una imagen con varios jugadores: una fila por jugador con sus fotos.

    Cada fila lleva el nombre escrito arriba. Juntar a cuatro o cinco
    jugadores por imagen deja mandar varias fotos de cada uno sin pasar el
    límite de imágenes por pedido.
    """
    from PIL import Image, ImageDraw

    filas = []
    for etiqueta, rutas in jugadores:
        fotos = []
        for r in rutas[:max_fotos]:
            try:
                with Image.open(r) as im:
                    w, h = im.size
                    fotos.append(im.convert("RGB").resize(
                        (max(1, int(w * alto_foto / h)), alto_foto)))
            except Exception:  # noqa: BLE001 - una foto rota no frena la hoja
                continue
        if fotos:
            filas.append((etiqueta, fotos))
    if not filas:
        return None
    barra, sep = 34, 6
    ancho = max(sum(f.size[0] for f in fotos) + sep * (len(fotos) + 1)
                for _, fotos in filas)
    ancho = max(ancho, 320)
    alto = sum(barra + alto_foto + sep for _ in filas) + sep
    hoja = Image.new("RGB", (ancho, alto), (30, 30, 30))
    dibujo = ImageDraw.Draw(hoja)
    fuente = _fuente(22)
    y = sep
    for etiqueta, fotos in filas:
        dibujo.rectangle([0, y, ancho, y + barra - 4], fill=(250, 220, 60))
        dibujo.text((8, y + 3), etiqueta, fill=(0, 0, 0), font=fuente)
        y += barra
        x = sep
        for f in fotos:
            hoja.paste(f, (x, y))
            x += f.size[0] + sep
        y += alto_foto + sep
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    hoja.save(destino, "JPEG", quality=85)
    return destino
