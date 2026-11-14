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
echo Esta prueba le hace una docena de pedidos a Gemini sobre un tramo
echo corto del video y anota todo lo que pasa. Tarda unos minutos.
echo.
echo Si ya cargaste un partido en la aplicacion, apreta Enter y usa ese.
echo Si no, pega el link de YouTube del partido y apreta Enter.
echo.
set URL=
set /p URL=Link del video (o Enter): 
echo.
if defined URL (
    ".venv\Scripts\python.exe" scripts\diagnostico.py --video "%URL%"
) else (
    ".venv\Scripts\python.exe" scripts\diagnostico.py
)
echo.
echo El resultado quedo guardado en la carpeta salidas\diagnostico
echo.
pause
