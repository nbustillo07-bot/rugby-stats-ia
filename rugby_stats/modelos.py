"""Objetos del dominio.

Nada acá nombra un club: un partido tiene dos equipos y uno de ellos está
marcado como el propio (el que se quiere analizar). Sirve para cualquier club.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any, Literal

# --------------------------------------------------------------------------
# Vocabulario cerrado
# --------------------------------------------------------------------------

ACCIONES = (
    "Tackle",
    "Tackle errado",
    "Carry",
    "Pase",
    "Offload",
    "Patada",
    "Ruck limpiado",
    "Pelota perdida",
    "Pelota recuperada",
    "Penal cometido",
    "Try",
    "Conversión",
    "Penal a los palos",
    "Drop",
    "Line tiro",
    "Line toma",
)

#: Resultados válidos por acción. El modelo tiene que elegir de acá: si
#: devuelve otra cosa, el evento queda sin clasificar y vuelve a la cola.
RESULTADOS = {
    "Tackle": ("positivo", "negativo", "neutro"),
    "Tackle errado": ("errado",),
    "Carry": ("avanza", "detenido", "retrocede", "quiebre"),
    "Pase": ("bueno", "malo", "forward"),
    "Offload": ("bueno", "malo"),
    "Patada": ("al touch", "en juego", "al palo", "cargada"),
    "Ruck limpiado": ("limpio", "no limpio"),
    "Pelota perdida": ("knock-on", "pase interceptado", "pelota robada", "forward"),
    "Pelota recuperada": ("robada", "interceptada", "suelta"),
    "Penal cometido": (
        "offside",
        "no soltar",
        "tackle alto",
        "obstrucción",
        "juego sucio",
        "scrum",
        "line",
        "otro",
    ),
    "Try": ("convertido", "no convertido"),
    "Conversión": ("adentro", "afuera"),
    "Penal a los palos": ("adentro", "afuera"),
    "Drop": ("adentro", "afuera"),
    "Line tiro": ("ganado", "perdido"),
    "Line toma": ("ganado", "perdido"),
}

FUENTES = ("video", "relato", "marcador", "arbitro", "manual")

#: Rasgos de partido que se guardan en la ficha de un jugador. Son los que
#: sirven para reconocerlo cuando no se le ve el número.
RASGOS = (
    "casco",
    "vincha",
    "vendas",
    "color_botines",
    "pelo",
    "contextura",
    "medias",
    "munequeras",
    "protector_bucal",
    "otro",
)

Legibilidad = Literal["alta", "media", "baja"]
TipoTramo = Literal["juego", "muerto"]


# --------------------------------------------------------------------------
# Equipos y jugadores
# --------------------------------------------------------------------------


@dataclass
class Equipo:
    """Un equipo del partido.

    ``kit`` es la descripción de la camiseta en palabras, tal como se la ve en
    el video. Entra en los prompts, así que conviene que sea concreta:
    "bordó liso con número blanco", "rayas bordó y blancas verticales".

    ``numero_legible`` dice qué tan fácil es leer el número en la espalda.
    Si es baja, el paso de fichas deja de ser opcional: es la única forma de
    saber quién es quién.
    """

    nombre: str
    kit: str = ""
    numero_legible: Legibilidad = "media"
    es_propio: bool = False
    color_principal: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Equipo":
        return cls(**{k: v for k, v in d.items() if k in cls.__annotations__})


@dataclass
class Jugador:
    """Un jugador del plantel de un partido.

    El plantel lo carga la persona a mano (o por CSV): número de camiseta y
    nombre son obligatorios, el resto ayuda. El apodo importa porque el relato
    los usa más que el apellido.
    """

    numero: int
    nombre: str
    equipo: str
    apodo: str = ""
    puesto: str = ""
    titular: bool = True
    id: int | None = None
    partido_id: int | None = None
    # Minuto en que entró y salió, si hubo cambio.
    entro_min: float | None = None
    salio_min: float | None = None

    @property
    def etiqueta(self) -> str:
        """Cómo se lo nombra en pantalla y en los prompts."""
        base = f"{self.numero}. {self.nombre}"
        return f"{base} ({self.apodo})" if self.apodo else base

    @property
    def nombres_para_relato(self) -> list[str]:
        """Formas en que el relator puede nombrarlo."""
        partes = [self.nombre]
        if self.apodo:
            partes.append(self.apodo)
        apellido = self.nombre.split()[-1] if self.nombre.split() else ""
        if apellido and apellido != self.nombre:
            partes.append(apellido)
        return sorted(set(p for p in partes if p))


@dataclass
class Ficha:
    """Cómo se ve un jugador en este partido.

    Se arma desde un cuadro donde se le leyó el número en la espalda, y se
    renueva cada vez que se le vuelve a leer: los jugadores se embarran, se
    sacan el casco, se cambian las vendas.
    """

    jugador_numero: int
    equipo: str
    t_segundos: float
    rasgos: dict[str, str] = field(default_factory=dict)
    confianza: float = 0.0
    fuente: str = "video"
    confirmada: bool = False
    nota: str = ""
    id: int | None = None
    partido_id: int | None = None
    # Recorte del jugador en el cuadro donde se le leyó el número. Es la
    # "ficha visual": se le muestra al modelo para comparar imagen contra
    # imagen, que es lo que mejor funcionó para identificar.
    imagen: str = ""

    def descripcion(self) -> str:
        """La ficha en una línea, para meterla en un prompt."""
        vistos = [f"{k.replace('_', ' ')}: {v}" for k, v in self.rasgos.items() if v]
        return "; ".join(vistos) if vistos else "sin rasgos registrados"


# --------------------------------------------------------------------------
# Partido
# --------------------------------------------------------------------------


@dataclass
class FuenteVideo:
    """De dónde sale el video.

    ``tipo`` puede ser:
      - ``youtube``: URL pública de YouTube, se le pasa directo a Gemini.
      - ``archivo``: archivo local, se sube por la Files API.
      - ``url``: cualquier otra URL, se baja primero con yt-dlp.
    """

    tipo: Literal["youtube", "archivo", "url"]
    referencia: str
    duracion_s: float | None = None
    # Si se bajó o se subió, acá queda la ruta local y el nombre en la Files API.
    ruta_local: str | None = None
    uri_subida: str | None = None
    # Cuándo se subió a la Files API (ISO, UTC). Los archivos subidos se
    # borran solos a las 48 horas, así que hay que saber cuándo renovarlos.
    subido_en: str | None = None
    # Si se bajó solo un pedazo del video: qué segundos del partido cubre el
    # archivo. None = el archivo es el video entero.
    recorte_desde_s: float | None = None
    recorte_hasta_s: float | None = None
    # Desfase entre el reloj del marcador en pantalla y el tiempo del video.
    # reloj_partido = t_video + desfase_s
    desfase_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "FuenteVideo":
        return cls(**{k: v for k, v in d.items() if k in cls.__annotations__})


@dataclass
class Partido:
    nombre: str
    equipo_propio: Equipo
    equipo_rival: Equipo
    video: FuenteVideo
    fecha: str = ""
    competencia: str = ""
    id: int | None = None
    # Tramo del video que se quiere analizar, en segundos de video.
    analizar_desde_s: float = 0.0
    analizar_hasta_s: float | None = None
    notas: str = ""

    def equipos(self) -> tuple[Equipo, Equipo]:
        return self.equipo_propio, self.equipo_rival

    def equipo(self, nombre: str) -> Equipo | None:
        for e in self.equipos():
            if e.nombre == nombre:
                return e
        return None


@dataclass
class Tramo:
    """Un pedazo del video clasificado como juego o tiempo muerto."""

    inicio_s: float
    fin_s: float
    tipo: TipoTramo
    reloj_inicio: str = ""
    marcador_propio: int | None = None
    marcador_rival: int | None = None
    confianza: float = 0.0
    id: int | None = None
    partido_id: int | None = None

    @property
    def duracion_s(self) -> float:
        return max(0.0, self.fin_s - self.inicio_s)


@dataclass
class Evento:
    """Una acción de un jugador en un momento del partido."""

    t_segundos: float
    accion: str
    equipo: str = ""
    jugador_numero: int | None = None
    resultado: str = ""
    metros: float | None = None
    confianza: float = 0.0
    fuente: str = "video"
    reloj: str = ""
    revisado: bool = False
    # Si el evento no pudo clasificarse del todo, queda pendiente.
    pendiente: bool = False
    nota: str = ""
    # Jugadores secundarios: el segundo tackleador, el que recibe el pase.
    jugadores_secundarios: list[int] = field(default_factory=list)
    # Qué propuso el modelo antes de que lo corrija una persona.
    propuesta_original: dict[str, Any] | None = None
    id: int | None = None
    partido_id: int | None = None

    def esta_clasificado(self) -> bool:
        """Regla de Nico: sin jugador o sin resultado, no vale."""
        if self.accion not in ACCIONES:
            return False
        if self.jugador_numero is None:
            return False
        validos = RESULTADOS.get(self.accion, ())
        return bool(self.resultado) and self.resultado in validos

    def motivo_pendiente(self) -> str:
        if self.accion not in ACCIONES:
            return f"acción desconocida: {self.accion!r}"
        if self.jugador_numero is None:
            return "sin jugador identificado"
        validos = RESULTADOS.get(self.accion, ())
        if not self.resultado:
            return "sin resultado"
        if self.resultado not in validos:
            return f"resultado {self.resultado!r} no válido para {self.accion}"
        return ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, default=str)


@dataclass
class Correccion:
    """Lo que cambió una persona sobre la propuesta del modelo.

    Es el insumo de la pantalla de precisión: sin esto no se sabe si el
    sistema sirve.
    """

    evento_id: int
    campo: str
    valor_modelo: str
    valor_persona: str
    id: int | None = None
    partido_id: int | None = None
    momento: str = ""
