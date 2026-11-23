@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Falta instalar. Hace doble clic primero en 1_instalar.bat
    pause
    exit /b 1
)
title rugby-stats-ia - trabajador
:otra_vez
echo Buscando actualizaciones...
rem Trae las mejoras que se hayan subido a GitHub (si hay git). Si algo
rem falla, sigue con lo que ya tiene.
where git >nul 2>nul && git pull --ff-only -q >nul 2>nul
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
rem yt-dlp se actualiza seguido porque YouTube cambia seguido.
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -U "yt-dlp[default]"
".venv\Scripts\python.exe" scripts\trabajador.py
rem 3 = se reinicia solo para actualizarse (cada tantas horas sin trabajo).
if %errorlevel%==3 goto otra_vez
rem 4 = ya habia otro trabajador abierto: esta ventana no hace falta.
if %errorlevel%==4 exit /b 0
echo.
echo El trabajador se corto. Vuelve a arrancar en 30 segundos (cerra la ventana para frenarlo).
timeout /t 30 >nul
goto otra_vez
