@echo off
setlocal
title Remote Believe - Soporte del Explorador
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Reparar-RemoteBelieve.ps1"
echo.
pause
endlocal
