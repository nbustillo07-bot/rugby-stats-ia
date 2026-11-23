"""Cliente de Gemini con cola, reintentos y caché en disco.

Tres cosas que hacen que esto funcione con el free tier:

1. **Cola con ritmo.** Los límites por minuto y por día dependen del modelo y
   de la cuenta, y se ven en AI Studio. Acá no hay ningún límite hardcodeado:
   se arranca con un ritmo razonable y ante un 429 se baja solo.
2. **Reintentos con espera creciente.** Un 429 no es un error, es "esperá".
3. **Caché en disco.** Cada pedido se guarda por su hash. Si el análisis se
   corta a la mitad del partido, al retomarlo no se vuelve a pagar lo ya hecho.

El modo simulado (``ClienteSimulado``) devuelve respuestas sintéticas con la
forma correcta. Sirve para probar el resto del sistema sin clave y sin gastar
cuota; **no mide nada**, y las salidas quedan marcadas como simuladas.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)


_RESOLUCIONES = (
    "MEDIA_RESOLUTION_LOW",
    "MEDIA_RESOLUTION_MEDIUM",
    "MEDIA_RESOLUTION_HIGH",
)


class ErrorGemini(RuntimeError):
    pass


class CuotaAgotada(ErrorGemini):
    """429 que no se resolvió después de todos los reintentos."""


class CupoDiarioAgotado(CuotaAgotada):
    """Se acabaron los pedidos gratis de hoy para todos los modelos del grupo.

    No es un error del programa ni se arregla reintentando: hay que esperar a
    que Google renueve el cupo. Lo hecho hasta acá queda guardado.
    """

    def __init__(self, mensaje: str, nivel: str = "", renueva_en_s: float | None = None):
        super().__init__(mensaje)
        self.nivel = nivel
        self.renueva_en_s = renueva_en_s


@dataclass
class Pedido:
    """Un pedido al modelo.

    ``partes_video`` describe qué pedazo de video mirar y a qué velocidad; el
    cliente lo traduce a los objetos del SDK.
    """

    prompt: str
    video_uri: str | None = None
    video_mime: str = "video/*"
    inicio_s: float | None = None
    fin_s: float | None = None
    fps: float | None = None
    resolucion: str = "MEDIA_RESOLUTION_LOW"
    esquema: dict[str, Any] | None = None
    etiqueta: str = ""
    # Si es True, no se manda el audio (no se puede pedir al modelo, pero sirve
    # para marcar en el caché que este pedido no depende del relato).
    solo_video: bool = True
    # Límite de tiempo propio de este pedido, en segundos. Si es None vale el
    # del cliente.
    timeout_s: float | None = None
    # Qué grupo de modelos atiende este pedido: "fuerte" (pocos pedidos por
    # día, para lo difícil) o "liviano" (muchos, para el volumen).
    nivel: str = "fuerte"
    # Ancho de cada cuadro, para los proveedores que reciben imágenes (Claude).
    # 0 = lo decide el proveedor según la resolución.
    ancho: int = 0
    # Fichas visuales: (etiqueta, ruta de la imagen) de cada jugador, para que
    # el modelo compare imagen contra imagen. Gemini las ignora.
    referencias: list = field(default_factory=list)
    # Zoom que sigue al contacto (solo Claude): {"pista": [[t, [x1,y1,x2,y2]],
    # ...], "ancho_fuente", "escala", "minimo", "contexto_fps",
    # "contexto_ancho"}. Con zoom, los cuadros van recortados alrededor del
    # contacto y se suman unos pocos de la vista general. Gemini lo ignora.
    zoom: dict | None = None
    # El comienzo de ``prompt`` que es igual en muchos pedidos del partido
    # (planteles, instrucciones). Claude lo manda primero y lo guarda en caché
    # (se cobra al 10%); el resto del prompt va después de las imágenes. Los
    # otros proveedores mandan el prompt entero, como siempre.
    prefijo: str = ""
    # Título del bloque de imágenes de referencia (por defecto, "fichas
    # visuales" del equipo propio).
    titulo_referencias: str = ""

    def clave_cache(self, modelo: str) -> str:
        crudo = json.dumps(
            {
                "modelo": modelo,
                "prompt": self.prompt,
                "uri": self.video_uri,
                "inicio": self.inicio_s,
                "fin": self.fin_s,
                "fps": self.fps,
                "res": self.resolucion,
                "esquema": self.esquema,
                # Solo entran a la clave si se usan, para no invalidar la
                # caché de pedidos viejos.
                **({"ancho": self.ancho} if self.ancho else {}),
                **({"refs": [list(r) for r in self.referencias]} if self.referencias else {}),
                **({"zoom": self.zoom} if self.zoom else {}),
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(crudo.encode("utf-8")).hexdigest()[:32]


@dataclass
class Respuesta:
    texto: str
    datos: Any = None
    desde_cache: bool = False
    simulada: bool = False
    modelo: str = ""
    tokens_entrada: int | None = None
    tokens_salida: int | None = None
    etiqueta: str = ""

    def json(self) -> Any:
        """Devuelve el JSON de la respuesta, tolerando que venga en un bloque."""
        if self.datos is not None:
            return self.datos
        return extraer_json(self.texto)


def extraer_json(texto: str) -> Any:
    """Saca el JSON de una respuesta, aunque venga envuelto en ```json."""
    t = (texto or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    t = t.strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    # Último recurso: el primer objeto o lista balanceada del texto.
    # Importa arrancar por el delimitador que aparece ANTES: si no, en
    # '{"eventos": [...]}' se tomaría el corchete interno y se devolvería la
    # lista en lugar del objeto entero.
    candidatos = [
        (t.find(apertura), apertura, cierre)
        for apertura, cierre in (("{", "}"), ("[", "]"))
        if t.find(apertura) != -1
    ]
    for _, apertura, cierre in sorted(candidatos):
        i = t.find(apertura)
        nivel = 0
        en_texto = False
        escapado = False
        for j in range(i, len(t)):
            c = t[j]
            if en_texto:
                # Dentro de una cadena, las llaves no cuentan.
                if escapado:
                    escapado = False
                elif c == "\\":
                    escapado = True
                elif c == '"':
                    en_texto = False
                continue
            if c == '"':
                en_texto = True
            elif c == apertura:
                nivel += 1
            elif c == cierre:
                nivel -= 1
                if nivel == 0:
                    try:
                        return json.loads(t[i : j + 1])
                    except json.JSONDecodeError:
                        break
    raise ErrorGemini(f"La respuesta no es JSON válido: {texto[:300]!r}")


class _Ritmo:
    """Limitador simple de pedidos por minuto que se adapta a los 429."""

    def __init__(self, rpm: float, rpm_minimo: float = 1.0):
        self.rpm = max(rpm_minimo, float(rpm))
        self.rpm_minimo = rpm_minimo
        self.rpm_original = self.rpm
        self._ultimo = 0.0

    @property
    def intervalo_s(self) -> float:
        return 60.0 / self.rpm

    def esperar(self, dormir: Callable[[float], None] | None = None) -> None:
        ahora = time.monotonic()
        falta = self._ultimo + self.intervalo_s - ahora
        if falta > 0:
            (dormir or time.sleep)(falta)
        self._ultimo = time.monotonic()

    def bajar(self) -> None:
        """Ante un 429, la mitad del ritmo."""
        self.rpm = max(self.rpm_minimo, self.rpm / 2)
        log.warning("Bajo el ritmo a %.1f pedidos por minuto", self.rpm)

    def recuperar(self) -> None:
        """De a poco, después de una racha sin errores."""
        self.rpm = min(self.rpm_original, self.rpm * 1.25)


class Cache:
    def __init__(self, directorio: Path, activa: bool = True):
        self.dir = Path(directorio)
        self.activa = activa
        if self.activa:
            self.dir.mkdir(parents=True, exist_ok=True)

    def leer(self, clave: str) -> Respuesta | None:
        if not self.activa:
            return None
        f = self.dir / f"{clave}.json"
        if not f.exists():
            return None
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return Respuesta(
            texto=d.get("texto", ""),
            datos=d.get("datos"),
            desde_cache=True,
            simulada=d.get("simulada", False),
            modelo=d.get("modelo", ""),
            tokens_entrada=d.get("tokens_entrada"),
            tokens_salida=d.get("tokens_salida"),
            etiqueta=d.get("etiqueta", ""),
        )

    def guardar(self, clave: str, r: Respuesta) -> None:
        if not self.activa:
            return
        f = self.dir / f"{clave}.json"
        f.write_text(
            json.dumps(
                {
                    "texto": r.texto,
                    "datos": r.datos,
                    "simulada": r.simulada,
                    "modelo": r.modelo,
                    "tokens_entrada": r.tokens_entrada,
                    "tokens_salida": r.tokens_salida,
                    "etiqueta": r.etiqueta,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def tamano(self) -> int:
        return len(list(self.dir.glob("*.json"))) if self.dir.exists() else 0


@dataclass
class Contadores:
    pedidos: int = 0
    desde_cache: int = 0
    reintentos: int = 0
    errores_429: int = 0
    saturaciones: int = 0
    costo_usd: float = 0.0
    tokens_entrada: int = 0
    tokens_salida: int = 0
    segundos_video: float = 0.0

    def resumen(self) -> str:
        return (
            f"{self.pedidos} pedidos ({self.desde_cache} de caché), "
            f"{self.reintentos} reintentos ({self.errores_429} por cuota, "
            f"{self.saturaciones} por saturación), "
            f"{self.tokens_entrada:,} tokens de entrada"
            + (f", US$ {self.costo_usd:.2f}" if self.costo_usd else "")
        )


class Cupos:
    """Lleva la cuenta de los pedidos de hoy y de qué modelos están agotados.

    Google no ofrece una forma de preguntar cuánto cupo queda, así que se
    cuenta de este lado. Es una estimación: si la misma clave se usa desde
    otro programa, Google lleva una cuenta distinta. Lo que manda siempre es
    la respuesta de Google; esto sirve para mostrar por dónde va el gasto y
    para no insistirle a un modelo que ya dijo que no.
    """

    def __init__(self, archivo: Path | None = None):
        self.archivo = archivo
        self.dia = self._hoy()
        self.uso: dict[str, int] = {}
        self.agotados: dict[str, float] = {}
        self._leer()

    @staticmethod
    def _hoy() -> str:
        # El cupo diario de Google se renueva a la medianoche del Pacífico.
        import datetime as _dt

        ahora = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=8)
        return ahora.strftime("%Y-%m-%d")

    def _leer(self) -> None:
        if not self.archivo or not self.archivo.exists():
            return
        try:
            d = json.loads(self.archivo.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        self.agotados = {
            str(k): float(v) for k, v in (d.get("agotados") or {}).items()
        }
        if d.get("dia") == self.dia:
            self.uso = {str(k): int(v) for k, v in (d.get("uso") or {}).items()}

    def _guardar(self) -> None:
        if not self.archivo:
            return
        try:
            self.archivo.parent.mkdir(parents=True, exist_ok=True)
            self.archivo.write_text(
                json.dumps(
                    {"dia": self.dia, "uso": self.uso, "agotados": self.agotados},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass  # llevar la cuenta es una ayuda, no puede frenar el análisis

    def _cambio_de_dia(self) -> None:
        hoy = self._hoy()
        if hoy != self.dia:
            self.dia = hoy
            self.uso = {}

    def anotar(self, modelo: str) -> None:
        self._cambio_de_dia()
        self.uso[modelo] = self.uso.get(modelo, 0) + 1
        self._guardar()

    def usados(self, modelo: str) -> int:
        self._cambio_de_dia()
        return self.uso.get(modelo, 0)

    def marcar_agotado(self, modelo: str, demora_s: float | None) -> None:
        # Sin dato de Google, se supone que vuelve en 12 horas.
        self.agotados[modelo] = time.time() + (demora_s if demora_s else 12 * 3600)
        self._guardar()

    def agotado(self, modelo: str) -> bool:
        hasta = self.agotados.get(modelo)
        if hasta is None:
            return False
        if time.time() >= hasta:
            del self.agotados[modelo]
            self._guardar()
            return False
        return True

    def proxima_renovacion(self, modelos: list[str]) -> float | None:
        """En cuántos segundos vuelve a haber cupo en alguno de estos modelos."""
        ahora = time.time()
        esperas = [self.agotados[m] - ahora for m in modelos if m in self.agotados]
        esperas = [e for e in esperas if e > 0]
        return min(esperas) if esperas else None


def _demora_pedida(exc: Exception) -> float | None:
    """Cuánto pide esperar Google en un 429, en segundos, si lo dice."""
    texto = str(exc)
    m = re.search(r"retryDelay['\"]?\s*:\s*['\"]?([\d.]+)s", texto)
    if m:
        return float(m.group(1))
    m = re.search(
        r"retry in\s+(?:(\d+)h)?\s*(?:(\d+)m(?!s))?\s*(?:([\d.]+)s)?", texto, re.I
    )
    if m and any(m.groups()):
        return (
            int(m.group(1) or 0) * 3600
            + int(m.group(2) or 0) * 60
            + float(m.group(3) or 0)
        )
    return None


def _es_cupo_diario(exc: Exception, demora: float | None) -> bool:
    """¿El 429 es por el tope del día (y no por ir muy rápido)?"""
    texto = str(exc).lower()
    if "per day" in texto or "perday" in texto or "requests per day" in texto:
        return True
    if "per minute" in texto or "perminute" in texto:
        return False
    # Sin texto claro: si pide esperar más de diez minutos, no es el ritmo.
    return bool(demora and demora > 600)


def _duracion(segundos: float | None) -> str:
    if not segundos or segundos <= 0:
        return "un rato"
    if segundos < 90:
        return f"{segundos:.0f} segundos"
    if segundos < 5400:
        return f"{segundos / 60:.0f} minutos"
    return f"unas {segundos / 3600:.0f} horas"


class ClienteGemini:
    """Habla con la API de Gemini.

    Los modelos se organizan en **niveles**. El plan gratis da muy pocos
    pedidos por día a los modelos buenos (20 cada uno) y muchos a los
    livianos (500 cada uno), así que cada pedido dice qué nivel necesita y el
    cliente va usando los modelos de ese nivel en orden, pasando al siguiente
    cuando a uno se le acaba el cupo del día.
    """

    def __init__(
        self,
        api_key: str,
        modelo: str = "gemini-3.8-flash",
        alternativas: list[str] | None = None,
        rpm: float = 8,
        esperas_429: list[float] | None = None,
        max_reintentos: int = 4,
        dir_cache: Path | str = "data/cache",
        usar_cache: bool = True,
        temperatura: float = 0.1,
        timeout_s: float = 600.0,
        niveles: dict[str, list[str]] | None = None,
        rpm_por_nivel: dict[str, float] | None = None,
        respaldo: dict[str, str] | None = None,
        archivo_cupos: Path | str | None = None,
    ):
        from google import genai  # import tardío: no hace falta en modo simulado

        from google.genai import types

        self._genai = genai
        # Límite de tiempo por pedido, en milisegundos. Sin esto, si la
        # conexión se cuelga (antivirus, firewall, wifi), el programa se queda
        # esperando para siempre sin decir nada.
        self.cliente = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=int(timeout_s * 1000)),
        )
        self.modelo = modelo
        self.alternativas = list(alternativas or [])
        #: nivel -> modelos en orden de preferencia. Si no se pasan niveles,
        #: todos los pedidos usan ``modelo`` y sus ``alternativas``.
        self.niveles: dict[str, list[str]] | None = (
            {k: list(v) for k, v in niveles.items()} if niveles else None
        )
        #: nivel -> a qué otro nivel recurrir si se agota (o nada: frenar).
        self.respaldo: dict[str, str] = dict(respaldo or {})
        self.temperatura = temperatura
        self._rpm = float(rpm)
        self._rpm_por_nivel = {k: float(v) for k, v in (rpm_por_nivel or {}).items()}
        self._ritmos: dict[str, _Ritmo] = {}
        self.ritmo = _Ritmo(rpm)  # el del modelo que se usó último
        self.esperas = list(esperas_429 or [5, 15, 45, 120])
        self.max_reintentos = max_reintentos
        self.cache = Cache(Path(dir_cache), usar_cache)
        self.contadores = Contadores()
        self._modelo_en_uso = modelo
        self._en_uso_por_nivel: dict[str, str] = {}
        #: Función opcional que recibe los avisos (reintentos, cambios de
        #: modelo) para mostrarlos en pantalla.
        self.al_avisar: Callable[[str], None] | None = None
        #: Los errores que fue devolviendo Google, para el diagnóstico.
        self.errores_vistos: list[str] = []
        #: Registro de cupos: cuántos pedidos se le hicieron hoy a cada
        #: modelo y cuáles están agotados hasta cuándo. Va a disco para que
        #: valga entre una corrida y la siguiente.
        self.cupos = Cupos(Path(archivo_cupos) if archivo_cupos else None)

    # -- niveles y modelos --------------------------------------------------

    def modelos_de(self, nivel: str) -> list[str]:
        """Los modelos de un nivel, en orden de preferencia."""
        if self.niveles and nivel in self.niveles:
            return list(self.niveles[nivel])
        return [self.modelo] + [m for m in self.alternativas if m != self.modelo]

    def _candidatos(self, nivel: str) -> list[str]:
        """Modelos del nivel que hoy todavía tienen cupo, el vigente primero."""
        disponibles = [m for m in self.modelos_de(nivel) if not self.cupos.agotado(m)]
        vigente = self._en_uso_por_nivel.get(nivel) or (
            self._modelo_en_uso if not self.niveles else None
        )
        if vigente in disponibles:
            disponibles.remove(vigente)
            disponibles.insert(0, vigente)
        return disponibles

    def _ritmo_de(self, modelo: str, nivel: str) -> "_Ritmo":
        if modelo not in self._ritmos:
            # El ritmo es del modelo, no del pedido: se busca a qué nivel
            # pertenece de verdad, por si el pedido vino con un nivel ad hoc.
            propio = next(
                (
                    n
                    for n in ("fuerte", "liviano")
                    if self.niveles and modelo in self.niveles.get(n, [])
                ),
                nivel,
            )
            self._ritmos[modelo] = _Ritmo(self._rpm_por_nivel.get(propio, self._rpm))
        return self._ritmos[modelo]

    # -- armado del pedido ------------------------------------------------

    def _partes(self, p: Pedido) -> list[Any]:
        from google.genai import types

        partes: list[Any] = []
        if p.video_uri:
            meta_kwargs: dict[str, Any] = {}
            if p.inicio_s is not None:
                meta_kwargs["start_offset"] = f"{p.inicio_s:.3f}s"
            if p.fin_s is not None:
                meta_kwargs["end_offset"] = f"{p.fin_s:.3f}s"
            if p.fps is not None:
                # La API acepta hasta 24 cuadros por segundo.
                meta_kwargs["fps"] = float(min(24.0, max(0.01, p.fps)))
            partes.append(
                types.Part(
                    file_data=types.FileData(
                        file_uri=p.video_uri, mime_type=p.video_mime
                    ),
                    video_metadata=types.VideoMetadata(**meta_kwargs)
                    if meta_kwargs
                    else None,
                )
            )
        partes.append(types.Part(text=p.prompt))
        return partes

    def _config(self, p: Pedido) -> Any:
        from google.genai import types

        kwargs: dict[str, Any] = {"temperature": self.temperatura}
        # La resolución va a nivel del pedido (hay un solo video por pedido).
        # A nivel de la parte el SDK espera otro tipo de objeto y no todos los
        # modelos lo aceptan.
        if p.video_uri and p.resolucion in _RESOLUCIONES:
            kwargs["media_resolution"] = p.resolucion
        if p.timeout_s:
            kwargs["http_options"] = types.HttpOptions(timeout=int(p.timeout_s * 1000))
        if p.esquema:
            kwargs["response_mime_type"] = "application/json"
            kwargs["response_schema"] = p.esquema
        return types.GenerateContentConfig(**kwargs)

    # -- envío ------------------------------------------------------------

    def pedir(self, p: Pedido) -> Respuesta:
        # La caché va por nivel y no por modelo: si hoy contestó un modelo y
        # mañana le toca a otro del mismo grupo, lo ya hecho sirve igual.
        clave = p.clave_cache(f"nivel:{p.nivel}" if self.niveles else self.modelo)
        guardada = self.cache.leer(clave)
        if guardada is not None:
            self.contadores.pedidos += 1
            self.contadores.desde_cache += 1
            guardada.etiqueta = p.etiqueta
            return guardada

        nivel = p.nivel
        probados: set[str] = set()
        ultimo_error: Exception | None = None
        ultimo_tipo = ""
        modelos = self._candidatos(nivel)
        if not modelos:
            nivel, modelos = self._nivel_de_respaldo(nivel, None)

        while modelos:
            modelo = modelos.pop(0)
            if modelo in probados:
                continue
            probados.add(modelo)
            quedan_otros = bool(modelos)
            ritmo = self._ritmo_de(modelo, nivel)
            self.ritmo = ritmo
            saturado = 0

            for intento in range(self.max_reintentos + 1):
                ritmo.esperar()
                try:
                    self.cupos.anotar(modelo)
                    bruta = self.cliente.models.generate_content(
                        model=modelo,
                        contents=self._partes(p),
                        config=self._config(p),
                    )
                except Exception as exc:  # noqa: BLE001 - el SDK tira varios tipos
                    ultimo_error = exc
                    self.errores_vistos.append(
                        f"{modelo}: {type(exc).__name__}: {str(exc)[:600]}"
                    )
                    espera = self.esperas[min(intento, len(self.esperas) - 1)]

                    if _es_429(exc):
                        self.contadores.errores_429 += 1
                        demora = _demora_pedida(exc)
                        if _es_cupo_diario(exc, demora):
                            # Se acabó lo de hoy para este modelo: reintentar
                            # no sirve de nada. Se anota y se pasa al próximo.
                            ultimo_tipo = "diario"
                            self.cupos.marcar_agotado(modelo, demora)
                            self._avisar(
                                f"Se acabó el cupo gratis de hoy de {modelo}"
                                + (f" (se renueva en {_duracion(demora)})." if demora else ".")
                            )
                            break
                        ultimo_tipo = "cuota"
                        ritmo.bajar()
                        if intento < self.max_reintentos:
                            # Google suele decir cuánto esperar: se le hace caso.
                            if demora:
                                espera = min(demora + 1.0, 90.0)
                            self._avisar(
                                f"{modelo}: demasiados pedidos por minuto. "
                                f"Espero {espera:.0f} s y sigo "
                                f"({intento + 1}/{self.max_reintentos})."
                            )
                            self.contadores.reintentos += 1
                            time.sleep(espera)
                            continue
                        break  # probar el modelo siguiente

                    if _es_modelo_invalido(exc):
                        ultimo_tipo = "modelo"
                        self.cupos.marcar_agotado(modelo, 6 * 3600)
                        self._avisar(f"El modelo {modelo} no está disponible.")
                        break

                    if _es_de_conexion(exc):
                        raise ErrorGemini(
                            "No pude conectarme con Google "
                            f"({type(exc).__name__}: {exc}). Puede ser el wifi, un "
                            "antivirus o firewall que bloquea a Python, o que "
                            "Google tardó demasiado en contestar."
                        ) from exc

                    if _es_transitorio(exc):
                        ultimo_tipo = "saturado"
                        saturado += 1
                        self.contadores.saturaciones += 1
                        # Si hay otro modelo para probar, no se insiste mucho
                        # con uno que está saturado: se pasa al siguiente.
                        tope = 2 if quedan_otros else self.max_reintentos
                        if saturado <= tope and intento < self.max_reintentos:
                            self._avisar(
                                f"Google contestó que {modelo} está saturado. "
                                f"Espero {espera:.0f} s y pruebo de nuevo "
                                f"({saturado}/{tope})."
                            )
                            self.contadores.reintentos += 1
                            time.sleep(espera)
                            continue
                        break

                    raise ErrorGemini(f"Error de la API: {exc}") from exc

                if self._en_uso_por_nivel.get(nivel, modelo) != modelo or (
                    not self.niveles and modelo != self._modelo_en_uso
                ):
                    self._avisar(f"Sigo con el modelo {modelo}.")
                self._modelo_en_uso = modelo
                self._en_uso_por_nivel[nivel] = modelo
                ritmo.recuperar()
                r = self._a_respuesta(bruta, p, modelo)
                self.cache.guardar(clave, r)
                self.contadores.pedidos += 1
                if r.tokens_entrada:
                    self.contadores.tokens_entrada += r.tokens_entrada
                if r.tokens_salida:
                    self.contadores.tokens_salida += r.tokens_salida
                if p.inicio_s is not None and p.fin_s is not None:
                    self.contadores.segundos_video += max(0.0, p.fin_s - p.inicio_s)
                return r

            if not modelos and ultimo_tipo == "diario":
                # Se agotó todo el nivel: ¿hay un nivel de respaldo permitido?
                nivel, modelos = self._nivel_de_respaldo(nivel, probados)

        if ultimo_tipo == "diario" or (ultimo_error is None):
            raise self._error_cupo_diario(p.nivel)
        if ultimo_tipo == "saturado":
            raise ErrorGemini(
                "Google contestó que sus modelos están saturados y no pudo "
                f"atender el pedido ({ultimo_error}). No es un problema de tu "
                "cuenta ni de tu compu: suele pasar en horas pico del servicio "
                "gratis. Probá de nuevo en un rato; lo ya hecho queda guardado."
            )
        raise CuotaAgotada(
            "No pude completar el pedido con ningún modelo. "
            f"Último error: {ultimo_error}. Revisá la cuota en Google AI Studio."
        )

    def _nivel_de_respaldo(
        self, nivel: str, probados: set[str] | None
    ) -> tuple[str, list[str]]:
        """Si el nivel se agotó y hay permiso, sigue con el nivel de respaldo."""
        otro = self.respaldo.get(nivel)
        if not otro or otro == nivel:
            return nivel, []
        modelos = [m for m in self._candidatos(otro) if m not in (probados or set())]
        if modelos:
            self._avisar(
                f"Se acabó el cupo de hoy de los modelos de nivel '{nivel}'. "
                f"Sigo con los de nivel '{otro}', que son menos precisos."
            )
        return otro, modelos

    def _error_cupo_diario(self, nivel: str) -> "CupoDiarioAgotado":
        modelos = self.modelos_de(nivel)
        falta = self.cupos.proxima_renovacion(modelos)
        cuando = f" Se renueva en {_duracion(falta)}." if falta else ""
        return CupoDiarioAgotado(
            f"Se acabó el cupo gratis de hoy de los modelos de nivel '{nivel}' "
            f"({', '.join(modelos)}).{cuando} No hace falta reintentar ahora: "
            "todo lo analizado quedó guardado, y al volver a correr sigue "
            "desde donde quedó.",
            nivel=nivel,
            renueva_en_s=falta,
        )

    def _avisar(self, texto: str) -> None:
        """Cuenta en voz alta lo que está pasando mientras se espera.

        Sin esto, un reintento de dos minutos se ve igual que un programa
        colgado.
        """
        log.warning(texto)
        if self.al_avisar is not None:
            try:
                self.al_avisar(texto)
            except Exception:  # noqa: BLE001
                pass

    def _a_respuesta(self, bruta: Any, p: Pedido, modelo: str) -> Respuesta:
        texto = getattr(bruta, "text", None) or ""
        uso = getattr(bruta, "usage_metadata", None)
        datos = None
        if p.esquema:
            try:
                datos = extraer_json(texto)
            except ErrorGemini:
                datos = None
        return Respuesta(
            texto=texto,
            datos=datos,
            modelo=modelo,
            etiqueta=p.etiqueta,
            tokens_entrada=getattr(uso, "prompt_token_count", None) if uso else None,
            tokens_salida=getattr(uso, "candidates_token_count", None) if uso else None,
        )

    # -- archivos ---------------------------------------------------------

    def subir_archivo(self, ruta: Path | str, esperar: bool = True) -> str:
        """Sube un video por la Files API y devuelve su URI.

        Hace falta para archivos locales, y conviene también para videos
        largos de YouTube: al pasar una URL de YouTube con recorte, la API
        recorta los cuadros pero manda el audio entero en cada pedido.
        """
        ruta = Path(ruta)
        archivo = self.cliente.files.upload(file=str(ruta))
        if esperar:
            for _ in range(120):
                estado = getattr(getattr(archivo, "state", None), "name", str(
                    getattr(archivo, "state", "")
                ))
                if estado in ("ACTIVE", "SUCCEEDED"):
                    break
                if estado == "FAILED":
                    raise ErrorGemini(f"La subida de {ruta.name} falló.")
                time.sleep(5)
                archivo = self.cliente.files.get(name=archivo.name)
        return archivo.uri

    def verificar(self) -> dict[str, Any]:
        """Chequeo rápido de que la clave y el modelo andan."""
        r = self.pedir(
            Pedido(
                prompt="Respondé únicamente con este JSON: {\"ok\": true}",
                esquema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
                etiqueta="verificación",
                # Con un modelo liviano: no vale gastar del cupo chico en esto.
                nivel="liviano",
            )
        )
        return {
            "modelo": r.modelo or self.modelo,
            "respuesta": r.texto.strip()[:120],
            "desde_cache": r.desde_cache,
        }


def _texto_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}".lower()


def _es_429(exc: Exception) -> bool:
    t = _texto_error(exc)
    return "429" in t or "resource_exhausted" in t or "quota" in t or "rate limit" in t


def _es_modelo_invalido(exc: Exception) -> bool:
    t = _texto_error(exc)
    return ("404" in t and "model" in t) or "not found" in t or "is not supported" in t


def _es_transitorio(exc: Exception) -> bool:
    t = _texto_error(exc)
    if _es_de_conexion(exc):
        return False  # sin conexión no tiene sentido insistir cuatro veces
    return any(
        x in t
        for x in ("500", "502", "503", "504", "deadline", "unavailable", "overloaded",
                  "internal")
    )


def _es_de_conexion(exc: Exception) -> bool:
    # Si Google llegó a contestar con un código, no es la conexión.
    if isinstance(getattr(exc, "code", None), int):
        return False
    t = _texto_error(exc)
    return any(
        x in t
        for x in ("timeout", "timed out", "connecterror", "connection", "getaddrinfo",
                  "ssl", "certificate", "proxy", "network")
    )


# ---------------------------------------------------------------------------
# Modo simulado
# ---------------------------------------------------------------------------


@dataclass
class ClienteSimulado:
    """Devuelve respuestas con la forma correcta, sin tocar la API.

    Es para probar la base de datos, la cola de revisión y el Excel sin clave
    y sin gastar cuota. Las respuestas **no miden nada**: el pipeline marca
    todo lo que sale de acá como simulado, y la pantalla de precisión lo
    ignora.
    """

    modelo: str = "simulado"
    semilla: int = 18
    contadores: Contadores = field(default_factory=Contadores)
    respuestas: dict[str, Any] = field(default_factory=dict)
    _rnd: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._rnd = random.Random(self.semilla)
        self.cache = Cache(Path("data/cache"), activa=False)

    def pedir(self, p: Pedido) -> Respuesta:
        self.contadores.pedidos += 1
        if p.inicio_s is not None and p.fin_s is not None:
            self.contadores.segundos_video += max(0.0, p.fin_s - p.inicio_s)
        for marca, datos in self.respuestas.items():
            if marca in p.etiqueta or marca in p.prompt:
                return Respuesta(
                    texto=json.dumps(datos, ensure_ascii=False),
                    datos=datos,
                    simulada=True,
                    modelo=self.modelo,
                    etiqueta=p.etiqueta,
                )
        datos = self._inventar(p)
        return Respuesta(
            texto=json.dumps(datos, ensure_ascii=False),
            datos=datos,
            simulada=True,
            modelo=self.modelo,
            etiqueta=p.etiqueta,
        )

    def _inventar(self, p: Pedido) -> Any:
        """Respuesta sintética según la etiqueta del pedido."""
        etq = p.etiqueta or ""
        inicio = p.inicio_s or 0.0
        fin = p.fin_s if p.fin_s is not None else inicio + 60.0

        if etq.startswith("gruesa"):
            tramos = []
            t = inicio
            tipo = "juego"
            while t < fin:
                largo = self._rnd.uniform(20, 60)
                tramos.append(
                    {
                        "inicio_s": round(t, 1),
                        "fin_s": round(min(fin, t + largo), 1),
                        "tipo": tipo,
                        "reloj": _reloj(t),
                        "marcador_local": 8,
                        "marcador_visitante": 3,
                        "confianza": 0.9,
                    }
                )
                t += largo
                tipo = "muerto" if tipo == "juego" else "juego"
            return {"tramos": tramos}

        if etq.startswith("fichas"):
            return {
                "lecturas": [
                    {
                        "numero": self._rnd.choice([4, 9, 10, 12]),
                        "t_segundos": round(self._rnd.uniform(inicio, fin), 1),
                        "confianza": round(self._rnd.uniform(0.7, 0.98), 2),
                        "rasgos": {
                            "casco": self._rnd.choice(["no", "sí, oscuro"]),
                            "color_botines": self._rnd.choice(
                                ["blancos", "amarillos", "rojos", "oscuros"]
                            ),
                            "pelo": self._rnd.choice(["oscuro", "claro", "corto"]),
                            "contextura": self._rnd.choice(["flaco", "robusto"]),
                        },
                        "nota": "simulado",
                    }
                ]
            }

        if etq.startswith("eventos"):
            acciones = [
                ("Tackle", "positivo"),
                ("Carry", "avanza"),
                ("Pase", "bueno"),
                ("Ruck limpiado", "limpio"),
                ("Pelota perdida", "knock-on"),
            ]
            eventos = []
            t = inicio + 3
            while t < fin - 2:
                accion, resultado = self._rnd.choice(acciones)
                eventos.append(
                    {
                        "t_segundos": round(t, 1),
                        "accion": accion,
                        "resultado": resultado,
                        "equipo": "propio" if self._rnd.random() < 0.55 else "rival",
                        "numero": self._rnd.choice([1, 4, 8, 9, 10, 12, 13, 15]),
                        "metros": round(self._rnd.uniform(0, 12), 1)
                        if accion == "Carry"
                        else None,
                        "confianza": round(self._rnd.uniform(0.5, 0.97), 2),
                        "nota": "simulado",
                    }
                )
                t += self._rnd.uniform(8, 25)
            return {"eventos": eventos}

        if etq.startswith("contacto"):
            return {
                "tackleadores": [self._rnd.choice([4, 6, 7, 8])],
                "portador": self._rnd.choice([10, 12, 13]),
                "portador_avanza": self._rnd.random() < 0.4,
                "pelota_adelante": False,
                "confianza": round(self._rnd.uniform(0.6, 0.95), 2),
                "nota": "simulado",
            }

        if etq.startswith("relato"):
            return {
                "fragmentos": [
                    {
                        "inicio_s": round(inicio, 1),
                        "fin_s": round(fin, 1),
                        "texto": "(transcripción simulada)",
                        "jugadores_nombrados": [],
                    }
                ]
            }

        if etq.startswith("cambios"):
            return {"cambios": []}

        return {"ok": True, "simulado": True}

    def subir_archivo(self, ruta: Path | str, esperar: bool = True) -> str:
        return f"simulado://{Path(ruta).name}"

    def verificar(self) -> dict[str, Any]:
        return {"modelo": "simulado", "respuesta": '{"ok": true}', "desde_cache": False}


def _reloj(segundos: float) -> str:
    m, s = divmod(int(segundos), 60)
    return f"{m:02d}:{s:02d}"


def crear_cliente(cfg: Any, forzar_simulado: bool = False) -> Any:
    """Devuelve el cliente real del proveedor elegido, o el simulado."""
    if forzar_simulado or not cfg.hay_api_key:
        if not forzar_simulado:
            log.warning(
                "No hay %s: uso el modo simulado. "
                "Las stats que salgan de acá no miden nada.",
                cfg.nombre_variable_clave,
            )
        return ClienteSimulado()
    if cfg.proveedor == "claude":
        from ..proveedor_claude import ClienteClaude

        return ClienteClaude(
            api_key=cfg.api_key,
            niveles=cfg.niveles_claude,
            precios=cfg.precios_claude,
            esfuerzo=cfg.esfuerzo_claude,
            dir_cache=cfg.dir_cache,
            usar_cache=cfg.usar_cache,
            dir_cuadros=cfg.dir_video.parent / "cuadros",
            max_cuadros=cfg.max_cuadros_por_pedido,
            tope_usd=cfg.tope_usd_por_partido,
            max_reintentos=cfg.max_reintentos,
            esperas=cfg.esperas_429,
        )
    return ClienteGemini(
        api_key=cfg.api_key,
        modelo=cfg.modelo,
        alternativas=cfg.modelos_alternativos,
        rpm=cfg.rpm_inicial,
        esperas_429=cfg.esperas_429,
        max_reintentos=cfg.max_reintentos,
        dir_cache=cfg.dir_cache,
        usar_cache=cfg.usar_cache,
        temperatura=cfg.temperatura,
        niveles=cfg.niveles,
        rpm_por_nivel=cfg.rpm_por_nivel,
        respaldo=cfg.respaldo_de_niveles,
        archivo_cupos=cfg.archivo_cupos,
    )
