"""Cliente de la API de Claude.

Hace lo mismo que el cliente de Gemini —recibe un ``Pedido`` y devuelve una
``Respuesta``— así que el resto del programa no se entera de quién mira.

La diferencia es cómo mira. Claude no recibe video ni audio: recibe imágenes.
Para cada pedido:

1. Se sacan cuadros del tramo pedido, a los cuadros por segundo y al ancho que
   diga la configuración (``cuadros.extraer``).
2. Se le manda cada cuadro con su segundo exacto en el video al lado.
3. Si hay fichas visuales, van antes: el recorte de cada jugador en un cuadro
   donde se le leyó el número, para que compare imagen contra imagen.
4. Se pide la respuesta en JSON con el esquema del pedido.

Y lleva la cuenta de la plata. Antes de cada pedido estima cuánto va a costar
y, si con eso se pasa del tope por partido, no lo manda: frena con un aviso.
Es plata de verdad (en un plan Enterprise, cada token se factura), así que el
tope no es opcional por defecto.
"""

from __future__ import annotations

import base64
import copy
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import cuadros as cuadros_mod
from .gemini.cliente import (
    Cache,
    Contadores,
    CuotaAgotada,
    ErrorGemini,
    Pedido,
    Respuesta,
    extraer_json,
)

log = logging.getLogger(__name__)

#: Ancho de los cuadros cuando la velocidad no dice uno.
ANCHO_POR_RESOLUCION = {
    "MEDIA_RESOLUTION_LOW": 640,
    "MEDIA_RESOLUTION_MEDIUM": 768,
    "MEDIA_RESOLUTION_HIGH": 1008,
}

#: Tokens de salida que se suponen para estimar el costo de un pedido.
SALIDA_ESTIMADA = 1500


class TopeDeGastoAlcanzado(ErrorGemini):
    """El próximo pedido haría pasar el gasto del partido por encima del tope."""

    def __init__(self, mensaje: str, gastado_usd: float, tope_usd: float):
        super().__init__(mensaje)
        self.gastado_usd = gastado_usd
        self.tope_usd = tope_usd


class SinSaldo(ErrorGemini):
    """La cuenta de Anthropic se quedó sin crédito."""


@dataclass
class UsoModelo:
    pedidos: int = 0
    entrada: int = 0
    salida: int = 0
    cache_lectura: int = 0
    cache_escritura: int = 0
    usd: float = 0.0


def esquema_para_claude(esquema: dict[str, Any]) -> dict[str, Any]:
    """Pasa un esquema del estilo de Gemini a JSON Schema estándar.

    - ``"nullable": true`` pasa a ``"type": [tipo, "null"]``.
    - Todo objeto lleva ``additionalProperties: false``, que es lo que pide la
      salida estructurada de Claude.
    """

    def convertir(nodo: Any) -> Any:
        if isinstance(nodo, list):
            return [convertir(x) for x in nodo]
        if not isinstance(nodo, dict):
            return nodo
        n = {k: convertir(v) for k, v in nodo.items() if k != "nullable"}
        if nodo.get("nullable") and isinstance(n.get("type"), str):
            n["type"] = [n["type"], "null"]
            if "enum" in n:
                n["enum"] = list(n["enum"]) + [None]
        if n.get("type") == "object":
            n.setdefault("properties", {})
            n["additionalProperties"] = False
        return n

    return convertir(copy.deepcopy(esquema))


def _b64(ruta: Path | str) -> str:
    return base64.standard_b64encode(Path(ruta).read_bytes()).decode("ascii")


def _bloque_imagen(ruta: Path | str) -> dict[str, Any]:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": _b64(ruta)},
    }


