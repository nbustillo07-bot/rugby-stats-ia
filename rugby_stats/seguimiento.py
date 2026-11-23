"""Seguimiento de jugadores: quién es quién durante todo el tramo.

El número de la camiseta casi nunca se lee en el momento del tackle (el
jugador está lejos, de costado o en una pila). Pero el mismo jugador pasa
muchas veces por momentos en que sí se lee: caminando de espaldas, en un
line, levantándose cerca de la cámara. La idea:

1. Un detector de personas (YOLO, corre gratis en la compu) encuentra a
   todos en cada cuadro y los sigue de un cuadro al siguiente.
2. Por los colores de la camiseta y el short se separa a los del propio, los
   del rival y los demás (árbitro, público). Claude solo mira una hoja de
   muestras y dice qué grupo es cuál.
3. Cada recorrido se corta donde se cruza con otro jugador (ahí el
   seguimiento puede confundir a dos) y queda en "tramitos" limpios.
4. Claude mira las mejores fotos de cada tramito: lee el número si se ve y
   describe al jugador (botines, vincha, cintas, pelo, contextura...).
5. Con eso se arman las fichas de cada jugador del plantel.
6. En cada contacto se buscan los tramitos del propio que estaban ahí, y se
   le pasan al que decide el tackle con su número (si se leyó) o su
   descripción, junto con las fichas de todos.

Todo lo pesado (detectar y seguir) no le cuesta nada a la API. A Claude le
llegan pocas hojas de fotos.
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

RASGOS_SEG = ("botines", "vincha_casco", "cintas", "pelo", "contextura", "medias", "otro")


class SinSeguimiento(RuntimeError):
    """No se puede seguir jugadores en esta compu (falta el paquete)."""


@dataclass
class Det:
    t: float          # segundos del partido (no del archivo)
    pista: int        # id del seguimiento crudo
    caja: tuple[int, int, int, int]
    alto_cuadro: int = 1080
    ancho_cuadro: int = 1920
    grupo: int = -1   # grupo de colores
    equipo: str = ""  # "propio" | "rival" | "otro"
    tramo: int = -1   # tramito limpio


@dataclass
class Tramito:
    id: int
    equipo: str
    desde: float
    hasta: float
    n: int
    numero: int | None = None
    confianza_numero: float = 0.0
    descripcion: dict[str, str] = field(default_factory=dict)
    fotos: list[str] = field(default_factory=list)
    # Quién es, al final (por número o por parecido), y con qué seguridad.
    jugador: int | None = None
    seguridad: float = 0.0
    como: str = ""


@dataclass
class Seguimiento:
    dets: list[Det]
    tramitos: dict[int, Tramito]
    ancho: int = 1920
    alto: int = 1080

    # -- guardar y cargar ---------------------------------------------------
    def guardar(self, ruta: Path) -> None:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        datos = {
            "ancho": self.ancho, "alto": self.alto,
            "dets": [[d.t, d.pista, *d.caja, d.grupo, d.equipo, d.tramo] for d in self.dets],
            "tramitos": [asdict(t) for t in self.tramitos.values()],
        }
        ruta.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def cargar(cls, ruta: Path) -> "Seguimiento | None":
        if not ruta.exists():
            return None
        try:
            d = json.loads(ruta.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
        ancho, alto = int(d.get("ancho", 1920)), int(d.get("alto", 1080))
        dets = [
            Det(t=f[0], pista=f[1], caja=(f[2], f[3], f[4], f[5]), alto_cuadro=alto,
                ancho_cuadro=ancho, grupo=f[6], equipo=f[7], tramo=f[8])
            for f in d.get("dets", [])
        ]
        tramitos = {int(t["id"]): Tramito(**t) for t in d.get("tramitos", [])}
        return cls(dets=dets, tramitos=tramitos, ancho=ancho, alto=alto)

    # -- consultas ------------------------------------------------------------
    def cerca_de(self, t: float, caja: tuple[float, float, float, float] | None,
                 antes_s: float = 1.5, despues_s: float = 0.5, maximo: int = 4) -> list[tuple[Tramito, float]]:
        """Los tramitos del propio que están cerca del contacto.

        ``caja`` es la zona del contacto en proporciones del cuadro (0 a 1).
        Devuelve (tramito, distancia) del más cercano al más lejano. Sin caja
        se ordenan por cuánto tiempo estuvieron en pantalla en ese momento.
        """
        mejor: dict[int, float] = {}
        for d in self.dets:
            if d.tramo < 0 or not (t - antes_s <= d.t <= t + despues_s):
                continue
            tr = self.tramitos.get(d.tramo)
            if tr is None or tr.equipo != "propio":
                continue
            x1, y1, x2, y2 = d.caja
            alto_j = max(0.02, (y2 - y1) / self.alto)
            cx, cy = (x1 + x2) / 2 / self.ancho, (y1 + y2) / 2 / self.alto
            if caja is None:
                dist = abs(d.t - t)
            else:
                bx = (caja[0] + caja[2]) / 2
                by = (caja[1] + caja[3]) / 2
                # Distancia medida en "alturas de jugador": cerca de cámara los
                # jugadores son grandes y las distancias en pantalla también.
                dist = ((cx - bx) ** 2 + (cy - by) ** 2) ** 0.5 / alto_j
                # Adentro de la zona del contacto: distancia cero.
                if caja[0] <= cx <= caja[2] and caja[1] <= cy <= caja[3]:
                    dist = 0.0
            if d.tramo not in mejor or dist < mejor[d.tramo]:
                mejor[d.tramo] = dist
        orden = sorted(mejor.items(), key=lambda x: x[1])[:maximo]
        return [(self.tramitos[k], v) for k, v in orden if k in self.tramitos]


# ---------------------------------------------------------------------------
# 1. Detectar y seguir
# ---------------------------------------------------------------------------


def _hist(img) -> list[float]:
    import cv2

    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    h = cv2.calcHist([hsv], [0, 1, 2], None, [8, 4, 4], [0, 180, 0, 256, 0, 256]).flatten()
    s = float(h.sum()) or 1.0
    return (h / s).tolist()


def _zonas(fr, caja):
    """Camiseta y short de un jugador (la parte central, sin los brazos)."""
    x1, y1, x2, y2 = caja
    h, w = y2 - y1, x2 - x1
    cx1, cx2 = x1 + int(0.25 * w), x2 - int(0.25 * w)
    torso = fr[y1 + int(0.18 * h): y1 + int(0.45 * h), cx1:cx2]
    short = fr[y1 + int(0.47 * h): y1 + int(0.62 * h), cx1:cx2]
    return torso, short


def detectar(video: Path, desfase_s: float, cfg: dict[str, Any],
             progreso: Callable[[str, float], None] | None = None) -> tuple[list[Det], list[list[float]], int, int]:
    """Corre el detector con seguimiento sobre todo el archivo.

    Devuelve las detecciones (con tiempo del partido), los colores de cada
    una y el tamaño del cuadro.
    """
    try:
        import cv2
        from ultralytics import YOLO
    except ImportError as exc:  # pragma: no cover - depende de la compu
        raise SinSeguimiento(
            "Falta el paquete para seguir jugadores. Corré 7_trabajador.bat de nuevo "
            "(instala 'ultralytics')."
        ) from exc

    modelo = YOLO(str(cfg.get("modelo") or "yolo11n.pt"))
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    ancho = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920)
    alto = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080)
    paso = max(1, int(round(fps / float(cfg.get("fps", 10)))))
    alto_min = int(cfg.get("alto_minimo_px", 60))
    dets: list[Det] = []
    colores: list[list[float]] = []
    i = 0
    while True:
        if i % paso:
            if not cap.grab():
                break
            i += 1
            continue
        ok, fr = cap.read()
        if not ok:
            break
        r = modelo.track(fr, persist=True, classes=[0], imgsz=int(cfg.get("imgsz", 1280)),
                         conf=float(cfg.get("conf", 0.25)), tracker="bytetrack.yaml",
                         verbose=False)[0]
        if r.boxes is not None and r.boxes.id is not None:
            for caja, pid in zip(r.boxes.xyxy.tolist(), r.boxes.id.int().tolist()):
                c = tuple(int(v) for v in caja)
                if c[3] - c[1] < alto_min:
                    continue
                torso, short = _zonas(fr, c)
                if torso.size == 0 or short.size == 0:
                    continue
                dets.append(Det(t=i / fps + desfase_s, pista=int(pid), caja=c,
                                alto_cuadro=alto, ancho_cuadro=ancho))
                colores.append(_hist(torso) + _hist(short))
        if progreso and total and i % (paso * 50) == 0:
            progreso(f"Siguiendo jugadores {100 * i // total}%", i / total)
        i += 1
    cap.release()
    return dets, colores, ancho, alto


# ---------------------------------------------------------------------------
# 2. Separar equipos
# ---------------------------------------------------------------------------


def agrupar_colores(colores: list[list[float]], grupos: int = 6) -> list[int]:
    import numpy as np
    from sklearn.cluster import KMeans

    X = np.sqrt(np.asarray(colores, dtype="float32"))
    k = min(grupos, len(X))
    return KMeans(k, n_init=10, random_state=0).fit(X).labels_.tolist()


def hoja_de_grupos(video: Path, desfase_s: float, dets: list[Det], destino: Path,
                   por_grupo: int = 10) -> Path:
    """Una fila de muestras por grupo de colores, numeradas, para que Claude diga cuál es cuál."""
    import cv2
    import numpy as np

    rng = np.random.default_rng(0)
    por: dict[int, list[Det]] = defaultdict(list)
    for d in dets:
        por[d.grupo].append(d)
    filas = []
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    for g in sorted(por):
        lista = sorted(por[g], key=lambda d: -(d.caja[3] - d.caja[1]))[: max(por_grupo * 5, 1)]
        elegidas = [lista[k] for k in rng.choice(len(lista), min(por_grupo, len(lista)), replace=False)]
        fotos = []
        for d in elegidas:
            cap.set(cv2.CAP_PROP_POS_FRAMES, round((d.t - desfase_s) * fps))
            ok, fr = cap.read()
            if not ok:
                continue
            x1, y1, x2, y2 = d.caja
            fotos.append(cv2.resize(fr[y1:y2, x1:x2], (72, 144)))
        while len(fotos) < por_grupo:
            fotos.append(np.full((144, 72, 3), 255, np.uint8))
        fila = np.hstack([np.full((144, 60, 3), 255, np.uint8)] + fotos)
        cv2.putText(fila, str(g + 1), (8, 85), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 0, 0), 3)
        filas.append(fila)
    cap.release()
    destino.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(destino), np.vstack(filas))
    return destino


ESQUEMA_GRUPOS = {
    "type": "object",
    "properties": {
        "propio": {"type": "array", "items": {"type": "integer"}},
        "rival": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["propio", "rival"],
}


def nombrar_grupos(cliente: Any, hoja: Path, kit_propio: str, kit_rival: str,
                   nombre_propio: str, nombre_rival: str) -> tuple[set[int], set[int]]:
    """Claude mira la hoja y dice qué filas son de cada equipo (números de fila desde 1)."""
    from .gemini.cliente import Pedido

    prompt = (
        "Cada fila de la imagen es un grupo de personas recortadas de un partido de "
        "rugby, con el número de grupo a la izquierda.\n"
        f"{nombre_propio}: {kit_propio or 'sin descripción'}.\n"
        f"{nombre_rival}: {kit_rival or 'cualquier otra camiseta'}.\n"
        "Fijate en la camiseta (lisa o a rayas, colores) y el short.\n"
        f'En "propio" poné los grupos donde casi todos son de {nombre_propio}; en '
        f'"rival" los grupos donde casi todos son de {nombre_rival}. Los grupos '
        "mezclados, de árbitros o de público no van en ninguna lista."
    )
    r = cliente.pedir(Pedido(prompt=prompt, esquema=ESQUEMA_GRUPOS, etiqueta="grupos de camisetas",
                             referencias=[("Grupos", str(hoja))],
                             titulo_referencias="GRUPOS DE CAMISETAS:", nivel="fuerte"))
    datos = r.json()
    propio = {int(x) - 1 for x in datos.get("propio") or [] if str(x).lstrip("-").isdigit()}
    rival = {int(x) - 1 for x in datos.get("rival") or [] if str(x).lstrip("-").isdigit()} - propio
    return propio, rival


def asignar_equipos(dets: list[Det], colores: list[list[float]], propio: set[int], rival: set[int]) -> None:
    """Cada detección queda como propio / rival / otro.

    Los grupos claros se usan para enseñarle a un clasificador sencillo, que
    después decide en los grupos mezclados. Al final vota cada recorrido.
    """
    import numpy as np

    X = np.sqrt(np.asarray(colores, dtype="float32"))
    y = np.array([1 if d.grupo in propio else 2 if d.grupo in rival else 0 for d in dets])
    pred = y.copy()
    if (y == 1).any() and (y == 2).any():
        from sklearn.linear_model import LogisticRegression

        clf = LogisticRegression(max_iter=500).fit(X, y)
        proba = clf.predict_proba(X)
        clases = list(clf.classes_)
        for k, d in enumerate(dets):
            if y[k] != 0:
                continue
            p = proba[k]
            mejor = int(np.argmax(p))
            # Solo si está muy claro; si no, "otro" (árbitro, público, mezcla).
            if clases[mejor] in (1, 2) and p[mejor] > 0.65:
                pred[k] = clases[mejor]
    nombres = {0: "otro", 1: "propio", 2: "rival"}
    for d, p in zip(dets, pred):
        d.equipo = nombres[int(p)]


# ---------------------------------------------------------------------------
# 3. Tramitos limpios
# ---------------------------------------------------------------------------


def _iou(a, b) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    if not inter:
        return 0.0
    area = lambda c: (c[2] - c[0]) * (c[3] - c[1])  # noqa: E731
    return inter / float(area(a) + area(b) - inter)


def armar_tramitos(dets: list[Det], minimo: int = 5, cruce: float = 0.25,
                   hueco_s: float = 1.0) -> dict[int, Tramito]:
    """Corta cada recorrido donde se cruza con otro o desaparece, y lo vota por equipo."""
    por_t: dict[float, list[Det]] = defaultdict(list)
    for d in dets:
        por_t[round(d.t, 3)].append(d)
    cruzados: set[int] = set()
    for lista in por_t.values():
        for a in range(len(lista)):
            for b in range(a + 1, len(lista)):
                if _iou(lista[a].caja, lista[b].caja) > cruce:
                    cruzados.add(id(lista[a]))
                    cruzados.add(id(lista[b]))

    por_pista: dict[int, list[Det]] = defaultdict(list)
    for d in dets:
        por_pista[d.pista].append(d)
    tramitos: dict[int, Tramito] = {}
    siguiente = 0
    for lista in por_pista.values():
        lista.sort(key=lambda d: d.t)
        actual: list[Det] = []

        def cerrar() -> None:
            nonlocal siguiente, actual
            if len(actual) >= minimo:
                votos = Counter(d.equipo for d in actual)
                equipo, cuantos = votos.most_common(1)[0]
                if equipo != "otro" and cuantos >= 0.7 * len(actual):
                    for d in actual:
                        d.tramo = siguiente
                    tramitos[siguiente] = Tramito(id=siguiente, equipo=equipo, desde=actual[0].t,
                                                  hasta=actual[-1].t, n=len(actual))
                    siguiente += 1
            actual = []

        for d in lista:
            if id(d) in cruzados or (actual and d.t - actual[-1].t > hueco_s):
                cerrar()
                if id(d) in cruzados:
                    continue
            actual.append(d)
        cerrar()
    return tramitos


# ---------------------------------------------------------------------------
# 4. Fotos de cada tramito, número y descripción
# ---------------------------------------------------------------------------


def _nitidez(img) -> float:
    import cv2

    return float(cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())


def sacar_fotos(video: Path, desfase_s: float, seg: Seguimiento, carpeta: Path,
                por_tramito: int = 4, equipo: str = "propio") -> None:
    """Guarda las mejores fotos (grandes, nítidas, de momentos distintos) de cada tramito."""
    import cv2

    candidatas: dict[int, list[Det]] = defaultdict(list)
    for d in seg.dets:
        t = seg.tramitos.get(d.tramo)
        if t is not None and t.equipo == equipo:
            candidatas[d.tramo].append(d)
    pedidos: list[tuple[float, int, Det]] = []
    for tid, lista in candidatas.items():
        lista.sort(key=lambda d: -(d.caja[3] - d.caja[1]))
        elegidas: list[Det] = []
        for d in lista[: por_tramito * 4]:
            if all(abs(d.t - o.t) >= 0.8 for o in elegidas):
                elegidas.append(d)
            if len(elegidas) >= por_tramito * 2:
                break
        pedidos += [(d.t, tid, d) for d in elegidas]
    pedidos.sort(key=lambda x: x[0])
    carpeta.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    fotos: dict[int, list[tuple[float, str]]] = defaultdict(list)
    for t, tid, d in pedidos:
        cap.set(cv2.CAP_PROP_POS_FRAMES, round((t - desfase_s) * fps))
        ok, fr = cap.read()
        if not ok:
            continue
        x1, y1, x2, y2 = d.caja
        m = int(0.06 * (y2 - y1))
        rec = fr[max(0, y1 - m): y2 + m, max(0, x1 - m): x2 + m]
        if rec.size == 0:
            continue
        ruta = carpeta / f"t{tid}_{int(t * 10)}.jpg"
        cv2.imwrite(str(ruta), rec, [cv2.IMWRITE_JPEG_QUALITY, 92])
        fotos[tid].append(((y2 - y1) * (_nitidez(rec) ** 0.25), str(ruta)))
    cap.release()
    for tid, lista in fotos.items():
        lista.sort(reverse=True)
        seg.tramitos[tid].fotos = [r for _, r in lista[:por_tramito]]


def hoja_tramitos(tramitos: list[Tramito], destino: Path, alto: int = 220) -> Path:
    """Una fila por tramito (con su letra) y sus fotos al lado."""
    from PIL import Image, ImageDraw

    filas = []
    for k, t in enumerate(tramitos):
        imgs = []
        for r in t.fotos:
            try:
                im = Image.open(r).convert("RGB")
            except Exception:  # noqa: BLE001
                continue
            imgs.append(im.resize((max(1, int(im.width * alto / im.height)), alto)))
        ancho = 70 + sum(i.width + 6 for i in imgs)
        fila = Image.new("RGB", (max(ancho, 200), alto), "white")
        ImageDraw.Draw(fila).text((10, alto // 2 - 20), _letra(k), fill="black",
                                  font=_fuente(40))
        x = 70
        for im in imgs:
            fila.paste(im, (x, 0))
            x += im.width + 6
        filas.append(fila)
    W = max(f.width for f in filas)
    hoja = Image.new("RGB", (W, sum(f.height + 8 for f in filas)), "white")
    y = 0
    for f in filas:
        hoja.paste(f, (0, y))
        y += f.height + 8
    destino.parent.mkdir(parents=True, exist_ok=True)
    hoja.save(destino, quality=90)
    return destino


def _letra(k: int) -> str:
    return "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[k] if k < 26 else f"Z{k}"


def _fuente(tam: int):
    from PIL import ImageFont

    for nombre in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(nombre, tam)
        except OSError:
            continue
    return ImageFont.load_default()


ESQUEMA_LECTURA = {
    "type": "object",
    "properties": {
        "jugadores": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "letra": {"type": "string"},
                    "mismo_jugador": {"type": "boolean"},
                    "numero": {"type": ["integer", "null"]},
                    "confianza_numero": {"type": "number"},
                    "descripcion": {
                        "type": "object",
                        "properties": {r: {"type": "string"} for r in RASGOS_SEG},
                    },
                },
                "required": ["letra", "numero", "confianza_numero", "descripcion"],
            },
        }
    },
    "required": ["jugadores"],
}


def prompt_lectura(nombre: str, kit: str, numeros: list[int]) -> str:
    return f"""Cada fila de la imagen es UN jugador de {nombre} (camiseta: {kit or 'la del equipo'}), \
