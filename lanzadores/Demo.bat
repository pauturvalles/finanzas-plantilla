@echo off
rem Abre la DEMO del dashboard (datos inventados de demo/, sin hoja ni claves) en http://localhost:8501.
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo No encuentro el entorno .venv. Crealo con: py -3.12 -m venv .venv
  pause
  exit /b 1
)
title Demo del dashboard
set FINANZAS_DEMO=1
".venv\Scripts\python.exe" -m streamlit run dashboard\app.py
pause
