@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
echo ============================================
echo   rugby-stats-ia - clave de Gemini
echo ============================================
echo.
if not exist ".env" copy ".env.example" ".env" >nul
echo Se van a abrir dos cosas:
echo.
echo   1. La pagina de Google AI Studio en el navegador.
echo      Ahi: iniciar sesion con Google, tocar "Create API key"
echo      y COPIAR la clave (empieza con AIza...).
echo.
echo   2. El Bloc de notas con el archivo .env.
echo      Ahi: pegar la clave justo despues de GEMINI_API_KEY=
echo      sin espacios ni comillas. Guardar con Ctrl+S y cerrar.
echo.
pause
start "" "https://aistudio.google.com/apikey"
notepad ".env"
echo.
echo Listo. Siguiente: doble clic en 3_verificar.bat
echo.
pause
