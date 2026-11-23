"""El relato, desde los subtítulos automáticos de YouTube.

Claude no escucha audio. Pero YouTube genera subtítulos automáticos de casi
todos los videos con relato, con el momento de cada frase, y yt-dlp los baja
sin bajar el video. Tienen errores en los apellidos ("Shofre" por "Joffre"):
eso se corrige después con el plantel como vocabulario.

Si el video no tiene subtítulos, el análisis sigue sin relato: el relato
ayuda a saber quién, pero el video manda.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

RE_TIEMPO = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[.,](\d{3})"
)
RE_TIEMPO_CORTO = re.compile(
    r"(\d{1,2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{1,2}):(\d{2})[.,](\d{3})"
)
RE_ETIQUETA = re.compile(r"<[^>]+>")
RE_PALABRA_CON_TIEMPO = re.compile(r"<\d{1,2}:\d{2}:\d{2}[.,]\d{3}>")


@dataclass
class Linea:
    inicio_s: float
    fin_s: float
    texto: str


def _seg(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def leer_vtt(texto: str) -> list[Linea]:
    """Pasa un .vtt a líneas con tiempo, sin repeticiones.

    Los subtítulos automáticos de YouTube van "rodando": cada frase aparece
    dos o tres veces mientras se escribe la siguiente. Se queda solo con las
    líneas que traen palabras nuevas.
    """
    lineas: list[Linea] = []
    bloques = re.split(r"\n\s*\n", texto.replace("\r\n", "\n"))
    hay_palabras_con_tiempo = bool(RE_PALABRA_CON_TIEMPO.search(texto))
    for bloque in bloques:
        filas = [f for f in bloque.split("\n")]
        tiempo = None
        resto: list[str] = []
        for f in filas:
            m = RE_TIEMPO.search(f)
            if m and tiempo is None:
                tiempo = (_seg(*m.groups()[:4]), _seg(*m.groups()[4:]))
                continue
            m2 = RE_TIEMPO_CORTO.search(f)
            if m2 and tiempo is None:
                g = m2.groups()
                tiempo = (_seg("0", *g[:3]), _seg("0", *g[3:]))
                continue
            if tiempo is not None:
                resto.append(f)
        if tiempo is None:
            continue
        inicio, fin = tiempo
        if hay_palabras_con_tiempo:
            # Solo las filas que traen palabras nuevas (con marcas de tiempo).
            nuevas = [f for f in resto if RE_PALABRA_CON_TIEMPO.search(f)]
            candidatas = nuevas
        else:
            candidatas = resto
        for f in candidatas:
            limpio = " ".join(RE_ETIQUETA.sub("", f).split())
            if not limpio:
                continue
            if lineas and limpio == lineas[-1].texto:
                lineas[-1].fin_s = max(lineas[-1].fin_s, fin)
                continue
            lineas.append(Linea(inicio, fin, limpio))
    return lineas


def agrupar(
    lineas: list[Linea], desde_s: float = 0.0, hasta_s: float | None = None,
    largo_s: float = 20.0,
) -> list[dict]:
    """Junta las líneas en fragmentos de unos ``largo_s`` segundos."""
    fragmentos: list[dict] = []
    actual: dict | None = None
    for ln in lineas:
        if ln.fin_s < desde_s or (hasta_s is not None and ln.inicio_s > hasta_s):
            continue
        if actual is None or ln.inicio_s - actual["inicio_s"] >= largo_s:
            if actual:
                fragmentos.append(actual)
            actual = {
                "inicio_s": round(ln.inicio_s, 2),
                "fin_s": round(ln.fin_s, 2),
                "texto": ln.texto,
                "jugadores_nombrados": [],
            }
        else:
            actual["texto"] += " " + ln.texto
            actual["fin_s"] = round(max(actual["fin_s"], ln.fin_s), 2)
    if actual:
        fragmentos.append(actual)
    return fragmentos


def descargar(url: str, destino: Path | str, cmd_ytdlp: list[str]) -> Path | None:
    """Baja los subtítulos en castellano (automáticos o hechos a mano)."""
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    antes = set(destino.glob("*.vtt"))
    cmd = [
        *cmd_ytdlp, "--no-warnings", "--no-playlist", "--skip-download",
        "--write-subs", "--write-auto-subs",
        "--sub-langs", "es.*,es,es-419,-live_chat",
        "--sub-format", "vtt",
        "-o", str(destino / "%(id)s.%(ext)s"),
        url,
    ]
    try:
        subprocess.run(
            cmd, capture_output=True, text=True, timeout=300, check=True,
            encoding="utf-8", errors="replace",
        )
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("No pude bajar los subtítulos: %s", exc)
    nuevos = sorted(set(destino.glob("*.vtt")) | antes, key=lambda f: f.stat().st_mtime)
    # Preferencia: subtítulos hechos a mano, después los automáticos.
    for f in reversed(nuevos):
        if "orig" not in f.name and ".es" in f.name:
            return f
    return nuevos[-1] if nuevos else None


def relato_desde_youtube(
    url: str, destino: Path | str, cmd_ytdlp: list[str] | None,
    desde_s: float = 0.0, hasta_s: float | None = None,
) -> list[dict]:
    """Fragmentos de relato con tiempo, o lista vacía si no hay subtítulos."""
    if not cmd_ytdlp:
        log.warning("Sin yt-dlp no puedo bajar los subtítulos del relato.")
        return []
    archivo = descargar(url, destino, cmd_ytdlp)
    if archivo is None:
        log.warning("El video no tiene subtítulos: sigo sin relato.")
        return []
    texto = archivo.read_text(encoding="utf-8", errors="replace")
    return agrupar(leer_vtt(texto), desde_s, hasta_s)
