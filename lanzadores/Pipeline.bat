@echo off
rem Lanza el pipeline completo (bancos, ficheros de Drive e inversiones). Lee las claves del .env.
cd /d "%~dp0.."
title Pipeline de finanzas
set PYTHONPATH=src
set /p MODO="Escribe R para solo reclasificar (sin bancos) o pulsa Enter para todo: "
if /i "%MODO%"=="R" (
  ".venv\Scripts\python.exe" -X utf8 -m finanzas.pipeline --sin-bancos
) else (
  ".venv\Scripts\python.exe" -X utf8 -m finanzas.pipeline
)
echo.
pause
