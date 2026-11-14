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

    def esperar(self, dormir: Callable[[float], None] = time.sleep) -> None:
        ahora = time.monotonic()
        falta = self._ultimo + self.intervalo_s - ahora
        if falta > 0:
            dormir(falta)
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
    tokens_entrada: int = 0
    tokens_salida: int = 0
    segundos_video: float = 0.0

    def resumen(self) -> str:
        return (
            f"{self.pedidos} pedidos ({self.desde_cache} de caché), "
            f"{self.reintentos} reintentos ({self.errores_429} por cuota, "
            f"{self.saturaciones} por saturación), "
            f"{self.tokens_entrada:,} tokens de entrada"
        )


class ClienteGemini:
    """Habla con la API de Gemini."""

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
        timeout_s: float = 240.0,
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
        self.temperatura = temperatura
        self.ritmo = _Ritmo(rpm)
        self.esperas = list(esperas_429 or [5, 15, 45, 120])
        self.max_reintentos = max_reintentos
        self.cache = Cache(Path(dir_cache), usar_cache)
        self.contadores = Contadores()
        self._modelo_en_uso = modelo
        #: Función opcional que recibe los avisos (reintentos, cambios de
        #: modelo) para mostrarlos en pantalla.
        self.al_avisar: Callable[[str], None] | None = None
        #: Los errores que fue devolviendo Google, para el diagnóstico.
        self.errores_vistos: list[str] = []

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
        clave = p.clave_cache(self.modelo)
        guardada = self.cache.leer(clave)
        if guardada is not None:
            self.contadores.pedidos += 1
            self.contadores.desde_cache += 1
            guardada.etiqueta = p.etiqueta
            return guardada

        modelos = [self._modelo_en_uso] + [
            m for m in self.alternativas if m != self._modelo_en_uso
        ]
        ultimo_error: Exception | None = None
        ultimo_tipo = ""

        for n_modelo, modelo in enumerate(modelos):
            quedan_otros = n_modelo < len(modelos) - 1
            saturado = 0
            for intento in range(self.max_reintentos + 1):
                self.ritmo.esperar()
                try:
                    bruta = self.cliente.models.generate_content(
                        model=modelo,
                        contents=self._partes(p),
                        config=self._config(p),
                    )
                except Exception as exc:  # noqa: BLE001 - el SDK tira varios tipos
                    ultimo_error = exc
                    self.errores_vistos.append(
                        f"{modelo}: {type(exc).__name__}: {str(exc)[:300]}"
                    )
                    espera = self.esperas[min(intento, len(self.esperas) - 1)]

                    if _es_429(exc):
                        ultimo_tipo = "cuota"
                        self.contadores.errores_429 += 1
                        self.ritmo.bajar()
                        if intento < self.max_reintentos:
                            self._avisar(
                                f"Google dice que se pasó el límite gratis ({modelo}). "
                                f"Espero {espera:.0f} s y pruebo de nuevo "
                                f"({intento + 1}/{self.max_reintentos})."
                            )
                            self.contadores.reintentos += 1
                            time.sleep(espera)
                            continue
                        break  # probar el modelo siguiente

                    if _es_modelo_invalido(exc):
                        ultimo_tipo = "modelo"
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
                        if quedan_otros:
                            self._avisar(
                                f"{modelo} sigue saturado: pruebo con {modelos[n_modelo + 1]}."
                            )
                        break

                    raise ErrorGemini(f"Error de la API: {exc}") from exc

                if modelo != self._modelo_en_uso:
                    self._avisar(f"Sigo con el modelo {modelo}.")
                self._modelo_en_uso = modelo
                self.ritmo.recuperar()
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
    """Devuelve el cliente real si hay clave, o el simulado si no."""
    if forzar_simulado or not cfg.hay_api_key:
        if not forzar_simulado:
            log.warning(
                "No hay GEMINI_API_KEY: uso el modo simulado. "
                "Las stats que salgan de acá no miden nada."
            )
        return ClienteSimulado()
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
    )
