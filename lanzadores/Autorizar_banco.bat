@echo off
rem Autoriza (o renueva) la conexion de un banco en Enable Banking y muestra el uid de cada cuenta.
cd /d "%~dp0.."
title Autorizar banco
set /p BANCO="Nombre exacto del banco (p. ej. Banco Sabadell; Enter para ver la lista): "
if "%BANCO%"=="" (
  ".venv\Scripts\python.exe" -X utf8 scripts\autorizar_banco.py --listar
) else (
  ".venv\Scripts\python.exe" -X utf8 scripts\autorizar_banco.py "%BANCO%"
)
echo.
pause
