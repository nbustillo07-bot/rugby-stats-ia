"""Cuánto va a costar analizar un tramo con Claude, antes de gastar nada.

Es una cuenta, no una medición: supone cuánto del video es juego real, cada
cuántos segundos hay un contacto y cuánto contesta el modelo. Los números
reales salen del primer análisis (quedan registrados en la base y se ven en la
interfaz), y con eso se puede corregir ``SUPUESTOS``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .config import Config
from .cuadros import alto_para, tokens_imagen

#: Supuestos de la cuenta. Ajustables cuando haya mediciones reales.
SUPUESTOS = {
    # Fracción del video que es juego real (en la prueba dio menos de 1/3).
    "fraccion_juego": 0.35,
    # Cada cuántos segundos de juego hay un contacto que re-examinar.
    "segundos_por_contacto": 25.0,
    # Re-exámenes de más por contactos que no se resuelven a la primera.
    "repeticion_contactos": 1.2,
    # Modo solo tackles: tackles (e intentos) de un equipo en 80 minutos, más
    # los falsos positivos que la pasada rápida lista "por las dúdas".
    "tackles_por_80_min": 100,
    "falsos_positivos": 0.15,
    # Qué parte de los contactos termina pidiendo segunda opinión al experto.
    "fraccion_segunda_opinion": 0.25,
    # Ventanas de fichas que se miran antes de tener a todos (se corta solo).
    "ventanas_fichas": 8,
    # Texto fijo de cada pedido: instrucciones, plantel, esquema.
    "tokens_texto_por_pedido": 3000,
    # En eventos y contactos casi todo ese texto va en caché (se cobra al
    # 10%): solo cambia esto por pedido.
    "tokens_texto_variable": 400,
    # Tokens de las fichas visuales que van en cada pedido de eventos y
    # contactos (unos 23 recortes chicos).
    "tokens_fichas_visuales": 2500,
    # Lo que contesta el modelo, por tipo de pedido (incluye lo que piensa).
    "salida": {
        "gruesa": 700, "eventos": 1500, "contactos": 600, "fichas": 900,
        "relato": 2500, "cambios": 300, "localizar": 900, "experto": 700,
    },
}


@dataclass
class CostoPaso:
    paso: str
    modelo: str
    pedidos: int = 0
    cuadros: int = 0
    tokens_entrada: int = 0
    tokens_cache: int = 0
    tokens_salida: int = 0
    usd: float = 0.0


@dataclass
class Estimacion:
    minutos: float
    pasos: list[CostoPaso] = field(default_factory=list)

    @property
    def usd(self) -> float:
        return sum(p.usd for p in self.pasos)

    @property
    def por_minuto(self) -> float:
        return self.usd / self.minutos if self.minutos else 0.0

    @property
    def cuadros(self) -> int:
        return sum(p.cuadros for p in self.pasos)


def _precio(cfg: Config, modelo: str) -> dict[str, float]:
    precios = cfg.precios_claude
    if modelo in precios:
        return precios[modelo]
    if precios:
        return max(precios.values(), key=lambda p: p.get("entrada", 0))
    return {"entrada": 5.0, "salida": 25.0, "cache_lectura": 0.5}


def _tokens_cuadro(cfg: Config, nombre: str) -> tuple[int, float, float]:
    v = cfg.velocidad(nombre)
    ancho = v.ancho or 640
    return tokens_imagen(ancho, alto_para(ancho)), v.fps, v.ventana_s


def estimar(segundos: float, cfg: Config, pasos: list[str] | None = None) -> Estimacion:
    """Costo estimado de analizar ``segundos`` de video con Claude."""
    s = SUPUESTOS
    est = Estimacion(minutos=segundos / 60)
    if segundos <= 0:
        return est
    maximo = cfg.max_cuadros_por_pedido
    juego = segundos * s["fraccion_juego"]
    muerto = segundos - juego
    texto = s["tokens_texto_por_pedido"]
    fichas_vis = s["tokens_fichas_visuales"]
    pedidos_paso = pasos or ["gruesa", "relato", "fichas", "eventos", "contactos", "cambios"]

    def agregar(paso: str, pedidos: int, cuadros: int, tok_cuadro: int,
                cache: int = 0, nivel: str | None = None) -> None:
        nivel = nivel or cfg.nivel_de(paso)
        modelo = cfg.niveles_claude.get(nivel, cfg.niveles_claude.get("fuerte", ["claude-sonnet-5-5"]))[0]
        p = _precio(cfg, modelo)
        texto_cache = 0
        txt = texto
        if paso in ("eventos", "contactos", "experto"):
            txt = s["tokens_texto_variable"]
            texto_cache = texto - txt
        entrada = pedidos * txt + cuadros * tok_cuadro
        cache_total = pedidos * (cache + texto_cache)
        salida = pedidos * s["salida"].get(paso, 800)
        usd = (
            entrada * p.get("entrada", 0)
            + cache_total * p.get("cache_lectura", p.get("entrada", 0) * 0.1)
            + salida * p.get("salida", 0)
        ) / 1_000_000
        est.pasos.append(CostoPaso(paso, modelo, pedidos, cuadros, entrada, cache_total, salida, usd))

    if "gruesa" in pedidos_paso:
        tok, fps, ventana = _tokens_cuadro(cfg, "gruesa")
        n = math.ceil(segundos / ventana)
        cuadros = min(int(segundos * fps), n * maximo)
        agregar("gruesa", n, cuadros, tok)

    if "relato" in pedidos_paso:
        # Texto solo: unas 150 palabras por minuto de relato.
        n = max(1, math.ceil(segundos / 600))
        agregar("relato", n, 0, 0)
        est.pasos[-1].tokens_entrada += int(segundos / 60 * 250)

    if "fichas" in pedidos_paso:
        tok, fps, ventana = _tokens_cuadro(cfg, "fichas")
        tope = s["ventanas_fichas"]
        if cfg.fotos_por_jugador > 1:
            tope = int(cfg.get("claude.max_ventanas_fichas", 10) or 10)
        n = min(tope, max(1, math.ceil(muerto / ventana)))
        cuadros = min(int(n * ventana * fps), n * maximo)
        agregar("fichas", n, cuadros, tok)

    if "eventos" in pedidos_paso:
        tok, fps, ventana = _tokens_cuadro(cfg, "juego")
        n = max(1, math.ceil(juego / max(1.0, ventana - cfg.solape_s)))
        cuadros = min(int(n * ventana * fps), n * maximo)
        agregar("eventos", n, cuadros, tok, fichas_vis)

    if "contactos" in pedidos_paso:
        v = cfg.velocidad("contacto")
        tok = tokens_imagen(v.ancho or 1008, alto_para(v.ancho or 1008))
        largo = v.margen_antes_s + v.margen_despues_s + v.ventana_s / 2
        zoom = cfg.zoom_contacto
        if cfg.solo_tackles:
            n = max(1, math.ceil(
                segundos / 4800 * s["tackles_por_80_min"] * (1 + s["falsos_positivos"])
            ))
        else:
            n = max(1, math.ceil(juego / s["segundos_por_contacto"] * s["repeticion_contactos"]))
            if not zoom:
                n = max(1, math.ceil(n))
        if zoom:
            # 1) el liviano ubica el contacto
            vl = cfg.velocidad("localizar")
            tok_l = tokens_imagen(vl.ancho or 640, alto_para(vl.ancho or 640))
            agregar("localizar", n, n * max(1, int(largo * vl.fps)), tok_l, nivel="liviano")
            # 2) el fuerte mira el zoom más algo de vista general
            ctx_ancho = int(zoom.get("contexto_ancho") or 512)
            tok_ctx = tokens_imagen(ctx_ancho, alto_para(ctx_ancho))
            ctx = int(largo * float(zoom.get("contexto_fps") or 0))
            por_contacto = min(maximo, max(1, int(largo * v.fps)))
            tok_medio = (por_contacto * tok + ctx * tok_ctx) // max(1, por_contacto + ctx)
            agregar("contactos", n, n * (por_contacto + ctx), tok_medio, fichas_vis)
            # 3) la segunda opinión, solo en los dudosos
            if "experto" in cfg.niveles_claude:
                m = max(1, math.ceil(n * s["fraccion_segunda_opinion"]))
                agregar("experto", m, m * (por_contacto + ctx), tok_medio, fichas_vis,
                        nivel="experto")
        else:
            por_contacto = min(maximo, max(1, int(largo * v.fps)))
            agregar("contactos", n, n * por_contacto, tok, fichas_vis)

    if "cambios" in pedidos_paso:
        n = max(1, math.ceil(segundos / 600))
        tok = tokens_imagen(640, alto_para(640))
        agregar("cambios", n, n * maximo, tok)

    return est


def tramo_y_partido(partido, cfg: Config) -> tuple[Estimacion | None, Estimacion]:
    """Estimación del tramo elegido y del partido entero (80 min si no se sabe)."""
    desde = partido.analizar_desde_s or 0.0
    hasta = partido.analizar_hasta_s or partido.video.duracion_s
    tramo = estimar(hasta - desde, cfg) if hasta else None
    entero_s = partido.video.duracion_s or 80 * 60
    return tramo, estimar(entero_s, cfg)
