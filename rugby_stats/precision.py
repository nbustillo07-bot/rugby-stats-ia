"""La pantalla de precisión: ¿esto sirve o no?

Sin esto no se sabe nada. El cálculo es simple y honesto: de las propuestas
que una persona ya revisó, qué porcentaje confirmó **sin cambiarle nada**. Eso
es la precisión del modelo para ese tipo de evento.

Dos aclaraciones que importan:

- La precisión se mide solo sobre lo revisado. Si de 200 eventos se revisaron
  12, el número sale de esos 12 y la muestra es chica: el informe lo dice.
- La precisión del **sistema entero** no es esta. Si la persona revisa todo,
  el sistema queda al 100% y lo que mide este número es cuánto trabajo le
  ahorra el modelo. Por eso, cuando un tipo de evento pasa el umbral, se
  puede dejar de revisarlo: ahí la precisión del sistema empieza a ser la del
  modelo.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .db import BaseDatos
from .modelos import Correccion, Evento

#: Con menos revisados que esto, el porcentaje no significa gran cosa.
MUESTRA_MINIMA = 10


@dataclass
class PrecisionTipo:
    accion: str
    revisados: int
    confirmados_sin_cambios: int
    corregidos: int
    descartados: int
    # Corrección más común: qué campo falla más en este tipo de evento.
    campo_que_mas_falla: str = ""
    errores_por_campo: dict[str, int] | None = None

    @property
    def precision(self) -> float:
        return (
            self.confirmados_sin_cambios / self.revisados if self.revisados else 0.0
        )

    @property
    def muestra_suficiente(self) -> bool:
        return self.revisados >= MUESTRA_MINIMA

    def veredicto(self, umbral: float) -> str:
        if not self.muestra_suficiente:
            return f"muestra chica ({self.revisados} revisados): no concluye"
        if self.precision >= umbral:
            return f"pasa el {umbral * 100:.0f}%: se puede dejar de revisar"
        return f"abajo del {umbral * 100:.0f}%: hay que seguir revisando"


@dataclass
class Informe:
    por_tipo: list[PrecisionTipo]
    total_eventos: int
    total_revisados: int
    simulado: bool = False

    @property
    def precision_global(self) -> float:
        rev = sum(p.revisados for p in self.por_tipo)
        ok = sum(p.confirmados_sin_cambios for p in self.por_tipo)
        return ok / rev if rev else 0.0

    @property
    def porcentaje_revisado(self) -> float:
        return (
            100 * self.total_revisados / self.total_eventos if self.total_eventos else 0.0
        )

    def como_dict(self) -> dict[str, dict[str, float]]:
        """Para meterlo en la hoja de notas del Excel."""
        return {
            p.accion: {
                "precision": p.precision,
                "revisados": p.revisados,
                "corregidos": p.corregidos,
            }
            for p in self.por_tipo
        }

    def resumen(self, umbral: float) -> str:
        if self.total_revisados == 0:
            return (
                "Todavía no revisaste ningún evento, así que no hay precisión "
                "medida. Hasta que no corrijas fila por fila, no se sabe si "
                "esto sirve."
            )
        partes = [
            f"{self.total_revisados} de {self.total_eventos} eventos revisados "
            f"({self.porcentaje_revisado:.0f}%)",
            f"precisión global {self.precision_global * 100:.0f}%",
        ]
        listos = [p.accion for p in self.por_tipo
                  if p.muestra_suficiente and p.precision >= umbral]
        if listos:
            partes.append("pasan el umbral: " + ", ".join(listos))
        return " · ".join(partes)


def calcular(db: BaseDatos, partido_id: int, cfg: Config) -> Informe:
    eventos = db.eventos(partido_id)
    correcciones = db.correcciones(partido_id)
    return calcular_desde(eventos, correcciones, cfg)


def calcular_desde(
    eventos: list[Evento], correcciones: list[Correccion], cfg: Config
) -> Informe:
    por_evento: dict[int, list[Correccion]] = {}
    for c in correcciones:
        por_evento.setdefault(c.evento_id, []).append(c)

    revisados = [e for e in eventos if e.revisado]
    acciones = sorted({e.accion for e in revisados})
    por_tipo: list[PrecisionTipo] = []

    for accion in acciones:
        de_este_tipo = [e for e in revisados if e.accion == accion]
        confirmados = 0
        corregidos = 0
        descartados = 0
        errores: dict[str, int] = {}

        for e in de_este_tipo:
            cs = por_evento.get(e.id or -1, [])
            if not cs:
                confirmados += 1
                continue
            if any(c.campo == "descartado" for c in cs):
                descartados += 1
            else:
                corregidos += 1
            for c in cs:
                errores[c.campo] = errores.get(c.campo, 0) + 1

        peor = max(errores, key=lambda k: errores[k]) if errores else ""
        por_tipo.append(
            PrecisionTipo(
                accion=accion,
                revisados=len(de_este_tipo),
                confirmados_sin_cambios=confirmados,
                corregidos=corregidos,
                descartados=descartados,
                campo_que_mas_falla=peor,
                errores_por_campo=errores,
            )
        )

    por_tipo.sort(key=lambda p: (-p.revisados, p.accion))
    return Informe(
        por_tipo=por_tipo,
        total_eventos=len(eventos),
        total_revisados=len(revisados),
    )


def donde_falla(informe: Informe) -> list[str]:
    """Qué conviene mejorar primero, en palabras.

    No sirve saber solo el porcentaje: sirve saber que lo que falla es *quién*
    hizo el tackle y no *que hubo un tackle*, porque eso se arregla subiendo
    los cuadros por segundo del re-examen, no cambiando el prompt.
    """
    sugerencias: list[str] = []
    traduccion = {
        "jugador_numero": "la identificación del jugador",
        "accion": "qué acción fue",
        "resultado": "el resultado de la acción",
        "equipo": "de qué equipo era",
        "metros": "los metros estimados",
        "descartado": "eventos que no existieron",
    }
    for p in informe.por_tipo:
        if not p.errores_por_campo or not p.muestra_suficiente:
            continue
        if p.precision >= 0.95:
            continue
        campo = traduccion.get(p.campo_que_mas_falla, p.campo_que_mas_falla)
        detalle = f"En {p.accion} ({p.precision * 100:.0f}%), lo que más falla es {campo}."
        if p.campo_que_mas_falla == "jugador_numero":
            detalle += (
                " Subí los cuadros por segundo del re-examen de contactos en "
                "config.yaml, o confirmá mejor las fichas antes de analizar."
            )
        elif p.campo_que_mas_falla == "resultado":
            detalle += (
                " Suele ser el criterio de positivo/negativo: revisá la "
                "tolerancia de avance en config.yaml."
            )
        elif p.campo_que_mas_falla == "descartado":
            detalle += (
                " El modelo está inventando eventos: subí el umbral de "
                "confianza para que entren menos a la planilla."
            )
        sugerencias.append(detalle)

    if not sugerencias and informe.total_revisados < MUESTRA_MINIMA:
        sugerencias.append(
            f"Revisá al menos {MUESTRA_MINIMA} eventos de cada tipo antes de "
            "sacar conclusiones. Con menos, el porcentaje es ruido."
        )
    return sugerencias
