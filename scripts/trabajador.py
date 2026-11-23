#!/usr/bin/env python3
"""El trabajador: la compu que hace el análisis pesado para la app web.

Se queda prendido esperando. Cada pocos segundos le pregunta a la base de la
nube si alguien apretó "Analizar" en la app; si hay un pedido, baja el video
de YouTube, corre el análisis con Claude y va subiendo el avance. Cuando
termina, el partido se pone en verde en la app.

    python scripts/trabajador.py            # queda esperando pedidos
    python scripts/trabajador.py --una-vez  # hace lo que haya y termina

Mientras analiza, la compu no se duerme (en Windows se le avisa al sistema).
Si se apaga a mitad de un análisis, al volver a prenderla sigue donde quedó.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

for _flujo in (sys.stdout, sys.stderr):
    try:
        _flujo.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from rugby_stats import costos, revision  # noqa: E402
from rugby_stats.analisis import pipeline  # noqa: E402
from rugby_stats.config import Config, cargar_config  # noqa: E402
from rugby_stats.db import BaseDatos, ruta_por_defecto  # noqa: E402
from rugby_stats.gemini import video as video_mod  # noqa: E402
from rugby_stats.gemini.cliente import ClienteSimulado, crear_cliente  # noqa: E402
from rugby_stats.modelos import Equipo, Jugador, Partido  # noqa: E402
from rugby_stats.nube import Nube, Trabajo  # noqa: E402

log = logging.getLogger("trabajador")

ESPERA_S = 10
LATIDO_S = 20


class Cancelado(pipeline.Interrumpir):
    pass


# ---------------------------------------------------------------------------
# Que la compu no se duerma mientras analiza
# ---------------------------------------------------------------------------


def no_dormir(activar: bool) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        estado = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if activar else 0)
        ctypes.windll.kernel32.SetThreadExecutionState(estado)
    except Exception:  # noqa: BLE001 - si no se puede, se sigue igual
        pass


# ---------------------------------------------------------------------------
# Latido: la app muestra "compu conectada" mientras llegue
# ---------------------------------------------------------------------------


class Latido(threading.Thread):
    def __init__(self, url: str | None):
        super().__init__(daemon=True)
        self.url = url
        self.estado = "esperando pedidos"
        self._parar = threading.Event()

    def run(self) -> None:
        nube = None
        while not self._parar.is_set():
            try:
                if nube is None:
                    nube = Nube(self.url)
                nube.latido(self.estado)
            except Exception as exc:  # noqa: BLE001 - sin internet un rato, se reintenta
                log.debug("No pude mandar el latido: %s", exc)
                if nube is not None:
                    nube.cerrar()
                nube = None
            self._parar.wait(LATIDO_S)
        if nube is not None:
            nube.cerrar()

    def parar(self) -> None:
        self._parar.set()


# ---------------------------------------------------------------------------
# Un pedido
# ---------------------------------------------------------------------------


def _partido_local(db: BaseDatos, fila: dict, jugadores: list[dict]) -> int:
    """Crea o actualiza en la base local el partido que viene de la app."""
    propio = Equipo(nombre=fila["equipo_propio"], kit=fila["kit_propio"] or "",
                    numero_legible=fila["legible_propio"] or "media", es_propio=True)
    rival = Equipo(nombre=fila["equipo_rival"], kit=fila["kit_rival"] or "",
                   numero_legible=fila["legible_rival"] or "media")
    existente = db.partido(fila["local_id"]) if fila.get("local_id") else None
    if existente and existente.video.referencia == fila["video_url"]:
        fuente = existente.video
    else:
        fuente = video_mod.detectar_fuente(fila["video_url"])
    p = Partido(
        nombre=fila["nombre"], equipo_propio=propio, equipo_rival=rival, video=fuente,
        analizar_desde_s=fila["desde_s"], analizar_hasta_s=fila["hasta_s"],
    )
    if existente:
        p.id = existente.id
        p.video.desfase_s = existente.video.desfase_s
    pid = db.guardar_partido(p)

    nuevos = [
        Jugador(numero=int(j["numero"]), nombre=j["nombre"], equipo=propio.nombre,
                apodo=j.get("apodo") or "", titular=bool(j.get("titular", True)))
        for j in jugadores
    ]
    actuales = db.plantel(pid, propio.nombre)

    def firma(js):
        return sorted((j.numero, j.nombre, j.apodo, j.titular) for j in js)

    # Solo se reemplaza si cambió: así no se pierden los cambios detectados.
    if firma(nuevos) != firma(actuales):
        db.guardar_plantel(pid, nuevos)
    return pid


def _eventos_para_subir(db: BaseDatos, pid: int, propio: str) -> list[dict]:
    salida = []
    for e in db.eventos(pid):
        if e.equipo and e.equipo != propio:
            continue
        prop = e.propuesta_original or {}
        salida.append({
            "t_segundos": e.t_segundos, "reloj": e.reloj, "accion": e.accion,
            "resultado": e.resultado, "jugador_numero": e.jugador_numero,
            "confianza": e.confianza, "pendiente": e.pendiente, "nota": e.nota,
            "candidatos": revision.candidatos_de(e),
            "secundarios": list(e.jugadores_secundarios or []),
        })
    return salida


def _subir_resultados(nube: Nube, db: BaseDatos, trabajo: Trabajo, pid: int,
                      propio: str) -> int:
    n = nube.subir_eventos(trabajo.partido_id, _eventos_para_subir(db, pid, propio))
    for numero, ruta in revision.foto_de_cada_jugador(db, pid, propio).items():
        try:
            nube.subir_foto(trabajo.partido_id, numero, Path(ruta).read_bytes())
        except Exception as exc:  # noqa: BLE001 - una foto que no sube no frena
            log.warning("No pude subir la foto del %s: %s", numero, exc)
    nube.actualizar_partido(trabajo.partido_id, gasto_usd=round(db.gasto_partido(pid), 4))
    return n


def procesar(nube: Nube, db: BaseDatos, cfg: Config, trabajo: Trabajo,
             latido: Latido | None = None, cliente=None) -> str:
    """Hace un pedido de análisis. Devuelve el estado final."""
    fila = nube.partido(trabajo.partido_id)
    if fila is None:
        nube.terminar_trabajo(trabajo.id, "error", "El partido ya no existe.")
        return "error"
    nombre = fila["nombre"]
    log.info("Analizando «%s» (pedido %s)", nombre, trabajo.id)
    if latido:
        latido.estado = f"analizando {nombre}"

    pid = None
    try:
        pid = _partido_local(db, fila, nube.jugadores(trabajo.partido_id))
        nube.actualizar_partido(trabajo.partido_id, local_id=pid)
        tramo, _entero = costos.tramo_y_partido(db.partido(pid), cfg)
        if tramo:
            nube.actualizar_partido(trabajo.partido_id, estimado_usd=round(tramo.usd, 2))

        cliente = cliente or crear_cliente(cfg)
        if isinstance(cliente, ClienteSimulado):
            raise RuntimeError(
                f"En la compu no está la clave {cfg.nombre_variable_clave} (archivo .env). "
                "Sin clave no se puede analizar de verdad."
            )

        ultimo = {"t": 0.0, "control": time.monotonic()}

        def progreso(texto: str, fraccion: float) -> None:
            ahora = time.monotonic()
            if ahora - ultimo["control"] > 15:
                ultimo["control"] = ahora
                if nube.cancelado(trabajo.id):
                    raise Cancelado("Cancelado desde la app.")
            if ahora - ultimo["t"] > 3 or fraccion >= 1.0:
                ultimo["t"] = ahora
                gasto = float(getattr(cliente, "gasto_usd", 0.0) or 0.0)
                try:
                    nube.actualizar_partido(
                        trabajo.partido_id, avance=round(max(0.0, min(1.0, fraccion)), 3),
                        mensaje=texto[:300],
                        gasto_usd=round(db.gasto_partido(pid) + gasto, 4),
                    )
                except Exception as exc:  # noqa: BLE001 - el avance no frena el análisis
                    log.debug("No pude subir el avance: %s", exc)

        no_dormir(True)
        res = pipeline.correr(db, cliente, pid, cfg, progreso=progreso)
        n = _subir_resultados(nube, db, trabajo, pid, fila["equipo_propio"])
        tackles = [e for e in db.eventos(pid) if e.accion in ("Tackle", "Tackle errado")]
        dudosos = len([e for e in tackles if e.pendiente or e.jugador_numero is None])
        mensaje = (
            f"Listo: {len(tackles)} tackles, {dudosos} para revisar. "
            f"Costó US$ {db.gasto_partido(pid):.2f}."
        )
        nube.actualizar_partido(trabajo.partido_id, estado="listo", avance=1.0,
                                mensaje=mensaje)
        nube.terminar_trabajo(trabajo.id, "listo", mensaje)
        log.info("%s (%d eventos subidos)", mensaje, n)
        _ = res
        return "listo"
    except Cancelado:
        if pid is not None:
            _subir_resultados(nube, db, trabajo, pid, fila["equipo_propio"])
        nube.actualizar_partido(trabajo.partido_id, estado="cancelado",
                                mensaje="Cancelado. Lo hecho quedó guardado.")
        nube.terminar_trabajo(trabajo.id, "cancelado")
        return "cancelado"
    except Exception as exc:  # noqa: BLE001 - se informa en la app
        log.exception("Falló el análisis de «%s»", nombre)
        try:
            if pid is not None:
                _subir_resultados(nube, db, trabajo, pid, fila["equipo_propio"])
        except Exception:  # noqa: BLE001
            pass
        mensaje = f"{exc}"
        nube.actualizar_partido(trabajo.partido_id, estado="error", mensaje=mensaje[:1000])
        nube.terminar_trabajo(trabajo.id, "error", mensaje)
        return "error"
    finally:
        no_dormir(False)
        if latido:
            latido.estado = "esperando pedidos"


# ---------------------------------------------------------------------------
# Bucle
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="Trabajador de la app web.")
    ap.add_argument("--una-vez", action="store_true",
                    help="Hace los pedidos que haya y termina.")
    ap.add_argument("--config")
    args = ap.parse_args()

    carpeta_log = RAIZ / "data"
    carpeta_log.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(carpeta_log / "trabajador.log", encoding="utf-8")],
    )
    cfg = cargar_config(args.config)
    db = BaseDatos(ruta_por_defecto())

    print("=" * 60)
    print("  rugby-stats-ia — trabajador de la app")
    print("  Dejá esta ventana abierta. Para cortar: cerrala.")
    print("=" * 60)

    nube = None
    latido = None
    while True:
        try:
            if nube is None:
                nube = Nube()
                nube.preparar()
                colgados = nube.recuperar_colgados()
                if colgados:
                    log.info("Retomo %d análisis que habían quedado a mitad.", colgados)
                if latido is None and not args.una_vez:
                    latido = Latido(nube.url)
                    latido.start()
                log.info("Conectado. Esperando pedidos de la app…")
            if args.una_vez:
                nube.latido("haciendo los pedidos pendientes")
            trabajo = nube.tomar_trabajo()
            if trabajo is None:
                if args.una_vez:
                    return 0
                time.sleep(ESPERA_S)
                continue
            procesar(nube, db, cfg, trabajo, latido)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:  # noqa: BLE001 - sin internet: se reintenta
            log.warning("Sin conexión con la base (%s). Reintento en 30 s.", exc)
            if nube is not None:
                nube.cerrar()
            nube = None
            if args.una_vez:
                return 1
            time.sleep(30)


if __name__ == "__main__":
    raise SystemExit(main())
