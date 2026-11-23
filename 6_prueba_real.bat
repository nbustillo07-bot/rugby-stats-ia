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
echo Actualizando lo necesario...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
echo.
echo ============================================
echo   Prueba real: 2 minutos del partido cargado
echo ============================================
echo.
echo Corre el analisis completo sobre 2 minutos del ultimo partido que
echo cargaste, mide cuanto costo DE VERDAD y lo proyecta al partido
echo entero. Sale alrededor de 30 centavos de dolar. No toca el partido
echo guardado: trabaja sobre una copia.
echo.
pause
".venv\Scripts\python.exe" scripts\diagnostico.py
echo.
echo El informe quedo en la carpeta salidas\diagnostico
echo.
pause
