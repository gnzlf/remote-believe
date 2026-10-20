@echo off
setlocal
title Remote Believe - Solo diagnostico
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Reparar-RemoteBelieve.ps1" -SoloDiagnostico
echo.
pause
endlocal
