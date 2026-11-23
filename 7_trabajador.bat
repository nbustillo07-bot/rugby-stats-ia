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
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
:otra_vez
".venv\Scripts\python.exe" scripts\trabajador.py
echo.
echo El trabajador se corto. Vuelve a arrancar en 30 segundos (cerra la ventana para frenarlo).
timeout /t 30 >nul
goto otra_vez
