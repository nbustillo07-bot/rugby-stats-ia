@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
echo ============================================
echo   rugby-stats-ia - instalacion
echo ============================================
echo.

set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY (
    python --version >nul 2>nul && set PY=python
)
if not defined PY (
    echo No encontre Python en esta compu.
    echo.
    echo Lo instalo ahora con winget. Si pide permiso, aceptar.
    echo.
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    echo.
    echo Python quedo instalado. CERRA esta ventana y volve a hacer
    echo doble clic en 1_instalar.bat para seguir.
    echo.
    pause
    exit /b 0
)

echo Python encontrado:
%PY% --version
echo.

if not exist ".venv\Scripts\python.exe" (
    echo Creando el entorno aislado ^(.venv^)...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo.
        echo ERROR: no pude crear el entorno. Sacale una foto a esta ventana.
        pause
        exit /b 1
    )
)

echo Instalando las librerias. Tarda unos minutos, es normal...
echo.
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: fallo la instalacion de librerias. Sacale una foto a esta ventana.
    pause
    exit /b 1
)

if not exist ".env" copy ".env.example" ".env" >nul

echo.
echo Corriendo las pruebas para confirmar que quedo todo bien...
".venv\Scripts\python.exe" -m pytest tests -q -x -p no:cacheprovider
echo.
echo ============================================
echo   LISTO. Instalacion terminada.
echo   Siguiente: doble clic en 2_poner_clave.bat
echo   (o en 4_abrir.bat para probar en modo simulado)
echo ============================================
echo.
pause
