@echo off
rem Abre el dashboard en el navegador (http://localhost:8501). Cierra esta ventana para pararlo.
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo No encuentro el entorno .venv. Crealo con: py -3.12 -m venv .venv
  pause
  exit /b 1
)
if not exist ".streamlit\secrets.toml" (
  echo Falta .streamlit\secrets.toml: generalo con  python -X utf8 scripts\secretos_streamlit.py
  pause
  exit /b 1
)
title Dashboard de finanzas
".venv\Scripts\python.exe" -m streamlit run dashboard\app.py
pause
