"""De dónde sale el video y cómo se lo prepara.

Tres fuentes, y el sistema no se entera de la diferencia más adelante:

- **YouTube**: se le pasa la URL directo a Gemini. Solo videos públicos.
- **Archivo local**: se sube por la Files API.
- **Otra URL**: se baja con yt-dlp y queda como archivo local.

El detalle que importa: al pasar una URL de YouTube con ``start_offset`` y
``end_offset``, la API recorta los **cuadros** pero manda el **audio del video
entero** en cada pedido. En un partido de 80 minutos, con decenas de ventanas,
eso infla los tokens muchísimo. Por eso, si el video pasa del largo que fija
``video.descargar_si_dura_mas_de_s``, conviene bajarlo una vez y subirlo: ahí
el recorte sí ahorra de verdad.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..modelos import FuenteVideo

log = logging.getLogger(__name__)

RE_YOUTUBE = re.compile(
    r"(?:youtube\.com/(?:watch\?v=|live/|embed/|shorts/)|youtu\.be/)([\w-]{11})"
)


class ErrorVideo(RuntimeError):
    pass


def hay_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _cmd_ytdlp() -> list[str] | None:
    """Cómo invocar yt-dlp.

    Primero como módulo del mismo Python que está corriendo: cuando la
    aplicación se abre con doble clic, el entorno no está "activado" y el
    ejecutable yt-dlp no está en el PATH aunque esté instalado.
    """
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    if shutil.which("yt-dlp"):
        return ["yt-dlp"]
    return None


def hay_ytdlp() -> bool:
    return _cmd_ytdlp() is not None


def id_youtube(url: str) -> str | None:
    m = RE_YOUTUBE.search(url or "")
    return m.group(1) if m else None


def detectar_fuente(referencia: str) -> FuenteVideo:
    """Decide qué tipo de fuente es, a partir de lo que escribió la persona."""
    ref = (referencia or "").strip()
    if not ref:
        raise ErrorVideo("No me pasaste ningún video.")
    if id_youtube(ref):
        return FuenteVideo(tipo="youtube", referencia=ref)
    if ref.startswith(("http://", "https://")):
        return FuenteVideo(tipo="url", referencia=ref)
    p = Path(ref).expanduser()
    if p.exists():
        return FuenteVideo(tipo="archivo", referencia=str(p), ruta_local=str(p))
    raise ErrorVideo(
        f"No encontré {ref!r}. Pasá una URL de YouTube, otra URL de video, "
        "o la ruta de un archivo que exista."
    )


def duracion_s(ruta_o_url: str) -> float | None:
    """Largo del video en segundos. ffprobe para archivos, yt-dlp para URLs."""
    p = Path(ruta_o_url)
    if p.exists():
        if not shutil.which("ffprobe"):
            return None
        try:
            salida = subprocess.run(
                [
                    "ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", str(p),
                ],
                capture_output=True, text=True, timeout=60, check=True,
            ).stdout.strip()
            return float(salida)
        except (subprocess.SubprocessError, ValueError):
            return None
    cmd = _cmd_ytdlp()
    if cmd is None:
        return None
    try:
        salida = subprocess.run(
            [*cmd, "--no-warnings", "--no-playlist", "--dump-single-json",
             "--skip-download", ruta_o_url],
            capture_output=True, text=True, timeout=120, check=True,
        ).stdout
        return float(json.loads(salida).get("duration") or 0) or None
    except (subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        return None


def descargar(referencia: str, destino: Path, calidad: str = "480") -> Path:
    """Baja un video con yt-dlp.

    480p alcanza y sobra: el modelo reduce cada cuadro a una resolución fija,
    y un archivo más chico sube más rápido y entra en el límite de 2 GB de la
    Files API aunque el partido sea largo.
    """
    cmd_base = _cmd_ytdlp()
    if cmd_base is None:
        raise ErrorVideo(
            "Falta yt-dlp para bajar el video. Instalalo con: pip install yt-dlp"
        )
    destino.mkdir(parents=True, exist_ok=True)
    plantilla = str(destino / "%(id)s.%(ext)s")
    if hay_ffmpeg():
        formato = (
            f"bestvideo[height<={calidad}][ext=mp4]+bestaudio[ext=m4a]/"
            f"best[height<={calidad}][ext=mp4]/best[height<={calidad}]/best"
        )
    else:
        # Sin ffmpeg no se pueden unir video y audio por separado: se pide un
        # formato que ya venga en un solo archivo.
        formato = f"best[height<={calidad}][ext=mp4]/best[ext=mp4]/best"
    cmd = [
        *cmd_base, "--no-warnings", "--no-playlist",
        "-f", formato,
        "--merge-output-format", "mp4",
        "-o", plantilla, "--print", "after_move:filepath",
        referencia,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, check=True)
    except subprocess.CalledProcessError as exc:
        raise ErrorVideo(
            f"yt-dlp no pudo bajar el video: {(exc.stderr or '')[-400:]}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ErrorVideo("La descarga tardó más de una hora y la corté.") from exc
    lineas = [x for x in (res.stdout or "").strip().splitlines() if x.strip()]
    ruta = Path(lineas[-1].strip()) if lineas else None
    if not ruta or not ruta.exists():
        candidatos = sorted(
            [f for f in destino.iterdir() if f.suffix.lower() in (".mp4", ".mkv", ".webm")],
            key=lambda f: f.stat().st_mtime,
        )
        if not candidatos:
            raise ErrorVideo("yt-dlp terminó pero no encontré el archivo bajado.")
        ruta = candidatos[-1]
    return ruta


def cortar_clip(
    origen: Path, inicio_s: float, duracion: float, destino: Path
) -> Path:
    """Corta un clip con ffmpeg, para mostrarlo en la cola de revisión."""
    if not hay_ffmpeg():
        raise ErrorVideo(
            "Falta ffmpeg para cortar clips. En Windows: winget install ffmpeg"
        )
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        return destino
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{max(0.0, inicio_s):.3f}", "-i", str(origen),
        "-t", f"{duracion:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
        "-c:a", "aac", "-movflags", "+faststart",
        str(destino),
    ]
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=True)
    except subprocess.CalledProcessError as exc:
        raise ErrorVideo(f"ffmpeg falló: {exc.stderr[-300:]}") from exc
    return destino


def url_youtube_con_tiempo(url: str, t_segundos: float) -> str:
    """URL de YouTube que arranca en un momento dado."""
    vid = id_youtube(url)
    if not vid:
        return url
    return f"https://www.youtube.com/watch?v={vid}&t={int(max(0, t_segundos))}s"


def embed_youtube(url: str, inicio_s: float, fin_s: float | None = None) -> str:
    """URL de embed para mostrar un clip dentro de la interfaz."""
    vid = id_youtube(url)
    if not vid:
        return url
    partes = [f"start={int(max(0, inicio_s))}"]
    if fin_s is not None:
        partes.append(f"end={int(max(inicio_s + 1, fin_s))}")
    return f"https://www.youtube.com/embed/{vid}?{'&'.join(partes)}"


#: Los archivos subidos a la Files API se borran solos a las 48 horas.
#: Se vuelven a subir un poco antes, para no quedar a mitad de un análisis.
VIDA_SUBIDA = timedelta(hours=40)


def _subida_vigente(fuente: FuenteVideo) -> bool:
    """¿El archivo subido a la Files API sigue ahí?"""
    if not fuente.uri_subida or fuente.uri_subida == fuente.referencia:
        return False
    if not fuente.subido_en:
        return False
    try:
        cuando = datetime.fromisoformat(fuente.subido_en)
    except ValueError:
        return False
    if cuando.tzinfo is None:
        cuando = cuando.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - cuando < VIDA_SUBIDA


def _subir(fuente: FuenteVideo, cliente) -> None:
    log.info("Subiendo %s por la Files API", Path(fuente.ruta_local or "").name)
    fuente.uri_subida = cliente.subir_archivo(fuente.ruta_local)
    fuente.subido_en = datetime.now(timezone.utc).isoformat(timespec="seconds")


def preparar(
    fuente: FuenteVideo,
    cliente,
    dir_trabajo: Path,
    descargar_si_dura_mas_de_s: float = 1200,
) -> FuenteVideo:
    """Deja la fuente lista para analizar y devuelve el URI que usa Gemini.

    Al terminar, ``fuente.uri_subida`` tiene lo que se le pasa al modelo:
    la URL de YouTube, o el URI de la Files API si hubo que subir el archivo.

    Se puede llamar todas las veces que haga falta: no vuelve a bajar lo que
    ya está en disco ni a subir lo que sigue vigente.
    """
    dir_trabajo = Path(dir_trabajo)
    dir_trabajo.mkdir(parents=True, exist_ok=True)

    en_disco = bool(fuente.ruta_local) and Path(fuente.ruta_local).exists()
    if fuente.ruta_local and not en_disco:
        # El archivo ya no está (se borró la carpeta de trabajo, por ejemplo).
        if fuente.tipo == "archivo":
            raise ErrorVideo(f"No encuentro el archivo {fuente.ruta_local}.")
        fuente.ruta_local = None

    if fuente.duracion_s is None:
        fuente.duracion_s = duracion_s(
            fuente.ruta_local if en_disco else fuente.referencia
        )

    # -- YouTube ----------------------------------------------------------
    if fuente.tipo == "youtube":
        if en_disco:
            # Ya se había bajado: se usa el archivo.
            if not _subida_vigente(fuente):
                _subir(fuente, cliente)
            return fuente

        largo = fuente.duracion_s or 0.0
        conviene_bajar = (
            descargar_si_dura_mas_de_s > 0
            and largo > descargar_si_dura_mas_de_s
            and hay_ytdlp()
        )
        if conviene_bajar:
            # El audio entero viaja en cada pedido: para un partido completo
            # sale mucho más barato bajarlo una vez y subirlo.
            log.info(
                "El video dura %.0f min: lo bajo y lo subo para que el recorte "
                "ahorre tokens de verdad.",
                largo / 60,
            )
            try:
                fuente.ruta_local = str(descargar(fuente.referencia, dir_trabajo))
                _subir(fuente, cliente)
                return fuente
            except Exception as exc:  # noqa: BLE001 - bajar es una mejora, no un requisito
                log.warning(
                    "No pude bajarlo ni subirlo (%s). Sigo con la URL de "
                    "YouTube: andá mirando el consumo de tokens.", exc,
                )
                fuente.ruta_local = None
        fuente.uri_subida = fuente.referencia
        fuente.subido_en = None
        return fuente

    # -- Otra URL: hay que bajarla sí o sí ---------------------------------
    if fuente.tipo == "url" and not en_disco:
        log.info("Bajando el video de %s", fuente.referencia)
        fuente.ruta_local = str(descargar(fuente.referencia, dir_trabajo))
        fuente.duracion_s = fuente.duracion_s or duracion_s(fuente.ruta_local)
        fuente.uri_subida = None

    # -- Archivo local (o URL ya bajada) ----------------------------------
    if not fuente.ruta_local:
        raise ErrorVideo("La fuente es un archivo pero no sé dónde está.")
    if not _subida_vigente(fuente):
        _subir(fuente, cliente)
    return fuente


def ventanas(
    desde_s: float, hasta_s: float, largo_s: float, solape_s: float = 0.0
) -> list[tuple[float, float]]:
    """Parte un tramo en ventanas con solape, para no cortar jugadas al medio."""
    if hasta_s <= desde_s:
        return []
    largo_s = max(1.0, float(largo_s))
    solape_s = max(0.0, min(float(solape_s), largo_s / 2))
    salida: list[tuple[float, float]] = []
    t = float(desde_s)
    while t < hasta_s:
        fin = min(hasta_s, t + largo_s)
        salida.append((round(t, 3), round(fin, 3)))
        if fin >= hasta_s:
            break
        t = fin - solape_s
    return salida
