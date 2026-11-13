@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
echo ============================================
echo   rugby-stats-ia - verificar la clave
echo ============================================
echo.
if not exist ".venv\Scripts\python.exe" (
    echo Falta instalar. Hace doble clic primero en 1_instalar.bat
    pause
    exit /b 1
)
echo Si queres probar tambien que puede mirar un video, pega el link
echo de YouTube y apreta Enter. Si no, apreta Enter directamente.
echo.
set URL=
set /p URL=Link del video (opcional): 
echo.
if defined URL (
    ".venv\Scripts\python.exe" scripts\verificar_api.py --video "%URL%"
) else (
    ".venv\Scripts\python.exe" scripts\verificar_api.py
)
echo.
pause