seguido durante unos segundos de un partido de rugby, con una letra a la izquierda. \
Las fotos de la fila son momentos distintos del mismo recorrido.

Para cada fila:
- "numero": el número de la espalda o del pecho, SOLO si lo leés con claridad en alguna \
foto. Números posibles: {", ".join(str(n) for n in sorted(numeros))}. Si no se lee, null. \
No adivines: un número equivocado es peor que ninguno.
- "confianza_numero": de 0 a 1.
- "mismo_jugador": false si en la fila hay fotos de dos personas distintas.
- "descripcion": cómo es, para reconocerlo cuando no se le vea el número. Solo lo que se ve; \
si no se ve, dejalo vacío:
  - botines: color o colores ("blancos", "naranjas con negro")
  - vincha_casco: "no", o qué tiene y de qué color ("casco negro", "vincha blanca")
  - cintas: dónde tiene cinta o vendas y de qué color ("cinta blanca en las dos rodillas", \
"vendas en los muslos", "muñequera negra")
  - pelo: largo y color ("corto oscuro", "rubio largo atado", "rapado")
  - contextura: "flaco", "mediano", "robusto", y si es alto o bajo
  - medias: si tienen algo distinto (bajas, otro color)
  - otro: barba, tatuajes, camiseta por fuera, cualquier cosa que lo distinga"""


def leer_tramitos(cliente: Any, seg: Seguimiento, carpeta: Path, nombre: str, kit: str,
                  numeros: list[int], por_hoja: int = 6, maximo: int = 120,
                  minimo_fotos: int = 2,
                  progreso: Callable[[str, float], None] | None = None) -> None:
    """Le pide a Claude el número y la descripción de los tramitos del propio."""
    from .gemini.cliente import Pedido

    lista = [t for t in seg.tramitos.values() if t.equipo == "propio" and len(t.fotos) >= minimo_fotos]
    # Primero los más largos: más fotos, más chances de leer el número.
    lista.sort(key=lambda t: -t.n)
    lista = lista[:maximo]
    texto = prompt_lectura(nombre, kit, numeros)
    validos = set(numeros)
    for k in range(0, len(lista), por_hoja):
        grupo = lista[k: k + por_hoja]
        if progreso:
            progreso(f"Leyendo números {k + len(grupo)}/{len(lista)}", (k + len(grupo)) / max(1, len(lista)))
        hoja = hoja_tramitos(grupo, carpeta / f"hoja_{k // por_hoja}.jpg")
        try:
            r = cliente.pedir(Pedido(prompt=texto, prefijo=texto, esquema=ESQUEMA_LECTURA,
                                     etiqueta=f"leer jugadores {k // por_hoja}",
                                     referencias=[("Jugadores", str(hoja))],
                                     titulo_referencias="JUGADORES A LEER:", nivel="fuerte"))
            datos = r.json()
        except Exception as exc:  # noqa: BLE001 - una hoja que falla no frena el resto
            if _es_grave(exc):
                raise
            log.warning("No pude leer la hoja %s: %s", k // por_hoja, exc)
            continue
        for item in (datos or {}).get("jugadores") or []:
            letra = str(item.get("letra") or "").strip().upper()
            idx = "ABCDEFGHIJKLMNOPQRSTUVWXYZ".find(letra[:1]) if letra else -1
            if not 0 <= idx < len(grupo):
                continue
            t = grupo[idx]
            if item.get("mismo_jugador") is False:
                continue
            desc = item.get("descripcion") or {}
            t.descripcion = {str(a): str(b).strip() for a, b in desc.items()
                             if b and str(b).strip() and str(b).strip().lower() not in ("no se ve", "null")}
            try:
                n = int(item.get("numero"))
            except (TypeError, ValueError):
                n = None
            if n in validos:
                t.numero = n
                t.confianza_numero = float(item.get("confianza_numero") or 0.0)
                if t.confianza_numero >= 0.7:
                    t.jugador, t.seguridad, t.como = n, t.confianza_numero, "número leído"


def _es_grave(exc: Exception) -> bool:
    texto = f"{type(exc).__name__} {exc}".lower()
    return any(p in texto for p in ("credit", "authentication", "api key", "billing", "permission"))


# ---------------------------------------------------------------------------
# 5. Fichas del plantel
# ---------------------------------------------------------------------------


def fichas_del_plantel(seg: Seguimiento) -> dict[int, dict[str, Any]]:
    """Por número: las descripciones juntadas y las mejores fotos de los tramitos identificados."""
    por: dict[int, list[Tramito]] = defaultdict(list)
    for t in seg.tramitos.values():
        if t.equipo == "propio" and t.jugador is not None and t.como == "número leído":
            por[t.jugador].append(t)
    salida: dict[int, dict[str, Any]] = {}
    for n, lista in por.items():
        lista.sort(key=lambda t: -t.seguridad)
        rasgos: dict[str, Counter] = defaultdict(Counter)
        for t in lista:
            for k, v in t.descripcion.items():
                rasgos[k][v.lower()] += 1
        fotos: list[str] = []
        for t in lista:
            for r in t.fotos[:2]:
                if len(fotos) < 4:
                    fotos.append(r)
        salida[n] = {
            "rasgos": {k: c.most_common(1)[0][0] for k, c in rasgos.items()},
            "fotos": fotos,
            "veces": len(lista),
            "desde": [t.desde for t in lista],
        }
    return salida


def describir_ficha(rasgos: dict[str, str]) -> str:
    nombres = {"botines": "botines", "vincha_casco": "cabeza", "cintas": "cintas",
               "pelo": "pelo", "contextura": "contextura", "medias": "medias", "otro": "otro"}
    return "; ".join(f"{nombres.get(k, k)}: {v}" for k, v in rasgos.items() if v)


# ---------------------------------------------------------------------------
# Todo junto
# ---------------------------------------------------------------------------


def correr(cliente: Any, video: Path, desfase_s: float, cfg_seg: dict[str, Any], carpeta: Path,
           nombre_propio: str, kit_propio: str, nombre_rival: str, kit_rival: str,
           numeros: list[int], progreso: Callable[[str, float], None] | None = None) -> Seguimiento:
    def avisar(msg: str, f: float, a: float, b: float) -> None:
        if progreso:
            progreso(msg, a + (b - a) * max(0.0, min(1.0, f)))

    dets, colores, ancho, alto = detectar(video, desfase_s, cfg_seg,
                                          lambda m, f: avisar(m, f, 0.0, 0.7))
    if not dets:
        return Seguimiento(dets=[], tramitos={}, ancho=ancho, alto=alto)
    for d, g in zip(dets, agrupar_colores(colores, int(cfg_seg.get("grupos", 6)))):
        d.grupo = g
    avisar("Separando equipos", 0, 0.7, 0.75)
    hoja = hoja_de_grupos(video, desfase_s, dets, carpeta / "grupos.jpg")
    propio, rival = nombrar_grupos(cliente, hoja, kit_propio, kit_rival, nombre_propio, nombre_rival)
    if not propio:
        log.warning("Claude no reconoció ningún grupo como del propio.")
    asignar_equipos(dets, colores, propio, rival)
    tramitos = armar_tramitos(dets, minimo=int(cfg_seg.get("minimo_detecciones", 5)))
    seg = Seguimiento(dets=dets, tramitos=tramitos, ancho=ancho, alto=alto)
    log.info("Seguimiento: %d detecciones, %d tramitos del propio, %d del rival",
             len(dets), sum(t.equipo == "propio" for t in tramitos.values()),
             sum(t.equipo == "rival" for t in tramitos.values()))
    avisar("Sacando fotos de cada jugador", 0, 0.75, 0.8)
    sacar_fotos(video, desfase_s, seg, carpeta / "fotos", int(cfg_seg.get("fotos_por_tramito", 4)))
    leer_tramitos(cliente, seg, carpeta / "hojas", nombre_propio, kit_propio, numeros,
                  por_hoja=int(cfg_seg.get("por_hoja", 6)),
                  maximo=int(cfg_seg.get("max_tramitos", 120)),
                  progreso=lambda m, f: avisar(m, f, 0.8, 1.0))
    return seg
