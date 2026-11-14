"""Base de datos SQLite.

Un archivo por instalación (``data/partidos.db``) con todos los partidos.
Todo lo que se analiza queda guardado, así que el proceso se puede cortar y
retomar sin perder nada: es lo que hace que un partido de 80 minutos sea
abordable con los límites del free tier.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .modelos import (
    Correccion,
    Equipo,
    Evento,
    Ficha,
    FuenteVideo,
    Jugador,
    Partido,
    Tramo,
)

ESQUEMA = """
CREATE TABLE IF NOT EXISTS partidos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    fecha TEXT DEFAULT '',
    competencia TEXT DEFAULT '',
    equipo_propio TEXT NOT NULL,      -- json de Equipo
    equipo_rival TEXT NOT NULL,       -- json de Equipo
    video TEXT NOT NULL,              -- json de FuenteVideo
    analizar_desde_s REAL DEFAULT 0,
    analizar_hasta_s REAL,
    notas TEXT DEFAULT '',
    creado TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jugadores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    partido_id INTEGER NOT NULL REFERENCES partidos(id) ON DELETE CASCADE,
    equipo TEXT NOT NULL,
    numero INTEGER NOT NULL,
    nombre TEXT NOT NULL,
    apodo TEXT DEFAULT '',
    puesto TEXT DEFAULT '',
    titular INTEGER DEFAULT 1,
    entro_min REAL,
    salio_min REAL,
    UNIQUE (partido_id, equipo, numero)
);

CREATE TABLE IF NOT EXISTS fichas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    partido_id INTEGER NOT NULL REFERENCES partidos(id) ON DELETE CASCADE,
    equipo TEXT NOT NULL,
    jugador_numero INTEGER NOT NULL,
    t_segundos REAL NOT NULL,
    rasgos TEXT NOT NULL DEFAULT '{}',
    confianza REAL DEFAULT 0,
    fuente TEXT DEFAULT 'video',
    confirmada INTEGER DEFAULT 0,
    nota TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS tramos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    partido_id INTEGER NOT NULL REFERENCES partidos(id) ON DELETE CASCADE,
    inicio_s REAL NOT NULL,
    fin_s REAL NOT NULL,
    tipo TEXT NOT NULL,
    reloj_inicio TEXT DEFAULT '',
    marcador_propio INTEGER,
    marcador_rival INTEGER,
    confianza REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    partido_id INTEGER NOT NULL REFERENCES partidos(id) ON DELETE CASCADE,
    t_segundos REAL NOT NULL,
    reloj TEXT DEFAULT '',
    equipo TEXT DEFAULT '',
    jugador_numero INTEGER,
    accion TEXT NOT NULL,
    resultado TEXT DEFAULT '',
    metros REAL,
    confianza REAL DEFAULT 0,
    fuente TEXT DEFAULT 'video',
    revisado INTEGER DEFAULT 0,
    pendiente INTEGER DEFAULT 0,
    nota TEXT DEFAULT '',
    jugadores_secundarios TEXT DEFAULT '[]',
    propuesta_original TEXT
);

CREATE TABLE IF NOT EXISTS correcciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    partido_id INTEGER NOT NULL REFERENCES partidos(id) ON DELETE CASCADE,
    evento_id INTEGER NOT NULL,
    campo TEXT NOT NULL,
    valor_modelo TEXT DEFAULT '',
    valor_persona TEXT DEFAULT '',
    momento TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transcripcion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    partido_id INTEGER NOT NULL REFERENCES partidos(id) ON DELETE CASCADE,
    inicio_s REAL NOT NULL,
    fin_s REAL NOT NULL,
    texto TEXT NOT NULL,
    jugadores_nombrados TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS progreso (
    partido_id INTEGER NOT NULL REFERENCES partidos(id) ON DELETE CASCADE,
    paso TEXT NOT NULL,
    estado TEXT NOT NULL,
    detalle TEXT DEFAULT '',
    actualizado TEXT NOT NULL,
    PRIMARY KEY (partido_id, paso)
);

