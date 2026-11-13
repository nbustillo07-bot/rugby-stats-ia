"""Carga del plantel.

Es el paso 0 y el que fija todo lo demás: los números del plantel son los
únicos válidos en todo el sistema. Si un evento sale con un número que no
está acá, se marca pendiente en lugar de contarse.

Dos formas de cargarlo: a mano en la interfaz, o por CSV. El CSV acepta
nombres de columna en castellano con o sin tildes, y en cualquier orden.
"""

from __future__ import annotations

import csv
import io
import unicodedata
from pathlib import Path

from .modelos import Jugador

#: Columnas aceptadas, por alias. La primera es la canónica.
ALIAS = {
    "numero": ("numero", "n", "nro", "camiseta", "num", "number", "#"),
    "nombre": ("nombre", "jugador", "apellido", "nombre y apellido", "name"),
    "apodo": ("apodo", "alias", "sobrenombre", "nickname"),
    "puesto": ("puesto", "posicion", "pos", "position"),
    "titular": ("titular", "es titular", "arranca", "starter"),
}

CABECERA_EJEMPLO = "numero,nombre,apodo,puesto,titular"

PUESTOS_POR_NUMERO = {
    1: "pilar izquierdo", 2: "hooker", 3: "pilar derecho",
    4: "segunda línea", 5: "segunda línea",
    6: "ala ciego", 7: "ala abierto", 8: "octavo",
    9: "medio scrum", 10: "apertura",
    11: "wing izquierdo", 12: "primer centro", 13: "segundo centro",
    14: "wing derecho", 15: "fullback",
}


class ErrorPlantel(ValueError):
    """No se pudo leer el plantel."""


class AvisoPlantel(Exception):
    """Se pudo cargar el plantel pero hubo filas salteadas."""

    def __init__(self, jugadores: list[Jugador], problemas: list[str]):
        self.jugadores = jugadores
        self.problemas = problemas
        super().__init__(
            f"Cargué {len(jugadores)} jugadores y salté {len(problemas)} filas."
        )


def _normalizar(texto: str) -> str:
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", texto or "")
        if unicodedata.category(c) != "Mn"
    )
    return sin_tildes.strip().lower().replace("_", " ").replace(".", "")


def _mapear_columnas(cabecera: list[str]) -> dict[str, int]:
    mapa: dict[str, int] = {}
    normalizadas = [_normalizar(c) for c in cabecera]
    for canonica, alias in ALIAS.items():
        for i, col in enumerate(normalizadas):
            if col in alias:
                mapa[canonica] = i
                break
    return mapa


def _es_titular(valor: str, numero: int) -> bool:
    v = _normalizar(valor)
    if v in ("si", "s", "1", "true", "titular", "x", "sí"):
        return True
    if v in ("no", "n", "0", "false", "suplente"):
        return False
    # Sin dato: por número. Del 1 al 15 titular, del 16 en adelante suplente.
    return numero <= 15


