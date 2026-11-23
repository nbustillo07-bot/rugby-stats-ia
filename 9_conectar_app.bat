@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   Conectar la compu con la app web
echo ============================================
echo.
if not exist ".env" copy ".env.example" ".env" >nul
findstr /b /c:"DATABASE_URL=" ".env" >nul || (echo.>>".env" & echo DATABASE_URL=>>".env")
echo Se abre el Bloc de notas con el archivo .env.
echo.
echo   Pega la direccion de la base justo despues de DATABASE_URL=
echo   (en Vercel: tu proyecto, Storage, la base, pestana ".env.local",
echo   la linea que empieza con DATABASE_URL=, sin las comillas).
echo   Guarda con Ctrl+S y cerra.
echo.
pause
notepad ".env"
echo.
echo Listo. Ahora doble clic en 8_arrancar_con_windows.bat
echo.
pause