class ClienteClaude:
    """Habla con la API de Claude (Anthropic)."""

    #: El video tiene que estar en disco para sacarle cuadros.
    necesita_video_local = True

    def __init__(
        self,
        api_key: str,
        niveles: dict[str, list[str]],
        precios: dict[str, dict[str, float]] | None = None,
        esfuerzo: str = "low",
        dir_cache: Path | str = "data/cache",
        usar_cache: bool = True,
        dir_cuadros: Path | str = "data/cuadros",
        max_cuadros: int = 80,
        tope_usd: float = 10.0,
        max_reintentos: int = 4,
        esperas: list[float] | None = None,
        timeout_s: float = 600.0,
        max_tokens: int = 8000,
        http_client: Any = None,
    ):
        import anthropic

        self._anthropic = anthropic
        opciones: dict[str, Any] = {"api_key": api_key, "max_retries": 0, "timeout": timeout_s}
        if http_client is not None:
            opciones["http_client"] = http_client
        self.cliente = anthropic.Anthropic(**opciones)
        self.niveles = {k: list(v) for k, v in niveles.items() if v}
        if not self.niveles:
            raise ValueError("No hay ningún modelo de Claude configurado.")
        self.niveles.setdefault("fuerte", next(iter(self.niveles.values())))
        self.niveles.setdefault("liviano", self.niveles["fuerte"])
        self.precios = precios or {}
        self.esfuerzo = esfuerzo
        self.cache = Cache(Path(dir_cache), usar_cache)
        self.dir_cuadros = Path(dir_cuadros)
        self.max_cuadros = max_cuadros
        self.max_reintentos = max_reintentos
        self.esperas = list(esperas or [5, 20, 60, 120])
        #: Cuántas veces se espera ante "vas muy rápido" (429) o "estoy
        #: saturado" (529). Las cuentas nuevas tienen límites por minuto
        #: bajos: es normal chocar seguido, y no tiene que cortar el análisis.
        self.max_reintentos_ritmo = max(self.max_reintentos, 15)
        self.max_tokens = max_tokens
        self.contadores = Contadores()
        self.al_avisar: Callable[[str], None] | None = None
        self.errores_vistos: list[str] = []
        # Plata
        self.tope_usd = float(tope_usd or 0)
        self.gasto_previo_usd = 0.0
        self.gasto_usd = 0.0
        self.uso: dict[str, UsoModelo] = {}
        # Videos en disco: ruta -> en qué segundo del partido arranca el archivo
        self._desfases: dict[str, float] = {}
        # Si la API rechaza algo de lo que se le pide, se deja de pedir.
        self._sin_formato = False
        self._sin_esfuerzo = False
        self._modelo_en_uso: dict[str, str] = {}
        #: Cada pedido que salió, con lo que costó y lo que contestó. Lo usa la
        #: prueba real para dejar un informe que se pueda revisar.
        self.registro: list[dict[str, Any]] = []

    # -- lo que el resto del programa espera de un cliente ------------------

    @property
    def modelo(self) -> str:
        return self.niveles["fuerte"][0]

    def modelos_de(self, nivel: str) -> list[str]:
        return list(self.niveles.get(nivel) or self.niveles["fuerte"])

    def subir_archivo(self, ruta: Path | str, esperar: bool = True) -> str:
        """Claude no necesita subir el video: se usa el archivo en disco."""
        return str(ruta)

    def registrar_video(self, ruta: Path | str, desfase_s: float = 0.0) -> None:
        """Avisa en qué segundo del partido arranca un archivo bajado a pedazos."""
        self._desfases[str(ruta)] = float(desfase_s or 0.0)

    def empezar_partido(self, gasto_previo_usd: float, tope_usd: float | None = None) -> None:
        """Lo llama el pipeline antes de arrancar: cuánto se gastó ya en el partido."""
        self.gasto_previo_usd = float(gasto_previo_usd or 0.0)
        self.gasto_usd = 0.0
        self.uso = {}
        if tope_usd is not None:
            self.tope_usd = float(tope_usd or 0)

    def resumen_gasto(self) -> dict[str, Any]:
        return {
            "usd": round(self.gasto_usd, 6),
            "por_modelo": {
                m: {
                    "pedidos": u.pedidos,
                    "entrada": u.entrada,
                    "salida": u.salida,
                    "cache_lectura": u.cache_lectura,
                    "cache_escritura": u.cache_escritura,
                    "usd": round(u.usd, 6),
                }
                for m, u in self.uso.items()
            },
        }

    # -- costos -----------------------------------------------------------

    def _precio(self, modelo: str) -> dict[str, float]:
        if modelo in self.precios:
            return self.precios[modelo]
        # Modelo sin precio cargado: se supone el más caro conocido, para que
        # el tope nunca quede corto.
        if self.precios:
            return max(self.precios.values(), key=lambda p: p.get("entrada", 0))
        return {"entrada": 5.0, "salida": 25.0, "cache_lectura": 0.5, "cache_escritura": 6.25}

    def costo(self, modelo: str, entrada: int, salida: int, cache_lectura: int = 0,
              cache_escritura: int = 0) -> float:
        p = self._precio(modelo)
        return (
            entrada * p.get("entrada", 0)
            + salida * p.get("salida", 0)
            + cache_lectura * p.get("cache_lectura", p.get("entrada", 0) * 0.1)
            + cache_escritura * p.get("cache_escritura", p.get("entrada", 0) * 1.25)
        ) / 1_000_000

    def _controlar_tope(self, modelo: str, tokens_entrada_est: int) -> None:
        if not self.tope_usd or self.tope_usd <= 0:
            return
        estimado = self.costo(modelo, tokens_entrada_est, SALIDA_ESTIMADA)
        total = self.gasto_previo_usd + self.gasto_usd
        if total + estimado > self.tope_usd:
            raise TopeDeGastoAlcanzado(
                f"Se llegó al tope de gasto de esta corrida: van US$ {total:.2f} "
                f"de US$ {self.tope_usd:.2f}, y el próximo pedido costaría unos "
                f"US$ {estimado:.2f}. No se mandó. Lo analizado hasta acá quedó "
                "guardado. Si querés seguir, subí 'tope_usd_por_partido' en "
                "config.yaml y volvé a correr: sigue desde donde quedó.",
                gastado_usd=total,
                tope_usd=self.tope_usd,
            )

    # -- armado del pedido ------------------------------------------------

    def _cuadros(self, p: Pedido) -> list[cuadros_mod.Cuadro]:
        if not p.video_uri or p.inicio_s is None or p.fin_s is None:
            return []
        ruta = Path(p.video_uri)
        if not ruta.exists():
            raise ErrorGemini(
                f"No encuentro el video en {ruta}. Con Claude el video tiene que "
                "estar bajado: volvé a correr el análisis y se baja solo."
            )
        ancho = int(getattr(p, "ancho", 0) or 0) or ANCHO_POR_RESOLUCION.get(p.resolucion, 640)
        desfase = self._desfases.get(str(ruta), 0.0)
        zoom = getattr(p, "zoom", None) or {}
        pista = []
        for punto in zoom.get("pista") or []:
            try:
                t, caja = float(punto[0]), cuadros_mod.caja_valida(punto[1])
            except (TypeError, ValueError, IndexError):
                continue
            if caja:
                pista.append((t, caja))
        if not pista:
            return cuadros_mod.extraer(
                ruta, float(p.inicio_s), float(p.fin_s), float(p.fps or 1.0), ancho,
                self.dir_cuadros, desfase, self.max_cuadros,
            )
        # Zoom: unos pocos cuadros de la vista general para el contexto, y el
        # resto recortados alrededor del contacto.
        ctx_fps = float(zoom.get("contexto_fps") or 0)
        generales: list[cuadros_mod.Cuadro] = []
        if ctx_fps > 0:
            generales = cuadros_mod.extraer(
                ruta, float(p.inicio_s), float(p.fin_s), ctx_fps,
                int(zoom.get("contexto_ancho") or 512), self.dir_cuadros, desfase,
                max(1, self.max_cuadros // 6),
            )
        cupo = max(1, self.max_cuadros - len(generales))
        ampliados = cuadros_mod.extraer_zoom(
            ruta, float(p.inicio_s), float(p.fin_s), float(p.fps or 1.0), pista, ancho,
            self.dir_cuadros, desfase, cupo,
            ancho_fuente=int(zoom.get("ancho_fuente") or 1920),
            escala=float(zoom.get("escala") or 2.2),
            minimo=float(zoom.get("minimo") or 0.3),
        )
        for c in generales:
            c.tipo = "vista general"  # type: ignore[attr-defined]
        for c in ampliados:
            c.tipo = "zoom al contacto"  # type: ignore[attr-defined]
        return sorted(generales + ampliados, key=lambda c: (c.t, getattr(c, "tipo", "")))

    def _contenido(self, p: Pedido, lista: list[cuadros_mod.Cuadro]) -> tuple[list[dict], int]:
        """Arma los bloques del mensaje y estima los tokens de entrada."""
        bloques: list[dict[str, Any]] = []
        tokens = 0
        texto = p.prompt
        prefijo = getattr(p, "prefijo", "") or ""
        if prefijo and texto.startswith(prefijo) and len(texto) > len(prefijo):
            # Lo fijo primero y marcado para la caché; lo de este pedido, al final.
            bloques.append({"type": "text", "text": prefijo,
                            "cache_control": {"type": "ephemeral"}})
            tokens += len(prefijo) // 3 + 20
            texto = texto[len(prefijo):].strip()
        referencias = [r for r in (getattr(p, "referencias", None) or []) if Path(r[1]).exists()]
        if referencias:
            bloques.append({
                "type": "text",
                "text": getattr(p, "titulo_referencias", "") or (
                    "FICHAS VISUALES. Así se ve cada jugador del equipo propio: "
                    "cada fila es un jugador, con su número y nombre arriba. Las "
                    "fotos marcadas HOY (o sin marca) son de ESTE partido, "
                    "recortadas donde se le leyó el número: valen para casco, "
                    "vincha, vendas, botines, pelo y contextura. Las marcadas "
                    "ANTES son de otros partidos o del álbum del club: ahí puede "
                    "tener otro número, otros botines u otra ropa, así que usalas "
                    "para la cara, el pelo, la contextura y la forma de correr."
                ),
            })
            for etiqueta, ruta in referencias:
                bloques.append({"type": "text", "text": f"Ficha: {etiqueta}"})
                bloques.append(_bloque_imagen(ruta))
                w, h = cuadros_mod._medidas(Path(ruta), 0)
                tokens += cuadros_mod.tokens_imagen(w, h) + 10
            # Las fichas son iguales en todos los pedidos del partido: se marcan
            # para que Claude las guarde en caché y se cobren al 10%.
            bloques[-1]["cache_control"] = {"type": "ephemeral"}
        if lista:
            bloques.append({
                "type": "text",
                "text": (
                    f"CUADROS DEL VIDEO ({len(lista)}), en orden. Antes de cada "
                    "uno va su segundo exacto en el video completo: usá esos "
                    "números para los tiempos de tu respuesta."
                ),
            })
            if any(getattr(c, "tipo", "") for c in lista):
                bloques[-1]["text"] += (
                    " Hay dos clases: 'vista general' es la cancha entera, para "
                    "ubicarte; 'zoom al contacto' es la misma escena recortada y "
                    "ampliada alrededor del contacto, para reconocer a los "
                    "jugadores."
                )
            for c in lista:
                tipo = getattr(c, "tipo", "")
                bloques.append({"type": "text", "text": f"t={c.t:.1f} s" + (f" · {tipo}" if tipo else "")})
                bloques.append(_bloque_imagen(c.ruta))
                tokens += c.tokens + 8
        if p.esquema and self._sin_formato:
            texto += (
                "\n\nRespondé únicamente con un JSON que cumpla este esquema, sin "
                f"texto alrededor:\n{json.dumps(p.esquema, ensure_ascii=False)}"
            )
        bloques.append({"type": "text", "text": texto})
        tokens += len(texto) // 3 + 50
        if p.esquema:
            tokens += len(json.dumps(p.esquema)) // 3
        return bloques, tokens

    def _parametros(self, p: Pedido) -> dict[str, Any]:
        config: dict[str, Any] = {}
        if self.esfuerzo and not self._sin_esfuerzo:
            config["effort"] = self.esfuerzo
        if p.esquema and not self._sin_formato:
            config["format"] = {"type": "json_schema", "schema": esquema_para_claude(p.esquema)}
        return {"output_config": config} if config else {}

    # -- envío ------------------------------------------------------------

    def pedir(self, p: Pedido) -> Respuesta:
        clave = p.clave_cache(f"claude|{p.nivel}")
        guardada = self.cache.leer(clave)
        if guardada is not None:
            self.contadores.pedidos += 1
            self.contadores.desde_cache += 1
            guardada.etiqueta = p.etiqueta
            return guardada

        lista = self._cuadros(p)
        modelos = self.modelos_de(p.nivel)
        vigente = self._modelo_en_uso.get(p.nivel)
        if vigente in modelos:
            modelos.remove(vigente)
            modelos.insert(0, vigente)

        ultimo_error: Exception | None = None
        for modelo in modelos:
            reintento_sin_algo = False
            intento = 0
            while intento <= max(self.max_reintentos, self.max_reintentos_ritmo):
                bloques, tokens_est = self._contenido(p, lista)
                self._controlar_tope(modelo, tokens_est)
                try:
                    msg = self.cliente.messages.create(
                        model=modelo,
                        max_tokens=self.max_tokens,
                        messages=[{"role": "user", "content": bloques}],
                        **self._parametros(p),
                    )
                except Exception as exc:  # noqa: BLE001 - se clasifica abajo
                    ultimo_error = exc
                    self.errores_vistos.append(f"{modelo}: {type(exc).__name__}: {str(exc)[:500]}")
                    tipo = self._clasificar(exc)
                    ritmo = getattr(exc, "status_code", None) in (429, 529)
                    tope = self.max_reintentos_ritmo if ritmo else self.max_reintentos
                    if tipo == "reintentar" and intento < tope:
                        espera = self._espera(exc, intento)
                        self._avisar(
                            f"Claude está ocupado o se pasó el ritmo ({modelo}). "
                            f"Espero {espera:.0f} s y sigo ({intento + 1}/{tope})."
                        )
                        self.contadores.reintentos += 1
                        if getattr(exc, "status_code", None) == 429:
                            self.contadores.errores_429 += 1
                        else:
                            self.contadores.saturaciones += 1
                        time.sleep(espera)
                        intento += 1
                        continue
                    if tipo == "sin_formato" and not reintento_sin_algo:
                        self._avisar("La API no aceptó el formato pedido: sigo sin él.")
                        self._sin_formato = True
                        reintento_sin_algo = True
                        continue
                    if tipo == "sin_esfuerzo" and not reintento_sin_algo:
                        self._sin_esfuerzo = True
                        reintento_sin_algo = True
                        continue
                    if tipo == "otro_modelo":
                        self._avisar(f"El modelo {modelo} no está disponible.")
                        break
                    raise self._error_claro(exc) from exc

                self._modelo_en_uso[p.nivel] = modelo
                return self._registrar(msg, p, modelo, clave)

        if ultimo_error is not None:
            raise ErrorGemini(
                "No pude completar el pedido con ningún modelo de Claude. "
                f"Último error: {ultimo_error}"
            )
        raise ErrorGemini("No hay modelos de Claude configurados para este pedido.")

    def _registrar(self, msg: Any, p: Pedido, modelo: str, clave: str) -> Respuesta:
        texto = "".join(
            getattr(b, "text", "") for b in (msg.content or []) if getattr(b, "type", "") == "text"
        )
        u = msg.usage
        entrada = int(getattr(u, "input_tokens", 0) or 0)
        salida = int(getattr(u, "output_tokens", 0) or 0)
        c_lect = int(getattr(u, "cache_read_input_tokens", 0) or 0)
        c_escr = int(getattr(u, "cache_creation_input_tokens", 0) or 0)
        usd = self.costo(modelo, entrada, salida, c_lect, c_escr)

        self.gasto_usd += usd
        uso = self.uso.setdefault(modelo, UsoModelo())
        uso.pedidos += 1
        uso.entrada += entrada
        uso.salida += salida
        uso.cache_lectura += c_lect
        uso.cache_escritura += c_escr
        uso.usd += usd
        self.contadores.pedidos += 1
        self.contadores.tokens_entrada += entrada + c_lect + c_escr
        self.contadores.tokens_salida += salida
        self.contadores.costo_usd += usd
        if p.inicio_s is not None and p.fin_s is not None:
            self.contadores.segundos_video += max(0.0, p.fin_s - p.inicio_s)

        if getattr(msg, "stop_reason", "") == "max_tokens":
            log.warning("La respuesta de %s quedó cortada por largo.", p.etiqueta)

        datos = None
        if p.esquema:
            try:
                datos = extraer_json(texto)
            except ErrorGemini:
                datos = None
        r = Respuesta(
            texto=texto,
            datos=datos,
            modelo=modelo,
            etiqueta=p.etiqueta,
            tokens_entrada=entrada + c_lect + c_escr,
            tokens_salida=salida,
        )
        r.costo_usd = usd  # type: ignore[attr-defined]
        self.registro.append({
            "etiqueta": p.etiqueta,
            "modelo": modelo,
            "nivel": p.nivel,
            "inicio_s": p.inicio_s,
            "fin_s": p.fin_s,
            "entrada": entrada,
            "salida": salida,
            "cache_lectura": c_lect,
            "cache_escritura": c_escr,
            "usd": round(usd, 6),
            "cortada": getattr(msg, "stop_reason", "") == "max_tokens",
            "json_ok": datos is not None if p.esquema else None,
            "respuesta": texto[:6000],
        })
        # Solo se guarda en caché lo que se pudo leer: una respuesta cortada o
        # rota no tiene que quedar "contestada" para siempre.
        if not p.esquema or datos is not None:
            self.cache.guardar(clave, r)
        return r

    # -- errores ----------------------------------------------------------

    def _clasificar(self, exc: Exception) -> str:
        a = self._anthropic
        codigo = getattr(exc, "status_code", None)
        texto = str(exc).lower()
        if isinstance(exc, (a.APIConnectionError, a.APITimeoutError)):
            return "reintentar"
        if codigo in (408, 409, 429, 500, 502, 503, 504, 529):
            return "reintentar"
        if codigo == 404 or isinstance(exc, a.NotFoundError):
            return "otro_modelo"
        if codigo == 400:
            if "credit" in texto or "balance" in texto or "billing" in texto:
                return "sin_saldo"
            if not self._sin_formato and any(
                k in texto for k in ("output_config", "json_schema", "schema")
            ):
                return "sin_formato"
            if not self._sin_esfuerzo and "effort" in texto:
                return "sin_esfuerzo"
        return "fatal"

    def _espera(self, exc: Exception, intento: int) -> float:
        respuesta = getattr(exc, "response", None)
        encabezados = getattr(respuesta, "headers", None) or {}
        try:
            pedido = float(encabezados.get("retry-after", ""))
            if pedido > 0:
                return min(pedido + 1.0, 180.0)
        except (TypeError, ValueError):
            pass
        if getattr(exc, "status_code", None) in (429, 529) and intento >= len(self.esperas):
            return 120.0
        return self.esperas[min(intento, len(self.esperas) - 1)]

    def _error_claro(self, exc: Exception) -> ErrorGemini:
        a = self._anthropic
        codigo = getattr(exc, "status_code", None)
        texto = str(exc)
        if codigo == 401 or isinstance(exc, a.AuthenticationError):
            return ErrorGemini(
                "Anthropic rechazó la clave (ANTHROPIC_API_KEY). Revisá que esté "
                "bien copiada en el archivo .env, sin espacios ni comillas."
            )
        if codigo == 403 or isinstance(exc, a.PermissionDeniedError):
            return ErrorGemini(
                "La clave no tiene permiso para usar este modelo o esta "
                f"organización. Lo que dijo Anthropic: {texto[:300]}"
            )
        if self._clasificar(exc) == "sin_saldo":
            return SinSaldo(
                "La cuenta de Anthropic no tiene saldo para este pedido. Hay que "
                "cargar crédito (o pedirle al administrador de la cuenta). Lo "
                "hecho hasta acá quedó guardado."
            )
        if isinstance(exc, (a.APIConnectionError, a.APITimeoutError)):
            return ErrorGemini(
                "No pude conectarme con Anthropic. Puede ser el wifi, o un "
                f"antivirus o firewall que bloquea a Python. ({texto[:200]})"
            )
        if codigo == 429:
            return CuotaAgotada(
                "Anthropic sigue diciendo que se pasó el límite de pedidos por "
                "minuto de la cuenta. Esperá un rato y volvé a correr: sigue "
                "desde donde quedó."
            )
        return ErrorGemini(f"Error de la API de Claude: {texto[:500]}")

    def _avisar(self, texto: str) -> None:
        log.warning(texto)
        if self.al_avisar is not None:
            try:
                self.al_avisar(texto)
            except Exception:  # noqa: BLE001
                pass

    # -- verificación -----------------------------------------------------

    def verificar(self) -> dict[str, Any]:
        r = self.pedir(
            Pedido(
                prompt='Respondé únicamente con este JSON: {"ok": true}',
                esquema={"type": "object", "properties": {"ok": {"type": "boolean"}},
                         "required": ["ok"]},
                etiqueta="verificación",
                nivel="liviano",
            )
        )
        return {
            "modelo": r.modelo,
            "respuesta": r.texto.strip()[:120],
            "desde_cache": r.desde_cache,
            "costo_usd": getattr(r, "costo_usd", 0.0),
        }

