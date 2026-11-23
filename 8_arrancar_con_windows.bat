@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   Que el trabajador arranque solo con Windows
echo ============================================
echo.
echo Esto deja un acceso directo en la carpeta de Inicio de Windows: cada vez
echo que prendas la compu, el trabajador arranca solo (minimizado) y queda
echo esperando lo que mandes desde la app.
echo.
echo Para sacarlo: borra "rugby-stats-trabajador" de la carpeta que se abre
echo al final.
echo.
pause
set INICIO=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%INICIO%\rugby-stats-trabajador.lnk'); $s.TargetPath='%~dp07_trabajador.bat'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save()"
if errorlevel 1 (
    echo No pude crear el acceso directo.
) else (
    echo Listo. Desde ahora arranca solo al prender la compu.
)
start "" "%INICIO%"
start "" "%~dp07_trabajador.bat"
echo.
pause
