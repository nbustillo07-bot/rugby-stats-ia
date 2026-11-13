"""Carga de config.yaml y de la clave de la API."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

RAIZ = Path(__file__).resolve().parent.parent
CONFIG_POR_DEFECTO = RAIZ / "config.yaml"


def _cargar_env(ruta: Path) -> None:
    """Lee un .env sencillo sin depender de python-dotenv."""
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        clave, valor = clave.strip(), valor.strip().strip("'\"")
        os.environ.setdefault(clave, valor)


@dataclass
class Velocidad:
    """Una de las tres velocidades de análisis."""

    fps: float
    resolucion: str = "MEDIA_RESOLUTION_LOW"
    ventana_s: float = 120.0
    margen_antes_s: float = 0.0
    margen_despues_s: float = 0.0


class Config:
    """Acceso a la configuración con rutas resueltas contra la raíz del repo."""

    def __init__(self, datos: dict[str, Any], raiz: Path = RAIZ):
        self._d = datos
        self.raiz = raiz

    # -- lectura genérica -------------------------------------------------

    def get(self, ruta: str, por_defecto: Any = None) -> Any:
        """``cfg.get("reglas.tackle_positivo")``."""
        actual: Any = self._d
        for parte in ruta.split("."):
            if not isinstance(actual, dict) or parte not in actual:
                return por_defecto
            actual = actual[parte]
        return actual

    def ruta(self, ruta_config: str, por_defecto: str) -> Path:
        valor = self.get(ruta_config, por_defecto) or por_defecto
        p = Path(valor)
        return p if p.is_absolute() else self.raiz / p

    # -- atajos con nombre ------------------------------------------------

    @property
    def modelo(self) -> str:
        return self.get("modelo.nombre", "gemini-3.8-flash")

    @property
    def modelos_alternativos(self) -> list[str]:
        return list(self.get("modelo.alternativas", []) or [])

    @property
    def temperatura(self) -> float:
        return float(self.get("modelo.temperatura", 0.1))

    def velocidad(self, nombre: str) -> Velocidad:
        d = self.get(f"velocidades.{nombre}") or {}
        if not d:
            raise KeyError(
                f"No hay una velocidad '{nombre}' en config.yaml. "
                f"Definidas: {sorted((self.get('velocidades') or {}).keys())}"
            )
        return Velocidad(
            fps=float(d["fps"]),
            resolucion=d.get("resolucion", "MEDIA_RESOLUTION_LOW"),
            ventana_s=float(d.get("ventana_s", 120)),
            margen_antes_s=float(d.get("margen_antes_s", 0)),
            margen_despues_s=float(d.get("margen_despues_s", 0)),
        )

    @property
    def solape_s(self) -> float:
        return float(self.get("ventanas.solape_s", 5))

    @property
    def tackle_positivo_es_portador_detenido(self) -> bool:
        """True si un tackle es positivo cuando el portador NO avanza.

        Es la convención del rugby y el valor por defecto. Se cambia en
        config.yaml con ``reglas.tackle_positivo: portador_avanza``.
        """
        return self.get("reglas.tackle_positivo", "portador_detenido") == "portador_detenido"

    @property
    def umbral_confianza_revision(self) -> float:
        return float(self.get("reglas.umbral_confianza_revision", 0.85))

    @property
    def umbral_precision_objetivo(self) -> float:
        return float(self.get("reglas.umbral_precision_objetivo", 0.95))

    @property
    def reintentos_sin_clasificar(self) -> int:
        return int(self.get("reglas.reintentos_sin_clasificar", 2))

    @property
    def puntos(self) -> dict[str, int]:
        d = self.get("puntos") or {}
        return {
            "try": int(d.get("try", 5)),
            "conversion": int(d.get("conversion", 2)),
            "penal": int(d.get("penal", 3)),
            "drop": int(d.get("drop", 3)),
            "try_penal": int(d.get("try_penal", 7)),
        }

    @property
    def rpm_inicial(self) -> float:
        return float(self.get("api.rpm_inicial", 8))

    @property
    def esperas_429(self) -> list[float]:
        return [float(x) for x in (self.get("api.espera_429_s") or [5, 15, 45, 120])]

    @property
    def max_reintentos(self) -> int:
        return int(self.get("api.max_reintentos", 4))

    @property
    def usar_cache(self) -> bool:
        return bool(self.get("api.cache", True))

    @property
    def dir_cache(self) -> Path:
        return self.ruta("api.cache_dir", "data/cache")

    @property
    def dir_video(self) -> Path:
        return self.ruta("video.dir_trabajo", "data/video")

    @property
    def dir_salidas(self) -> Path:
        return self.ruta("salida.dir", "salidas")

    @property
    def descargar_si_dura_mas_de_s(self) -> float:
        return float(self.get("video.descargar_si_dura_mas_de_s", 1200))

    @property
    def clip_revision_s(self) -> float:
        return float(self.get("video.clip_revision_s", 6))

    @property
    def jugadores_de(self) -> str:
        return str(self.get("salida.jugadores_de", "propio"))

    @property
    def api_key(self) -> str | None:
        return os.environ.get("GEMINI_API_KEY") or None

    @property
    def hay_api_key(self) -> bool:
        return bool(self.api_key)


def cargar_config(ruta: str | Path | None = None) -> Config:
    """Carga config.yaml y el .env de al lado."""
    p = Path(ruta) if ruta else CONFIG_POR_DEFECTO
    raiz = p.resolve().parent
    _cargar_env(raiz / ".env")
    if not p.exists():
        raise FileNotFoundError(
            f"No encontré {p}. Copiá config.yaml del repo o pasá otra ruta."
        )
    datos = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return Config(datos, raiz=raiz)
