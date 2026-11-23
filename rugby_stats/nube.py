"""La base de datos compartida con la app web (Vercel).

La app web y la compu se hablan a través de una base Postgres en la nube:

- la app anota los partidos, los planteles y los pedidos de análisis
  (``trabajos``);
- la compu (``scripts/trabajador.py``) toma los pedidos, hace el análisis con
  la base local de siempre y sube el avance, los tackles, las fotos y lo
  gastado;
- la revisión se hace en la app y queda en la base de la nube.

La compu nunca recibe conexiones: es ella la que pregunta cada tanto si hay
algo para hacer. Así no hace falta abrir nada en el router ni tener una
dirección fija.

La dirección de la base va en ``.env`` como ``DATABASE_URL`` (la misma que
usa la app en Vercel).
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger(__name__)

ESQUEMA = Path(__file__).resolve().parent.parent / "web" / "esquema.sql"
VERSION = "1"


class ErrorNube(RuntimeError):
    pass


@dataclass
class Trabajo:
    id: int
    partido_id: int
    tipo: str


def url_base() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise ErrorNube(
            "Falta DATABASE_URL en el archivo .env: es la dirección de la base "
            "de datos de la app (se copia de Vercel → Storage)."
        )
    return url


class Nube:
    """Acceso a la base compartida. Una conexión por instancia."""

    def __init__(self, url: str | None = None):
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - se instala con requirements
            raise ErrorNube("Falta la librería psycopg: corré 1_instalar.bat.") from exc
        self._psycopg = psycopg
        self.url = url or url_base()
        self.con = psycopg.connect(self.url, autocommit=True, connect_timeout=20)

    def cerrar(self) -> None:
        try:
            self.con.close()
        except Exception:  # noqa: BLE001
            pass

    @contextmanager
    def cursor(self) -> Iterator[Any]:
        with self.con.cursor() as cur:
            yield cur

    def _fila(self, cur) -> dict[str, Any] | None:
        f = cur.fetchone()
        if f is None:
            return None
        return {d.name: v for d, v in zip(cur.description, f)}

    def _filas(self, cur) -> list[dict[str, Any]]:
        nombres = [d.name for d in cur.description]
        return [dict(zip(nombres, f)) for f in cur.fetchall()]

    # -- esquema y latido --------------------------------------------------

    def preparar(self) -> None:
        """Crea las tablas que falten (lo mismo que hace la app)."""
        with self.cursor() as cur:
            cur.execute(ESQUEMA.read_text(encoding="utf-8"))

    def latido(self, estado: str) -> None:
        """Avisa que la compu está prendida y qué está haciendo."""
        with self.cursor() as cur:
            cur.execute(
                """INSERT INTO compu (id, ultimo_latido, estado, version)
                   VALUES (1, now(), %s, %s)
                   ON CONFLICT (id) DO UPDATE
                   SET ultimo_latido = now(), estado = EXCLUDED.estado,
                       version = EXCLUDED.version""",
                (estado[:300], VERSION),
            )

    # -- trabajos -----------------------------------------------------------

    def tomar_trabajo(self) -> Trabajo | None:
        """El próximo pedido en cola, marcado como tomado (de a uno)."""
        with self.cursor() as cur:
            cur.execute(
                """UPDATE trabajos SET estado = 'corriendo', empezado = now()
                   WHERE id = (
                     SELECT id FROM trabajos WHERE estado = 'en_cola'
                     ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED)
                   RETURNING id, partido_id, tipo"""
            )
            f = self._fila(cur)
        if f is None:
            return None
        self.actualizar_partido(f["partido_id"], estado="corriendo", avance=0.0,
                                mensaje="Arrancando…")
        return Trabajo(id=f["id"], partido_id=f["partido_id"], tipo=f["tipo"])

    def recuperar_colgados(self) -> int:
        """Trabajos que quedaron 'corriendo' porque la compu se apagó a mitad.

        Vuelven a la cola: el análisis es reanudable, sigue donde quedó.
        """
        with self.cursor() as cur:
            cur.execute(
                "UPDATE trabajos SET estado = 'en_cola' WHERE estado = 'corriendo' "
                "RETURNING partido_id"
            )
            ids = [f[0] for f in cur.fetchall()]
            for pid in ids:
                cur.execute(
                    "UPDATE partidos SET estado = 'en_cola', mensaje = %s, actualizado = now() "
                    "WHERE id = %s",
                    ("La compu se había apagado: sigue donde quedó.", pid),
                )
        return len(ids)

    def cancelado(self, trabajo_id: int) -> bool:
        with self.cursor() as cur:
            cur.execute("SELECT estado FROM trabajos WHERE id = %s", (trabajo_id,))
            f = cur.fetchone()
        return f is None or f[0] == "cancelado"

    def terminar_trabajo(self, trabajo_id: int, estado: str, mensaje: str = "") -> None:
        with self.cursor() as cur:
            cur.execute(
                "UPDATE trabajos SET estado = %s, terminado = now(), mensaje = %s "
                "WHERE id = %s AND estado <> 'cancelado'",
                (estado, mensaje[:2000], trabajo_id),
            )

    # -- partidos -----------------------------------------------------------

    def partido(self, partido_id: int) -> dict[str, Any] | None:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM partidos WHERE id = %s", (partido_id,))
            return self._fila(cur)

    def jugadores(self, partido_id: int) -> list[dict[str, Any]]:
        with self.cursor() as cur:
            cur.execute(
                "SELECT numero, nombre, apodo, titular FROM jugadores "
                "WHERE partido_id = %s ORDER BY numero",
                (partido_id,),
            )
            return self._filas(cur)

    _CAMPOS_PARTIDO = {"estado", "avance", "mensaje", "gasto_usd", "estimado_usd", "local_id"}

    def actualizar_partido(self, partido_id: int, **campos: Any) -> None:
        campos = {k: v for k, v in campos.items() if k in self._CAMPOS_PARTIDO}
        if not campos:
            return
        partes = ", ".join(f"{k} = %s" for k in campos)
        with self.cursor() as cur:
            cur.execute(
                f"UPDATE partidos SET {partes}, actualizado = now() WHERE id = %s",
                (*campos.values(), partido_id),
            )

    # -- resultados ---------------------------------------------------------

    def subir_eventos(self, partido_id: int, eventos: list[dict[str, Any]]) -> int:
        """Reemplaza los eventos sin revisar por los nuevos.

        Lo que ya revisó una persona en la app no se toca, y no se sube un
        evento nuevo que caiga encima de uno revisado (mismo momento).
        """
        with self.con.transaction(), self.cursor() as cur:
            cur.execute(
                "SELECT t_segundos FROM eventos WHERE partido_id = %s AND revisado",
                (partido_id,),
            )
            revisados = [f[0] for f in cur.fetchall()]
            cur.execute(
                "DELETE FROM eventos WHERE partido_id = %s AND NOT revisado", (partido_id,)
            )
            n = 0
            for e in eventos:
                if any(abs(e["t_segundos"] - t) < 2.0 for t in revisados):
                    continue
                cur.execute(
                    """INSERT INTO eventos
                       (partido_id, t_segundos, reloj, accion, resultado,
                        jugador_numero, jugador_modelo, resultado_modelo, confianza,
                        pendiente, nota, candidatos, secundarios)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        partido_id, e["t_segundos"], e.get("reloj", ""), e["accion"],
                        e.get("resultado", ""), e.get("jugador_numero"),
                        e.get("jugador_numero"), e.get("resultado", ""),
                        float(e.get("confianza", 0.0)), bool(e.get("pendiente")),
                        e.get("nota", "")[:2000],
                        json.dumps(e.get("candidatos") or []),
                        json.dumps(e.get("secundarios") or []),
                    ),
                )
                n += 1
        return n

    def subir_foto(self, partido_id: int, numero: int, jpg: bytes) -> None:
        with self.cursor() as cur:
            cur.execute(
                """INSERT INTO fotos (partido_id, numero, imagen) VALUES (%s,%s,%s)
                   ON CONFLICT (partido_id, numero) DO UPDATE SET imagen = EXCLUDED.imagen""",
                (partido_id, numero, jpg),
            )
