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
    # Ancho en píxeles de cada cuadro, para los proveedores que reciben
    # imágenes sueltas (Claude). 0 = lo decide el proveedor.
    ancho: int = 0


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

    # -- modelos -----------------------------------------------------------
    #
    # Forma nueva (config.yaml): "modelos.fuerte" y "modelos.liviano".
    # Forma vieja: "modelo.nombre" y "modelo.alternativas". Se aceptan las dos.

    @property
    def niveles(self) -> dict[str, list[str]] | None:
        """nivel -> modelos en orden, o None si la config es de la forma vieja.

        Son los modelos del proveedor elegido.
        """
        if self.proveedor == "claude":
            return self.niveles_claude
        m = self.get("modelos")
        if not isinstance(m, dict):
            return None
        niveles = {
            nivel: [str(x) for x in (m.get(nivel) or [])]
            for nivel in ("fuerte", "liviano")
        }
        niveles = {k: v for k, v in niveles.items() if v}
        if not niveles:
            return None
        # Si falta un nivel, se usa el otro: mejor eso que quedarse sin modelo.
        niveles.setdefault("fuerte", niveles.get("liviano", []))
        niveles.setdefault("liviano", niveles.get("fuerte", []))
        return niveles

    @property
    def modelo(self) -> str:
        n = self.niveles
        if n:
            return n["fuerte"][0]
        return self.get("modelo.nombre", "gemini-3.8-flash")

    @property
    def modelos_alternativos(self) -> list[str]:
        n = self.niveles
        if n:
            return n["fuerte"][1:]
        return list(self.get("modelo.alternativas", []) or [])

    @property
    def temperatura(self) -> float:
        return float(
            self.get("modelos.temperatura", self.get("modelo.temperatura", 0.1))
        )

    @property
    def rpm_por_nivel(self) -> dict[str, float]:
        d = self.get("modelos.pedidos_por_minuto") or {}
        return {str(k): float(v) for k, v in d.items()} if isinstance(d, dict) else {}

    @property
    def cupo_diario(self) -> dict[str, int]:
        """Pedidos por día de cada modelo, según lo que muestra AI Studio."""
        d = self.get("modelos.cupo_diario") or {}
        return {str(k): int(v) for k, v in d.items()} if isinstance(d, dict) else {}

    @property
    def respaldo_de_niveles(self) -> dict[str, str]:
        """Qué hacer cuando se agota el cupo diario de los modelos fuertes."""
        if self.get("modelos.si_se_agota_el_fuerte", "frenar") == "seguir_con_liviano":
            return {"fuerte": "liviano"}
        return {}

    def nivel_de(self, paso: str) -> str:
        """Qué nivel de modelo usa cada paso del análisis."""
        por_defecto = {
            "gruesa": "liviano",
            "relato": "liviano",
            "cambios": "liviano",
            "eventos": "liviano",
            "fichas": "fuerte",
            "contactos": "fuerte",
        }
        nivel = self.get(f"pasos.{paso}", por_defecto.get(paso, "fuerte"))
        return nivel if nivel in ("fuerte", "liviano") else "fuerte"

    @property
    def solo_tackles(self) -> bool:
        """Si es True, solo se miden los tackles del equipo propio."""
        return str(self.get("analisis.medir", "todo")).strip().lower() == "tackles"

    @property
    def zoom_contacto(self) -> dict[str, Any]:
        """Opciones del zoom al contacto; {} si está apagado."""
        z = self.get("claude.zoom") or {}
        if not isinstance(z, dict) or not z.get("activo", False):
            return {}
        return {k: v for k, v in z.items() if k != "activo"}

    @property
    def fotos_por_jugador(self) -> int:
        return max(1, int(self.get("claude.fotos_por_jugador", 1) or 1))

    @property
    def solo_equipo_propio(self) -> bool:
        """Si es True, del rival no se registra ni se identifica nada."""
        return bool(self.get("analisis.solo_equipo_propio", False))

    @property
    def archivo_cupos(self) -> Path:
        return self.dir_cache.parent / "cupos.json"

    def velocidad(self, nombre: str) -> Velocidad:
        d = dict(self.get(f"velocidades.{nombre}") or {})
        if not d:
            # Una velocidad que solo existe para un proveedor (p. ej. la de
            # ubicar el contacto, que es solo de Claude).
            d = dict(self.get(f"{self.proveedor}.velocidades.{nombre}") or {})
        if not d:
            raise KeyError(
                f"No hay una velocidad '{nombre}' en config.yaml. "
                f"Definidas: {sorted((self.get('velocidades') or {}).keys())}"
            )
        # Cada proveedor puede ajustar las velocidades: Claude recibe cuadros
        # sueltos y conviene mandarle menos y más chicos que el video entero
        # que recibe Gemini.
        propio = self.get(f"{self.proveedor}.velocidades.{nombre}") or {}
        if isinstance(propio, dict):
            d.update(propio)
        return Velocidad(
            fps=float(d["fps"]),
            resolucion=d.get("resolucion", "MEDIA_RESOLUTION_LOW"),
            ventana_s=float(d.get("ventana_s", 120)),
            margen_antes_s=float(d.get("margen_antes_s", 0)),
            margen_despues_s=float(d.get("margen_despues_s", 0)),
            ancho=int(d.get("ancho", 0) or 0),
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
        """La clave del proveedor elegido."""
        if self.proveedor == "claude":
            return self.clave_anthropic
        return self.clave_gemini

    @property
    def clave_gemini(self) -> str | None:
        return os.environ.get("GEMINI_API_KEY") or None

    @property
    def clave_anthropic(self) -> str | None:
        return os.environ.get("ANTHROPIC_API_KEY") or None

    @property
    def hay_api_key(self) -> bool:
        return bool(self.api_key)

    # -- proveedor ---------------------------------------------------------

    @property
    def proveedor(self) -> str:
        """Quién mira el video: "claude" o "gemini"."""
        p = str(self.get("proveedor", "gemini") or "gemini").strip().lower()
        return p if p in ("claude", "gemini") else "gemini"

    @property
    def nombre_variable_clave(self) -> str:
        return "ANTHROPIC_API_KEY" if self.proveedor == "claude" else "GEMINI_API_KEY"

    @property
    def niveles_claude(self) -> dict[str, list[str]]:
        m = self.get("claude.modelos") or {}
        niveles = {
            n: [str(x) for x in (m.get(n) or [])] for n in ("fuerte", "liviano", "experto")
        } if isinstance(m, dict) else {}
        niveles = {k: v for k, v in niveles.items() if v}
        niveles.setdefault("fuerte", ["claude-sonnet-5-5"])
        niveles.setdefault("liviano", niveles["fuerte"])
        return niveles

    @property
    def precios_claude(self) -> dict[str, dict[str, float]]:
        """Dólares por millón de tokens, por modelo."""
        d = self.get("claude.precios") or {}
        salida: dict[str, dict[str, float]] = {}
        for modelo, p in (d.items() if isinstance(d, dict) else []):
            if isinstance(p, dict):
                salida[str(modelo)] = {k: float(v) for k, v in p.items()}
        return salida

    @property
    def esfuerzo_claude(self) -> str:
        e = str(self.get("claude.esfuerzo", "low") or "low")
        return e if e in ("low", "medium", "high", "xhigh", "max") else "low"

    @property
    def tope_usd_por_partido(self) -> float:
        return float(self.get("claude.tope_usd_por_partido", 10) or 0)

    @property
    def max_cuadros_por_pedido(self) -> int:
        return int(self.get("claude.max_cuadros_por_pedido", 80) or 80)

    @property
    def calidad_descarga(self) -> int:
        return int(self.get("video.calidad_descarga", 720) or 720)


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
