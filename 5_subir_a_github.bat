@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
echo ============================================
echo   rugby-stats-ia - subir a GitHub
echo ============================================
echo.
where git >nul 2>nul
if errorlevel 1 (
    echo No encontre git. Instalalo desde https://git-scm.com y volve a probar.
    pause
    exit /b 1
)
echo ANTES de seguir: el repositorio vacio tiene que estar creado en
echo https://github.com/new con el nombre rugby-stats-ia
echo (sin README, sin .gitignore y sin licencia).
echo.
set USUARIO=
set /p USUARIO=Usuario de GitHub (el que aparece en github.com/USUARIO): 
if not defined USUARIO (
    echo No escribiste ningun usuario.
    pause
    exit /b 1
)
git config core.fileMode false
git branch -M main
git remote remove origin >nul 2>nul
git remote add origin "https://%USUARIO%@github.com/%USUARIO%/rugby-stats-ia.git"
echo.
echo Subiendo... Si se abre una ventana de GitHub pidiendo iniciar
echo sesion, tocar "Sign in with your browser" y aceptar.
echo.
git push -u origin main
if errorlevel 1 (
    echo.
    echo No se pudo subir. Lo mas comun: el repositorio no esta creado,
    echo o el usuario esta mal escrito. Sacale una foto a esta ventana.
) else (
    echo.
    echo LISTO. Quedo en https://github.com/%USUARIO%/rugby-stats-ia
)
echo.
pause
