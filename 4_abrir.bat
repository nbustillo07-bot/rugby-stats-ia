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
echo Actualizando lo necesario (la primera vez tarda un poco)...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
echo Abriendo rugby-stats-ia en el navegador...
echo (Para cerrarlo: cerrar esta ventana negra.)
echo.
".venv\Scripts\python.exe" -m streamlit run app.py
pause