CREATE INDEX IF NOT EXISTS ix_eventos_partido ON eventos(partido_id, t_segundos);
CREATE INDEX IF NOT EXISTS ix_fichas_partido ON fichas(partido_id, equipo, jugador_numero);
CREATE INDEX IF NOT EXISTS ix_tramos_partido ON tramos(partido_id, inicio_s);
"""


def ruta_por_defecto() -> Path:
    """Dónde vive la base. Se puede mover con la variable RUGBY_STATS_DB."""
    import os

    de_entorno = os.environ.get("RUGBY_STATS_DB")
    if de_entorno:
        return Path(de_entorno)
    return Path(__file__).resolve().parent.parent / "data" / "partidos.db"


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class BaseDatos:
    def __init__(self, ruta: str | Path = "data/partidos.db"):
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(self.ruta))
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys = ON")
        self.con.executescript(ESQUEMA)
        self.con.commit()

    def cerrar(self) -> None:
        self.con.close()

    def __enter__(self) -> "BaseDatos":
        return self

    def __exit__(self, *_: Any) -> None:
        self.cerrar()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.con
            self.con.commit()
        except Exception:
            self.con.rollback()
            raise

    # ------------------------------------------------------------------
    # Partidos
    # ------------------------------------------------------------------

    def guardar_partido(self, p: Partido) -> int:
        with self.tx() as con:
            if p.id is None:
                cur = con.execute(
                    """INSERT INTO partidos
                       (nombre, fecha, competencia, equipo_propio, equipo_rival,
                        video, analizar_desde_s, analizar_hasta_s, notas, creado)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        p.nombre,
                        p.fecha,
                        p.competencia,
                        json.dumps(p.equipo_propio.to_dict(), ensure_ascii=False),
                        json.dumps(p.equipo_rival.to_dict(), ensure_ascii=False),
                        json.dumps(p.video.to_dict(), ensure_ascii=False),
                        p.analizar_desde_s,
                        p.analizar_hasta_s,
                        p.notas,
                        _ahora(),
                    ),
                )
                p.id = int(cur.lastrowid)
            else:
                con.execute(
                    """UPDATE partidos SET nombre=?, fecha=?, competencia=?,
                       equipo_propio=?, equipo_rival=?, video=?,
                       analizar_desde_s=?, analizar_hasta_s=?, notas=?
                       WHERE id=?""",
                    (
                        p.nombre,
                        p.fecha,
                        p.competencia,
                        json.dumps(p.equipo_propio.to_dict(), ensure_ascii=False),
                        json.dumps(p.equipo_rival.to_dict(), ensure_ascii=False),
                        json.dumps(p.video.to_dict(), ensure_ascii=False),
                        p.analizar_desde_s,
                        p.analizar_hasta_s,
                        p.notas,
                        p.id,
                    ),
                )
        return int(p.id)

    def _fila_a_partido(self, f: sqlite3.Row) -> Partido:
        return Partido(
            id=f["id"],
            nombre=f["nombre"],
            fecha=f["fecha"],
            competencia=f["competencia"],
            equipo_propio=Equipo.from_dict(json.loads(f["equipo_propio"])),
            equipo_rival=Equipo.from_dict(json.loads(f["equipo_rival"])),
            video=FuenteVideo.from_dict(json.loads(f["video"])),
            analizar_desde_s=f["analizar_desde_s"] or 0.0,
            analizar_hasta_s=f["analizar_hasta_s"],
            notas=f["notas"] or "",
        )

    def partido(self, partido_id: int) -> Partido | None:
        f = self.con.execute("SELECT * FROM partidos WHERE id=?", (partido_id,)).fetchone()
        return self._fila_a_partido(f) if f else None

    def partidos(self) -> list[Partido]:
        filas = self.con.execute("SELECT * FROM partidos ORDER BY id DESC").fetchall()
        return [self._fila_a_partido(f) for f in filas]

    def borrar_partido(self, partido_id: int) -> None:
        with self.tx() as con:
            con.execute("DELETE FROM partidos WHERE id=?", (partido_id,))

    # ------------------------------------------------------------------
    # Jugadores
    # ------------------------------------------------------------------

    def guardar_plantel(self, partido_id: int, jugadores: list[Jugador]) -> None:
        """Reemplaza el plantel de un equipo. Pisa lo que había para ese equipo."""
        if not jugadores:
            return
        equipos = {j.equipo for j in jugadores}
        with self.tx() as con:
            for eq in equipos:
                con.execute(
                    "DELETE FROM jugadores WHERE partido_id=? AND equipo=?",
                    (partido_id, eq),
                )
            con.executemany(
                """INSERT INTO jugadores
                   (partido_id, equipo, numero, nombre, apodo, puesto, titular,
                    entro_min, salio_min)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        partido_id,
                        j.equipo,
                        j.numero,
                        j.nombre,
                        j.apodo,
                        j.puesto,
                        1 if j.titular else 0,
                        j.entro_min,
                        j.salio_min,
                    )
                    for j in jugadores
                ],
            )

    def plantel(self, partido_id: int, equipo: str | None = None) -> list[Jugador]:
        sql = "SELECT * FROM jugadores WHERE partido_id=?"
        args: list[Any] = [partido_id]
        if equipo:
            sql += " AND equipo=?"
            args.append(equipo)
        sql += " ORDER BY equipo, numero"
        return [
            Jugador(
                id=f["id"],
                partido_id=f["partido_id"],
                equipo=f["equipo"],
                numero=f["numero"],
                nombre=f["nombre"],
                apodo=f["apodo"] or "",
                puesto=f["puesto"] or "",
                titular=bool(f["titular"]),
                entro_min=f["entro_min"],
                salio_min=f["salio_min"],
            )
            for f in self.con.execute(sql, args).fetchall()
        ]

    def actualizar_cambio(
        self, partido_id: int, equipo: str, numero: int, entro_min: float | None,
        salio_min: float | None,
    ) -> None:
        with self.tx() as con:
            con.execute(
                """UPDATE jugadores SET entro_min=COALESCE(?, entro_min),
                   salio_min=COALESCE(?, salio_min)
                   WHERE partido_id=? AND equipo=? AND numero=?""",
                (entro_min, salio_min, partido_id, equipo, numero),
            )

    # ------------------------------------------------------------------
    # Fichas
    # ------------------------------------------------------------------

    def guardar_ficha(self, partido_id: int, ficha: Ficha) -> int:
        with self.tx() as con:
            cur = con.execute(
                """INSERT INTO fichas
                   (partido_id, equipo, jugador_numero, t_segundos, rasgos,
                    confianza, fuente, confirmada, nota)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    partido_id,
                    ficha.equipo,
                    ficha.jugador_numero,
                    ficha.t_segundos,
                    json.dumps(ficha.rasgos, ensure_ascii=False),
                    ficha.confianza,
                    ficha.fuente,
                    1 if ficha.confirmada else 0,
                    ficha.nota,
                ),
            )
            ficha.id = int(cur.lastrowid)
        return int(ficha.id)

    def _fila_a_ficha(self, f: sqlite3.Row) -> Ficha:
        return Ficha(
            id=f["id"],
            partido_id=f["partido_id"],
            equipo=f["equipo"],
            jugador_numero=f["jugador_numero"],
            t_segundos=f["t_segundos"],
            rasgos=json.loads(f["rasgos"] or "{}"),
            confianza=f["confianza"] or 0.0,
            fuente=f["fuente"] or "video",
            confirmada=bool(f["confirmada"]),
            nota=f["nota"] or "",
        )

    def fichas(self, partido_id: int, equipo: str | None = None) -> list[Ficha]:
        sql = "SELECT * FROM fichas WHERE partido_id=?"
        args: list[Any] = [partido_id]
        if equipo:
            sql += " AND equipo=?"
            args.append(equipo)
        sql += " ORDER BY jugador_numero, t_segundos"
        return [self._fila_a_ficha(f) for f in self.con.execute(sql, args).fetchall()]

    def fichas_vigentes(
        self, partido_id: int, equipo: str, t_segundos: float
    ) -> dict[int, Ficha]:
        """La ficha más reciente de cada jugador antes de ``t_segundos``.

        Las fichas se renuevan: los jugadores se embarran y se sacan el casco,
        así que vale la última lectura del número, no la primera.
        """
        vigentes: dict[int, Ficha] = {}
        for ficha in self.fichas(partido_id, equipo):
            if ficha.t_segundos <= t_segundos:
                vigentes[ficha.jugador_numero] = ficha
        return vigentes

    def confirmar_ficha(self, ficha_id: int, rasgos: dict[str, str] | None = None) -> None:
        with self.tx() as con:
            if rasgos is not None:
                con.execute(
                    "UPDATE fichas SET confirmada=1, rasgos=?, confianza=1.0 WHERE id=?",
                    (json.dumps(rasgos, ensure_ascii=False), ficha_id),
                )
            else:
                con.execute(
                    "UPDATE fichas SET confirmada=1, confianza=1.0 WHERE id=?", (ficha_id,)
                )

    def borrar_ficha(self, ficha_id: int) -> None:
        with self.tx() as con:
            con.execute("DELETE FROM fichas WHERE id=?", (ficha_id,))

    # ------------------------------------------------------------------
    # Tramos
    # ------------------------------------------------------------------

    def guardar_tramos(self, partido_id: int, tramos: list[Tramo]) -> None:
        if not tramos:
            return
        with self.tx() as con:
            con.executemany(
                """INSERT INTO tramos
                   (partido_id, inicio_s, fin_s, tipo, reloj_inicio,
                    marcador_propio, marcador_rival, confianza)
                   VALUES (?,?,?,?,?,?,?,?)""",
                [
                    (
                        partido_id,
                        t.inicio_s,
                        t.fin_s,
                        t.tipo,
                        t.reloj_inicio,
                        t.marcador_propio,
                        t.marcador_rival,
                        t.confianza,
                    )
                    for t in tramos
                ],
            )

    def tramos(self, partido_id: int, tipo: str | None = None) -> list[Tramo]:
        sql = "SELECT * FROM tramos WHERE partido_id=?"
        args: list[Any] = [partido_id]
        if tipo:
            sql += " AND tipo=?"
            args.append(tipo)
        sql += " ORDER BY inicio_s"
        return [
            Tramo(
                id=f["id"],
                partido_id=f["partido_id"],
                inicio_s=f["inicio_s"],
                fin_s=f["fin_s"],
                tipo=f["tipo"],
                reloj_inicio=f["reloj_inicio"] or "",
                marcador_propio=f["marcador_propio"],
                marcador_rival=f["marcador_rival"],
                confianza=f["confianza"] or 0.0,
            )
            for f in self.con.execute(sql, args).fetchall()
        ]

    def borrar_tramos(self, partido_id: int) -> None:
        with self.tx() as con:
            con.execute("DELETE FROM tramos WHERE partido_id=?", (partido_id,))

    # ------------------------------------------------------------------
    # Eventos
    # ------------------------------------------------------------------

    def guardar_evento(self, partido_id: int, e: Evento) -> int:
        datos = (
            partido_id,
            e.t_segundos,
            e.reloj,
            e.equipo,
            e.jugador_numero,
            e.accion,
            e.resultado,
            e.metros,
            e.confianza,
            e.fuente,
            1 if e.revisado else 0,
            1 if e.pendiente else 0,
            e.nota,
            json.dumps(e.jugadores_secundarios),
            json.dumps(e.propuesta_original, ensure_ascii=False)
            if e.propuesta_original
            else None,
        )
        with self.tx() as con:
            if e.id is None:
                cur = con.execute(
                    """INSERT INTO eventos
                       (partido_id, t_segundos, reloj, equipo, jugador_numero,
                        accion, resultado, metros, confianza, fuente, revisado,
                        pendiente, nota, jugadores_secundarios, propuesta_original)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    datos,
                )
                e.id = int(cur.lastrowid)
            else:
                con.execute(
                    """UPDATE eventos SET partido_id=?, t_segundos=?, reloj=?,
                       equipo=?, jugador_numero=?, accion=?, resultado=?, metros=?,
                       confianza=?, fuente=?, revisado=?, pendiente=?, nota=?,
                       jugadores_secundarios=?, propuesta_original=?
                       WHERE id=?""",
                    datos + (e.id,),
                )
        return int(e.id)

    def guardar_eventos(self, partido_id: int, eventos: list[Evento]) -> None:
        for e in eventos:
            self.guardar_evento(partido_id, e)

    def _fila_a_evento(self, f: sqlite3.Row) -> Evento:
        return Evento(
            id=f["id"],
            partido_id=f["partido_id"],
            t_segundos=f["t_segundos"],
            reloj=f["reloj"] or "",
            equipo=f["equipo"] or "",
            jugador_numero=f["jugador_numero"],
            accion=f["accion"],
            resultado=f["resultado"] or "",
            metros=f["metros"],
            confianza=f["confianza"] or 0.0,
            fuente=f["fuente"] or "video",
            revisado=bool(f["revisado"]),
            pendiente=bool(f["pendiente"]),
            nota=f["nota"] or "",
            jugadores_secundarios=json.loads(f["jugadores_secundarios"] or "[]"),
            propuesta_original=json.loads(f["propuesta_original"])
            if f["propuesta_original"]
            else None,
        )

    def eventos(
        self,
        partido_id: int,
        equipo: str | None = None,
        solo_pendientes: bool = False,
        solo_sin_revisar: bool = False,
    ) -> list[Evento]:
        sql = "SELECT * FROM eventos WHERE partido_id=?"
        args: list[Any] = [partido_id]
        if equipo:
            sql += " AND equipo=?"
            args.append(equipo)
        if solo_pendientes:
            sql += " AND pendiente=1"
        if solo_sin_revisar:
            sql += " AND revisado=0"
        sql += " ORDER BY t_segundos, id"
        return [self._fila_a_evento(f) for f in self.con.execute(sql, args).fetchall()]

    def evento(self, evento_id: int) -> Evento | None:
        f = self.con.execute("SELECT * FROM eventos WHERE id=?", (evento_id,)).fetchone()
        return self._fila_a_evento(f) if f else None

    def cola_revision(self, partido_id: int) -> list[Evento]:
        """Eventos a revisar, los de confianza baja primero.

        Primero los pendientes (sin clasificar), después por confianza
        creciente. Es el orden en que conviene gastar el tiempo de una persona.
        """
        filas = self.con.execute(
            """SELECT * FROM eventos WHERE partido_id=? AND revisado=0
               ORDER BY pendiente DESC, confianza ASC, t_segundos ASC""",
            (partido_id,),
        ).fetchall()
        return [self._fila_a_evento(f) for f in filas]

    def borrar_evento(self, evento_id: int) -> None:
        with self.tx() as con:
            con.execute("DELETE FROM eventos WHERE id=?", (evento_id,))

    def borrar_eventos(self, partido_id: int, desde_s: float = 0.0,
                       hasta_s: float | None = None) -> None:
        with self.tx() as con:
            if hasta_s is None:
                con.execute(
                    "DELETE FROM eventos WHERE partido_id=? AND t_segundos>=?",
                    (partido_id, desde_s),
                )
            else:
                con.execute(
                    """DELETE FROM eventos WHERE partido_id=?
                       AND t_segundos>=? AND t_segundos<?""",
                    (partido_id, desde_s, hasta_s),
                )

    # ------------------------------------------------------------------
    # Correcciones
    # ------------------------------------------------------------------

    def guardar_correccion(self, partido_id: int, c: Correccion) -> int:
        with self.tx() as con:
            cur = con.execute(
                """INSERT INTO correcciones
                   (partido_id, evento_id, campo, valor_modelo, valor_persona, momento)
                   VALUES (?,?,?,?,?,?)""",
                (
                    partido_id,
                    c.evento_id,
                    c.campo,
                    c.valor_modelo,
                    c.valor_persona,
                    c.momento or _ahora(),
                ),
            )
            c.id = int(cur.lastrowid)
        return int(c.id)

    def correcciones(self, partido_id: int) -> list[Correccion]:
        filas = self.con.execute(
            "SELECT * FROM correcciones WHERE partido_id=? ORDER BY id", (partido_id,)
        ).fetchall()
        return [
            Correccion(
                id=f["id"],
                partido_id=f["partido_id"],
                evento_id=f["evento_id"],
                campo=f["campo"],
                valor_modelo=f["valor_modelo"] or "",
                valor_persona=f["valor_persona"] or "",
                momento=f["momento"],
            )
            for f in filas
        ]

    # ------------------------------------------------------------------
    # Transcripción del relato
    # ------------------------------------------------------------------

    def guardar_transcripcion(
        self, partido_id: int, fragmentos: list[dict[str, Any]]
    ) -> None:
        if not fragmentos:
            return
        with self.tx() as con:
            con.executemany(
                """INSERT INTO transcripcion
                   (partido_id, inicio_s, fin_s, texto, jugadores_nombrados)
                   VALUES (?,?,?,?,?)""",
                [
                    (
                        partido_id,
                        float(f.get("inicio_s", 0)),
                        float(f.get("fin_s", 0)),
                        f.get("texto", ""),
                        json.dumps(f.get("jugadores_nombrados", []), ensure_ascii=False),
                    )
                    for f in fragmentos
                ],
            )

    def transcripcion(
        self, partido_id: int, desde_s: float = 0.0, hasta_s: float | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM transcripcion WHERE partido_id=? AND fin_s>=?"
        args: list[Any] = [partido_id, desde_s]
        if hasta_s is not None:
            sql += " AND inicio_s<=?"
            args.append(hasta_s)
        sql += " ORDER BY inicio_s"
        return [
            {
                "inicio_s": f["inicio_s"],
                "fin_s": f["fin_s"],
                "texto": f["texto"],
                "jugadores_nombrados": json.loads(f["jugadores_nombrados"] or "[]"),
            }
            for f in self.con.execute(sql, args).fetchall()
        ]

    def borrar_transcripcion(self, partido_id: int) -> None:
        with self.tx() as con:
            con.execute("DELETE FROM transcripcion WHERE partido_id=?", (partido_id,))

    # ------------------------------------------------------------------
    # Progreso (para poder retomar)
    # ------------------------------------------------------------------

    def marcar_progreso(
        self, partido_id: int, paso: str, estado: str, detalle: str = ""
    ) -> None:
        with self.tx() as con:
            con.execute(
                """INSERT INTO progreso (partido_id, paso, estado, detalle, actualizado)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(partido_id, paso) DO UPDATE SET
                     estado=excluded.estado, detalle=excluded.detalle,
                     actualizado=excluded.actualizado""",
                (partido_id, paso, estado, detalle, _ahora()),
            )

    def progreso(self, partido_id: int) -> dict[str, dict[str, str]]:
        filas = self.con.execute(
            "SELECT * FROM progreso WHERE partido_id=?", (partido_id,)
        ).fetchall()
        return {
            f["paso"]: {
                "estado": f["estado"],
                "detalle": f["detalle"] or "",
                "actualizado": f["actualizado"],
            }
            for f in filas
        }