def desde_csv(contenido: str | bytes, equipo: str) -> list[Jugador]:
    """Lee un plantel de un CSV.

    Mínimo: una columna de número y una de nombre. Lo demás es opcional.
    """
    if isinstance(contenido, bytes):
        for codificacion in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                contenido = contenido.decode(codificacion)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ErrorPlantel("No pude leer el archivo: fijate la codificación.")

    texto = contenido.strip()
    if not texto:
        raise ErrorPlantel("El archivo está vacío.")

    # Detectar el separador: coma, punto y coma o tabulación.
    try:
        dialecto = csv.Sniffer().sniff(texto.split("\n")[0], delimiters=",;\t")
        separador = dialecto.delimiter
    except csv.Error:
        separador = ","

    filas = list(csv.reader(io.StringIO(texto), delimiter=separador))
    if not filas:
        raise ErrorPlantel("El archivo está vacío.")

    mapa = _mapear_columnas(filas[0])
    if "numero" not in mapa or "nombre" not in mapa:
        raise ErrorPlantel(
            "Necesito una columna de número y una de nombre. "
            f"La cabecera que me pasaste es: {', '.join(filas[0])}. "
            f"Un ejemplo que funciona: {CABECERA_EJEMPLO}"
        )

    jugadores: list[Jugador] = []
    vistos: set[int] = set()
    problemas: list[str] = []

    for n_fila, fila in enumerate(filas[1:], start=2):
        if not any(c.strip() for c in fila):
            continue

        def campo(clave: str) -> str:
            i = mapa.get(clave)
            return fila[i].strip() if i is not None and i < len(fila) else ""

        crudo = campo("numero")
        try:
            numero = int(float(crudo))
        except ValueError:
            problemas.append(f"fila {n_fila}: {crudo!r} no es un número de camiseta")
            continue
        if not 1 <= numero <= 99:
            problemas.append(f"fila {n_fila}: el número {numero} está fuera de 1–99")
            continue
        if numero in vistos:
            problemas.append(f"fila {n_fila}: el número {numero} está repetido")
            continue

        nombre = campo("nombre")
        if not nombre:
            problemas.append(f"fila {n_fila}: falta el nombre del {numero}")
            continue

        vistos.add(numero)
        jugadores.append(
            Jugador(
                numero=numero,
                nombre=nombre,
                equipo=equipo,
                apodo=campo("apodo"),
                puesto=campo("puesto") or PUESTOS_POR_NUMERO.get(numero, ""),
                titular=_es_titular(campo("titular"), numero),
            )
        )

    if not jugadores:
        detalle = " ".join(problemas[:3])
        raise ErrorPlantel(f"No pude leer ningún jugador. {detalle}".strip())

    if problemas:
        # No se frena: se carga lo que se pudo y se avisa qué quedó afuera.
        raise AvisoPlantel(jugadores, problemas)

    return jugadores


def cargar_csv(ruta: str | Path, equipo: str) -> tuple[list[Jugador], list[str]]:
    """Como ``desde_csv`` pero devuelve (jugadores, problemas) sin excepción."""
    contenido = Path(ruta).read_bytes()
    try:
        return desde_csv(contenido, equipo), []
    except AvisoPlantel as aviso:
        return aviso.jugadores, aviso.problemas


def a_csv(jugadores: list[Jugador]) -> str:
    salida = io.StringIO()
    w = csv.writer(salida)
    w.writerow(["numero", "nombre", "apodo", "puesto", "titular"])
    for j in sorted(jugadores, key=lambda x: x.numero):
        w.writerow([j.numero, j.nombre, j.apodo, j.puesto, "si" if j.titular else "no"])
    return salida.getvalue()


def plantel_vacio(equipo: str, titulares: int = 15, suplentes: int = 8) -> list[Jugador]:
    """Un plantel en blanco para llenar en la interfaz."""
    jugadores = [
        Jugador(
            numero=n, nombre="", equipo=equipo, titular=True,
            puesto=PUESTOS_POR_NUMERO.get(n, ""),
        )
        for n in range(1, titulares + 1)
    ]
    jugadores += [
        Jugador(numero=n, nombre="", equipo=equipo, titular=False)
        for n in range(titulares + 1, titulares + suplentes + 1)
    ]
    return jugadores


def validar(jugadores: list[Jugador]) -> list[str]:
    """Avisos antes de arrancar el análisis. No frenan, avisan."""
    avisos: list[str] = []
    cargados = [j for j in jugadores if j.nombre.strip()]
    if not cargados:
        avisos.append("No hay ningún jugador cargado.")
        return avisos

    titulares = [j for j in cargados if j.titular]
    if len(titulares) < 15:
        avisos.append(
            f"Hay {len(titulares)} titulares cargados de 15. El sistema usa el "
            "descarte (son 15 en cancha) para identificar: con el plantel "
            "incompleto identifica bastante peor."
        )
    numeros = [j.numero for j in cargados]
    repetidos = {n for n in numeros if numeros.count(n) > 1}
    if repetidos:
        avisos.append(f"Números repetidos: {sorted(repetidos)}.")
    sin_apodo = [j.numero for j in cargados if not j.apodo.strip()]
    if len(sin_apodo) > len(cargados) * 0.7:
        avisos.append(
            "Casi nadie tiene apodo cargado. El relato usa más los apodos que "
            "los apellidos, así que cargarlos mejora el cruce con el relato."
        )
    return avisos
