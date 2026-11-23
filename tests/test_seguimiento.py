"""Seguimiento de jugadores: tramitos, lectura de números y uso en los contactos."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rugby_stats import seguimiento as S


def _det(t, pista, x, equipo="propio", y=400, alto=200):
    return S.Det(t=t, pista=pista, caja=(x, y, x + 80, y + alto), equipo=equipo)


def test_los_tramitos_se_cortan_donde_se_cruzan_dos():
    dets = []
    for k in range(20):
        t = 100 + k * 0.1
        dets.append(_det(t, 1, 100 + k * 5))
        # El 2 se le cruza al 1 en el medio (k 8 a 11).
        x2 = 140 + k * 5 if 8 <= k <= 11 else 900
        dets.append(_det(t, 2, x2, equipo="rival"))
    tr = S.armar_tramitos(dets, minimo=5)
    propios = [t for t in tr.values() if t.equipo == "propio"]
    assert len(propios) == 2  # antes y después del cruce
    assert all(t.hasta < 100.8 or t.desde > 101.1 for t in propios)


def test_un_recorrido_mezclado_no_es_de_nadie():
    dets = [_det(10 + k * 0.1, 1, 100, equipo="propio" if k % 2 else "rival") for k in range(20)]
    assert S.armar_tramitos(dets, minimo=5) == {}


def test_cerca_del_contacto_solo_los_del_propio(tmp_path):
    dets = [_det(50 + k * 0.1, 1, 900) for k in range(10)]
    dets += [_det(50 + k * 0.1, 2, 100) for k in range(10)]
    dets += [_det(50 + k * 0.1, 3, 1300, equipo="rival") for k in range(10)]
    tr = S.armar_tramitos(dets, minimo=5)
    seg = S.Seguimiento(dets=dets, tramitos=tr)
    seg.guardar(tmp_path / "s.json")
    seg = S.Seguimiento.cargar(tmp_path / "s.json")
    caja = (900 / 1920, 0.3, 1000 / 1920, 0.6)
    cerca = seg.cerca_de(50.5, caja)
    assert [t.equipo for t, _ in cerca] == ["propio", "propio"]
    assert cerca[0][1] == 0.0 and cerca[1][1] > 1


class _Resp:
    def __init__(self, d):
        self.d = d

    def json(self):
        return self.d


def test_leer_numeros_y_armar_fichas(tmp_path):
    from PIL import Image

    fotos = []
    for k in range(4):
        r = tmp_path / f"f{k}.jpg"
        Image.new("RGB", (60, 120), (120, 20, 30)).save(r)
        fotos.append(str(r))
    tr = {
        0: S.Tramito(id=0, equipo="propio", desde=10, hasta=15, n=40, fotos=fotos[:2]),
        1: S.Tramito(id=1, equipo="propio", desde=30, hasta=33, n=20, fotos=fotos[2:]),
        2: S.Tramito(id=2, equipo="rival", desde=30, hasta=33, n=20, fotos=fotos[2:]),
    }
    seg = S.Seguimiento(dets=[], tramitos=tr)
    vistos = []

    class Cliente:
        def pedir(self, p):
            vistos.append(p)
            return _Resp({"jugadores": [
                {"letra": "A", "numero": 6, "confianza_numero": 0.9, "mismo_jugador": True,
                 "descripcion": {"botines": "Rojos", "vincha_casco": "vincha blanca", "pelo": "no se ve"}},
                {"letra": "B", "numero": 40, "confianza_numero": 0.9,  # no está en el plantel
                 "descripcion": {"botines": "blancos"}},
            ]})

    S.leer_tramitos(Cliente(), seg, tmp_path / "hojas", "Los Molinos", "rayas", list(range(1, 24)))
    assert len(vistos) == 1 and vistos[0].referencias and vistos[0].nivel == "fuerte"
    assert "No adivines" in vistos[0].prompt
    assert tr[0].jugador == 6 and tr[0].como == "número leído"
    assert tr[1].jugador is None and tr[1].descripcion == {"botines": "blancos"}
    assert "pelo" not in tr[0].descripcion
    fichas = S.fichas_del_plantel(seg)
    assert list(fichas) == [6] and fichas[6]["rasgos"]["botines"] == "rojos"


def test_el_contacto_recibe_quienes_estaban(tmp_path):
    from PIL import Image

    from rugby_stats.analisis import pasadas
    from rugby_stats.config import Config
    from rugby_stats.modelos import Equipo, FuenteVideo, Jugador, Partido
    from tests.conftest import CONFIG_BASE

    foto = tmp_path / "f.jpg"
    Image.new("RGB", (60, 120)).save(foto)
    dets = [_det(50 + k * 0.1, 1, 900) for k in range(10)] + [_det(50 + k * 0.1, 2, 1000) for k in range(10)]
    tr = S.armar_tramitos(dets, minimo=5)
    for t in tr.values():
        t.fotos = [str(foto)]
    t0 = next(iter(tr.values()))
    t0.jugador, t0.como, t0.descripcion = 6, "número leído", {"botines": "rojos"}
    seg = S.Seguimiento(dets=dets, tramitos=tr)
    cfg = Config(dict(CONFIG_BASE, dir_video=str(tmp_path / "video")))
    partido = Partido(id=1, nombre="x", equipo_propio=Equipo(nombre="Los Molinos"),
                      equipo_rival=Equipo(nombre="Lanus"), video=FuenteVideo(tipo="archivo", referencia="v"))
    zoom = {"pista": [[50.0, [0.45, 0.3, 0.55, 0.6]], [51.0, [0.45, 0.3, 0.55, 0.6]]]}
    cerca, texto, hoja = pasadas._seguidos_en_contacto(
        seg, 50.5, zoom, cfg, partido, [Jugador(numero=6, nombre="Gallardo", equipo="Los Molinos")])
    assert len(cerca) == 2 and Path(hoja).exists()
    assert "6 Gallardo" in texto or "Gallardo" in texto
    assert "botines: rojos" in texto
    datos = pasadas._confirmar_con_seguimiento({"tackleadores": [6], "confianza_identificacion": 0.4}, cerca)
    assert datos["confianza_identificacion"] >= 0.9
    otro = pasadas._confirmar_con_seguimiento({"tackleadores": [9], "confianza_identificacion": 0.4}, cerca)
    assert otro["confianza_identificacion"] == 0.4


def test_separar_equipos_con_los_grupos_que_dice_claude():
    pytest.importorskip("sklearn")
    import random

    random.seed(0)

    def color(base):
        v = [0.0] * 256
        v[base] = 1.0
        v[base + 1] = random.random() * 0.2
        return v

    dets, colores = [], []
    for k in range(60):
        g = k % 3
        dets.append(S.Det(t=k, pista=k, caja=(0, 0, 10, 10), grupo=g))
        colores.append(color(10 if g == 0 else 100 if g == 1 else 200))
    S.asignar_equipos(dets, colores, {0}, {1})
    assert {d.equipo for d in dets if d.grupo == 0} == {"propio"}
    assert {d.equipo for d in dets if d.grupo == 1} == {"rival"}


def test_el_detector_corre_sobre_un_video(tmp_path):
    pytest.importorskip("ultralytics")
    modelo = Path("/home/claude/cv/yolo11n.pt")
    video = Path("/home/claude/cv/clip.mp4")
    if not modelo.exists() or not video.exists():
        pytest.skip("sin pesos del detector o video de rugby")
    dets, colores, ancho, alto = S.detectar(video, 100.0, {"fps": 0.5, "modelo": str(modelo), "imgsz": 640})
    assert ancho > 0 and alto > 0 and len(dets) == len(colores)
    assert all(d.t >= 100.0 for d in dets)
    json.dumps([d.caja for d in dets])
