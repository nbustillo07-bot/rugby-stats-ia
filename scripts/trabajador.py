#!/usr/bin/env python3
"""El trabajador: la compu que hace el análisis pesado para la app web.

Se queda prendido esperando. Cuando alguien aprieta "Analizar" en la app, la
app le manda un aviso por ntfy (un servicio gratis de notificaciones) y la
compu se despierta al instante; baja el video de YouTube, corre el análisis
con Claude y va subiendo el avance. Cuando termina, el partido se pone en
verde en la app.

Mientras no hay nada que hacer, le pregunta a la base solo cada 15 minutos
(por si se perdió algún aviso). Así la base de Neon se puede dormir: el plan
gratis da 100 horas de cómputo por mes, y preguntando cada pocos segundos se
gastarían en dos semanas.

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
from rugby_stats import config as config_mod  # noqa: E402
from rugby_stats.config import Config, cargar_config  # noqa: E402
from rugby_stats.db import BaseDatos, ruta_por_defecto  # noqa: E402
from rugby_stats.gemini import video as video_mod  # noqa: E402
from rugby_stats.gemini.cliente import ClienteSimulado, crear_cliente  # noqa: E402
from rugby_stats.modelos import Equipo, Jugador, Partido  # noqa: E402
from rugby_stats.nube import Nube, Trabajo  # noqa: E402

log = logging.getLogger("trabajador")

#: Sin trabajo, cada cuánto se pregunta a la base (la base se duerme a los 5
#: minutos sin consultas; con 15 minutos queda dormida dos tercios del tiempo).
ESPERA_S = 15 * 60
#: Después de un análisis o de un aviso, durante un rato se pregunta seguido.
ESPERA_ACTIVA_S = 15
VENTANA_ACTIVA_S = 20 * 60
LATIDO_S = 60
NTFY = "https://ntfy.sh"
#: Cada cuánto, si no hay nada para hacer, el trabajador se reinicia para
#: traer las actualizaciones (lo hace 7_trabajador.bat al volver a arrancar).
REINICIO_S = 6 * 3600
SALIDA_ACTUALIZAR = 3


class Cancelado(pipeline.Interrumpir):
    pass


# ---------------------------------------------------------------------------
# Uno solo a la vez (por si se abre dos veces: con Windows y a mano)
# ---------------------------------------------------------------------------

SALIDA_YA_HAY_OTRO = 4
_candado = None


def tomar_candado(carpeta: Path) -> bool:
    """True si este es el único trabajador abierto en esta compu."""
    global _candado
    carpeta.mkdir(parents=True, exist_ok=True)
    archivo = open(carpeta / "trabajador.lock", "a+")  # noqa: SIM115 - queda abierto a propósito
    try:
        if sys.platform == "win32":
            import msvcrt

            archivo.seek(0)
            msvcrt.locking(archivo.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(archivo.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        archivo.close()
        return False
    _candado = archivo
    return True


def version_actual() -> str:
    """El commit de git que está corriendo, para verlo desde la app."""
    import subprocess

    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=RAIZ,
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:  # noqa: BLE001 - sin git, se sigue igual
        pass
    return ""


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


def canal_de_aviso(carpeta: Path) -> str:
    """El canal de ntfy de esta compu: un nombre al azar, guardado en disco.

    Cualquiera que sepa el nombre puede mandar un aviso, pero lo único que
    hace un aviso es que la compu le pregunte a la base si hay trabajo.
    """
    import secrets

    ruta = carpeta / "canal_aviso.txt"
    try:
        canal = ruta.read_text(encoding="utf-8").strip()
        if canal:
            return canal
    except OSError:
        pass
    canal = "rugby-stats-" + secrets.token_urlsafe(18).replace("_", "x").replace("-", "y")
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(canal, encoding="utf-8")
    return canal


class Despertador(threading.Thread):
    """Escucha el canal de ntfy y despierta al bucle cuando llega un aviso."""

    def __init__(self, canal: str, base: str = NTFY):
        super().__init__(daemon=True)
        self.url = f"{base.rstrip('/')}/{canal}/json"
        self.evento = threading.Event()
        self._parar = threading.Event()

    def run(self) -> None:
        import json
        import urllib.request

        while not self._parar.is_set():
            try:
                with urllib.request.urlopen(self.url, timeout=120) as r:
                    for linea in r:
                        if self._parar.is_set():
                            return
                        try:
                            d = json.loads(linea)
                        except ValueError:
                            continue
                        if d.get("event") == "message":
                            log.info("Llegó un aviso de la app.")
                            self.evento.set()
            except Exception as exc:  # noqa: BLE001 - sin internet: se reintenta
                log.debug("Despertador sin conexión: %s", exc)
                self._parar.wait(30)

    def esperar(self, segundos: float) -> bool:
        """Espera hasta ``segundos`` o hasta que llegue un aviso."""
        llego = self.evento.wait(segundos)
        self.evento.clear()
        return llego

    def parar(self) -> None:
        self._parar.set()


class Latido(threading.Thread):
    """Mientras se analiza, avisa cada minuto que la compu sigue trabajando."""

    def __init__(self, url: str | None, aviso: str = ""):
        super().__init__(daemon=True)
        self.url = url
        self.aviso = aviso
        self.estado = "esperando pedidos"
        self._parar = threading.Event()

    def run(self) -> None:
        nube = None
        while not self._parar.is_set():
            try:
                if nube is None:
                    nube = Nube(self.url)
                nube.latido(self.estado, self.aviso)
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


PASOS_REHACER = ["cambios", "fichas", "eventos", "contactos"]


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
    fotos = revision.foto_de_cada_jugador(db, pid, propio)
    if fotos:
        nube.borrar_fotos(trabajo.partido_id)
    for numero, ruta in fotos.items():
        try:
            nube.subir_foto(trabajo.partido_id, numero, Path(ruta).read_bytes())
        except Exception as exc:  # noqa: BLE001 - una foto que no sube no frena
            log.warning("No pude subir la foto del %s: %s", numero, exc)
    nube.actualizar_partido(trabajo.partido_id, gasto_usd=round(db.gasto_partido(pid), 4))
    return n


def _bajar_album(nube: Nube, trabajo: Trabajo, carpeta: Path) -> dict[int, list[str]]:
    """Baja las fotos del álbum de los jugadores de este partido."""
    album: dict[int, list[str]] = {}
    destino = carpeta / f"p{trabajo.partido_id}"
    destino.mkdir(parents=True, exist_ok=True)
    for numero, imagenes in nube.album_de_partido(trabajo.partido_id).items():
        for k, img in enumerate(imagenes):
            ruta = destino / f"n{numero}_{k}.jpg"
            ruta.write_bytes(img)
            album.setdefault(numero, []).append(str(ruta))
    return album


def _fotos_para_el_album(db: BaseDatos, pid: int, propio: str, por_jugador: int = 2) -> dict[int, list[bytes]]:
    """Los mejores recortes de hoy de cada jugador, de momentos distintos."""
    salida: dict[int, list[bytes]] = {}
    momentos: dict[int, list[float]] = {}
    fichas = sorted(db.fichas(pid, propio), key=lambda f: (-f.confianza, f.t_segundos))
    for f in fichas:
        if f.confianza < 0.6 or not f.imagen or not Path(f.imagen).exists():
            continue
        ts = momentos.setdefault(f.jugador_numero, [])
        if len(ts) >= por_jugador or any(abs(f.t_segundos - x) < 15 for x in ts):
            continue
        ts.append(f.t_segundos)
        salida.setdefault(f.jugador_numero, []).append(Path(f.imagen).read_bytes())
    return salida


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
        try:
            album = _bajar_album(nube, trabajo, cfg.dir_video.parent / "album")
        except Exception as exc:  # noqa: BLE001 - sin álbum se analiza igual
            log.warning("No pude bajar el álbum: %s", exc)
            album = {}
        if album:
            log.info("Fotos del álbum: %d jugadores ya tienen fotos de antes.", len(album))
        if trabajo.tipo == "rehacer":
            # Rehacer sin volver a bajar el video ni a cortar el partido en
            # tramos: desde los cambios en adelante, todo de nuevo.
            res = pipeline.correr(db, cliente, pid, cfg, progreso=progreso, album=album,
                                  pasos=PASOS_REHACER, forzar=True)
        else:
            res = pipeline.correr(db, cliente, pid, cfg, progreso=progreso, album=album)
        n = _subir_resultados(nube, db, trabajo, pid, fila["equipo_propio"])
        try:
            sumadas = nube.sumar_al_album(
                trabajo.partido_id, _fotos_para_el_album(db, pid, fila["equipo_propio"]))
            log.info("Al álbum: %d fotos nuevas.", sumadas)
        except Exception as exc:  # noqa: BLE001 - el álbum no frena nada
            log.warning("No pude sumar fotos al álbum: %s", exc)
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
    if not tomar_candado(RAIZ / "data"):
        print("Ya hay un trabajador abierto en esta compu. Esta ventana se cierra.")
        return SALIDA_YA_HAY_OTRO
    cfg = cargar_config(args.config)
    db = BaseDatos(ruta_por_defecto())
    import rugby_stats.nube as nube_mod

    version = version_actual()
    if version:
        nube_mod.VERSION = version

    print("=" * 60)
    print("  rugby-stats-ia — trabajador de la app")
    print("  Dejá esta ventana abierta. Para cortar: cerrala.")
    print("=" * 60)

    canal = canal_de_aviso(RAIZ / "data")
    despertador = None
    if not args.una_vez:
        despertador = Despertador(canal)
        despertador.start()

    nube = None
    arranque = time.monotonic()
    activa_hasta = time.monotonic() + VENTANA_ACTIVA_S  # al arrancar, un rato atento
    while True:
        try:
            if nube is None:
                nube = Nube()
                nube.preparar()
                colgados = nube.recuperar_colgados()
                if colgados:
                    log.info("Retomo %d análisis que habían quedado a mitad.", colgados)
                log.info("Conectado. Esperando pedidos de la app…")
            nube.latido("esperando pedidos", canal)
            trabajo = nube.tomar_trabajo()
            if trabajo is None:
                if args.una_vez:
                    return 0
                if time.monotonic() - arranque > REINICIO_S:
                    log.info("Me reinicio para buscar actualizaciones.")
                    return SALIDA_ACTUALIZAR
                activa = time.monotonic() < activa_hasta
                if not activa:
                    # Se cierra la conexión para que la base se pueda dormir.
                    nube.cerrar()
                    nube = None
                espera = ESPERA_ACTIVA_S if activa else ESPERA_S
                if despertador.esperar(espera):
                    activa_hasta = time.monotonic() + VENTANA_ACTIVA_S
                continue
            # Por si se pegó la clave o se cambió la configuración con el
            # trabajador ya abierto: se vuelven a leer antes de cada análisis.
            config_mod._cargar_env(RAIZ / ".env", pisar=True)
            try:
                cfg = cargar_config(args.config)
            except Exception as exc:  # noqa: BLE001 - se sigue con la que había
                log.warning("No pude releer config.yaml (%s): sigo con la anterior.", exc)
            latido = Latido(nube.url, canal)
            latido.start()
            try:
                procesar(nube, db, cfg, trabajo, latido)
            finally:
                latido.parar()
            activa_hasta = time.monotonic() + VENTANA_ACTIVA_S
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
