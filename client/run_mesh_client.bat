@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw mesh_client.py
    exit /b 0
)

python mesh_client.py
if errorlevel 1 (
    echo.
    echo ERROR: No se pudo iniciar. Asegurate de tener Python 3.9+ instalado.
    pause
)
