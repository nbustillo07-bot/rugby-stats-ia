"""Preparar el video: bajar, subir, y no repetir ninguna de las dos cosas."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from rugby_stats.gemini import video as v
from rugby_stats.modelos import FuenteVideo

URL = "https://www.youtube.com/watch?v=VsRZotfwL6A"


class ClienteFalso:
    def __init__(self, falla: bool = False):
        self.subidas: list[str] = []
        self.falla = falla

    def subir_archivo(self, ruta, esperar=True):
        if self.falla:
            raise RuntimeError("no se pudo subir")
        self.subidas.append(str(ruta))
        return f"https://generativelanguage.googleapis.com/v1beta/files/f{len(self.subidas)}"


@pytest.fixture
def bajadas(tmp_path, monkeypatch):
    """Reemplaza la descarga por una que crea un archivo vacío."""
    hechas: list[str] = []

    def falsa(referencia, destino, calidad="480"):
        destino.mkdir(parents=True, exist_ok=True)
        ruta = destino / "bajado.mp4"
        ruta.write_bytes(b"video")
        hechas.append(referencia)
        return ruta

    monkeypatch.setattr(v, "descargar", falsa)
    monkeypatch.setattr(v, "hay_ytdlp", lambda: True)
    monkeypatch.setattr(v, "duracion_s", lambda _: 5400.0)
    return hechas


class TestYouTube:
    def test_un_video_corto_va_directo_por_url(self, tmp_path, bajadas, monkeypatch):
        monkeypatch.setattr(v, "duracion_s", lambda _: 300.0)
        c = ClienteFalso()
        f = v.preparar(FuenteVideo(tipo="youtube", referencia=URL), c, tmp_path, 1200)
        assert f.uri_subida == URL
        assert bajadas == [] and c.subidas == []

    def test_un_partido_entero_se_baja_y_se_sube(self, tmp_path, bajadas):
        c = ClienteFalso()
        f = v.preparar(FuenteVideo(tipo="youtube", referencia=URL), c, tmp_path, 1200)
        assert bajadas == [URL]
        assert len(c.subidas) == 1
        assert "files/" in f.uri_subida
        assert f.subido_en

    def test_la_segunda_vez_no_baja_ni_sube_de_nuevo(self, tmp_path, bajadas):
        """Antes cada corrida volvía a bajar y subir el partido entero."""
        c = ClienteFalso()
        f = v.preparar(FuenteVideo(tipo="youtube", referencia=URL), c, tmp_path, 1200)
        f = v.preparar(f, c, tmp_path, 1200)
        f = v.preparar(f, c, tmp_path, 1200)
        assert len(bajadas) == 1
        assert len(c.subidas) == 1

    def test_una_subida_vencida_se_renueva_sin_volver_a_bajar(self, tmp_path, bajadas):
        """Los archivos de la Files API se borran a las 48 horas."""
        c = ClienteFalso()
        f = v.preparar(FuenteVideo(tipo="youtube", referencia=URL), c, tmp_path, 1200)
        viejo = datetime.now(timezone.utc) - timedelta(hours=47)
        f.subido_en = viejo.isoformat()
        primera = f.uri_subida
        f = v.preparar(f, c, tmp_path, 1200)
        assert len(bajadas) == 1          # el archivo sigue en disco
        assert len(c.subidas) == 2        # pero se volvió a subir
        assert f.uri_subida != primera

    def test_si_se_borro_el_archivo_bajado_se_vuelve_a_bajar(self, tmp_path, bajadas):
        c = ClienteFalso()
        f = v.preparar(FuenteVideo(tipo="youtube", referencia=URL), c, tmp_path, 1200)
        (tmp_path / "bajado.mp4").unlink()
        f = v.preparar(f, c, tmp_path, 1200)
        assert len(bajadas) == 2

    def test_si_falla_la_descarga_sigue_con_la_url(self, tmp_path, monkeypatch):
        def explota(*a, **k):
            raise v.ErrorVideo("YouTube no deja")

        monkeypatch.setattr(v, "descargar", explota)
        monkeypatch.setattr(v, "hay_ytdlp", lambda: True)
        monkeypatch.setattr(v, "duracion_s", lambda _: 5400.0)
        f = v.preparar(FuenteVideo(tipo="youtube", referencia=URL), ClienteFalso(), tmp_path, 1200)
        assert f.uri_subida == URL
        assert f.ruta_local is None

    def test_si_falla_la_subida_sigue_con_la_url(self, tmp_path, bajadas):
        f = v.preparar(
            FuenteVideo(tipo="youtube", referencia=URL), ClienteFalso(falla=True),
            tmp_path, 1200,
        )
        assert f.uri_subida == URL

    def test_sin_yt_dlp_va_por_url(self, tmp_path, monkeypatch):
        monkeypatch.setattr(v, "hay_ytdlp", lambda: False)
        monkeypatch.setattr(v, "duracion_s", lambda _: None)
        f = v.preparar(FuenteVideo(tipo="youtube", referencia=URL), ClienteFalso(), tmp_path, 1200)
        assert f.uri_subida == URL

    def test_con_el_umbral_en_cero_nunca_baja(self, tmp_path, bajadas):
        c = ClienteFalso()
        f = v.preparar(FuenteVideo(tipo="youtube", referencia=URL), c, tmp_path, 0)
        assert f.uri_subida == URL and bajadas == []


class TestArchivoLocal:
    def test_se_sube_una_sola_vez(self, tmp_path, monkeypatch):
        monkeypatch.setattr(v, "duracion_s", lambda _: 600.0)
        archivo = tmp_path / "partido.mp4"
        archivo.write_bytes(b"x")
        c = ClienteFalso()
        f = FuenteVideo(tipo="archivo", referencia=str(archivo), ruta_local=str(archivo))
        f = v.preparar(f, c, tmp_path / "trabajo")
        f = v.preparar(f, c, tmp_path / "trabajo")
        assert len(c.subidas) == 1

    def test_si_el_archivo_desaparecio_lo_dice(self, tmp_path, monkeypatch):
        monkeypatch.setattr(v, "duracion_s", lambda _: None)
        f = FuenteVideo(tipo="archivo", referencia="no.mp4", ruta_local=str(tmp_path / "no.mp4"))
        with pytest.raises(v.ErrorVideo) as exc:
            v.preparar(f, ClienteFalso(), tmp_path)
        assert "No encuentro" in str(exc.value)

    def test_una_fuente_guardada_sin_fecha_de_subida_se_vuelve_a_subir(
        self, tmp_path, monkeypatch
    ):
        """Partidos guardados antes de que existiera la fecha de subida."""
        monkeypatch.setattr(v, "duracion_s", lambda _: 600.0)
        archivo = tmp_path / "partido.mp4"
        archivo.write_bytes(b"x")
        c = ClienteFalso()
        f = FuenteVideo(
            tipo="archivo", referencia=str(archivo), ruta_local=str(archivo),
            uri_subida="https://generativelanguage.googleapis.com/v1beta/files/viejo",
        )
        f = v.preparar(f, c, tmp_path / "trabajo")
        assert len(c.subidas) == 1


class TestOtraUrl:
    def test_se_baja_y_se_sube(self, tmp_path, bajadas):
        c = ClienteFalso()
        f = v.preparar(
            FuenteVideo(tipo="url", referencia="https://ejemplo.com/v.mp4"), c, tmp_path
        )
        assert len(bajadas) == 1 and len(c.subidas) == 1
        f = v.preparar(f, c, tmp_path)
        assert len(bajadas) == 1 and len(c.subidas) == 1


class TestYtDlp:
    def test_se_invoca_como_modulo_del_python_que_corre(self, monkeypatch):
        """Con doble clic el entorno no está activado y yt-dlp no está en el
        PATH, aunque esté instalado."""
        import sys

        monkeypatch.setattr(v.importlib.util, "find_spec", lambda n: object())
        monkeypatch.setattr(v.shutil, "which", lambda n: None)
        monkeypatch.setattr(v, "_deno", lambda: None)
        assert v._cmd_ytdlp() == [sys.executable, "-m", "yt_dlp"]
        assert v.hay_ytdlp()

    def test_le_pasa_deno_a_yt_dlp_si_esta(self, monkeypatch):
        """YouTube exige JavaScript: si está deno, se lo indica."""
        monkeypatch.setattr(v.importlib.util, "find_spec", lambda n: object())
        monkeypatch.setattr(v, "_deno", lambda: r"C:\venv\Scripts\deno.exe")
        assert v._cmd_ytdlp()[-2:] == ["--js-runtimes", r"deno:C:\venv\Scripts\deno.exe"]

    def test_si_no_esta_instalado_no_se_inventa(self, monkeypatch):
        monkeypatch.setattr(v.importlib.util, "find_spec", lambda n: None)
        monkeypatch.setattr(v.shutil, "which", lambda n: None)
        assert v._cmd_ytdlp() is None
        assert not v.hay_ytdlp()

    def test_sin_ffmpeg_pide_un_formato_de_un_solo_archivo(self, tmp_path, monkeypatch):
        vistos: list[list[str]] = []

        class Res:
            stdout = ""

        def correr(cmd, **k):
            vistos.append(cmd)
            (tmp_path / "x.mp4").write_bytes(b"v")
            return Res()

        monkeypatch.setattr(v, "_cmd_ytdlp", lambda: ["yt-dlp"])
        monkeypatch.setattr(v, "ffmpeg_exe", lambda: None)
        monkeypatch.setattr(v.subprocess, "run", correr)
        ruta = v.descargar(URL, tmp_path)
        formato = vistos[0][vistos[0].index("-f") + 1]
        assert "+" not in formato  # nada que haya que unir con ffmpeg
        assert ruta.name == "x.mp4"
