@echo off
rem Abre una consola en la carpeta del proyecto con el entorno virtual ya activado.
rem Desde ahi puedes escribir directamente, por ejemplo:
rem   python scripts\probar_vl.py
rem   python scripts\reconstruir_historico.py C:\ruta\historico_patrimonio --sin-subir
rem   python -m finanzas.pipeline
rem   pytest
cd /d "%~dp0.."
if not exist ".venv\Scripts\activate.bat" (
  echo No encuentro el entorno .venv. Crealo con: py -3.12 -m venv .venv
  echo y luego: .venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)
title Consola de finanzas
set PYTHONPATH=src
set PYTHONUTF8=1
cmd /k ".venv\Scripts\activate.bat && echo Entorno activado. Escribe el comando que te hayan dado, sin mas."
