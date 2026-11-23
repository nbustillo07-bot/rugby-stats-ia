@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
echo ============================================
echo   rugby-stats-ia - clave de Claude
echo ============================================
echo.
if not exist ".env" copy ".env.example" ".env" >nul
findstr /b /c:"ANTHROPIC_API_KEY=" ".env" >nul || (echo.>>".env" & echo ANTHROPIC_API_KEY=>>".env")
echo Se van a abrir dos cosas:
echo.
echo   1. La consola de Claude en el navegador (platform.claude.com).
echo      Ahi: iniciar sesion, tocar "Create Key", ponerle de nombre
echo      rugby-stats-ia y COPIAR la clave (empieza con sk-ant-...).
echo      Conviene ponerle un limite de gasto mensual a esa clave.
echo.
echo   2. El Bloc de notas con el archivo .env.
echo      Ahi: pegar la clave justo despues de ANTHROPIC_API_KEY=
echo      sin espacios ni comillas. Guardar con Ctrl+S y cerrar.
echo.
echo   La clave es como una tarjeta: no la mandes por chat ni la subas
echo   a GitHub (el archivo .env no se sube, ya esta configurado asi).
echo.
pause
start "" "https://platform.claude.com/settings/keys"
notepad ".env"
echo.
echo Listo. Siguiente: doble clic en 3_verificar.bat
echo.
pause
