"""Último valor liquidativo publicado de un fondo, por ISIN.

Fuente: ficha pública del Financial Times (markets.ft.com), que acepta «ISIN:EUR».
No es una API oficial: si cambian la página, `ultimo_vl` devolverá None y el pipeline
lo anotará en `registro`; mientras tanto se puede rellenar a mano la pestaña
`valores_liquidativos`. Prueba rápida: `python scripts/probar_vl.py`.
"""
from __future__ import annotations

import re
from datetime import datetime

import requests

URL = "https://markets.ft.com/data/funds/tearsheet/summary?s={isin}:EUR"
CABECERAS = {"User-Agent": "Mozilla/5.0 (finanzas personales; uso privado)"}


def ultimo_vl(isin: str) -> dict | None:
    """{'isin', 'fecha' (YYYY-MM-DD), 'vl', 'fuente'} o None si no se encuentra."""
    r = requests.get(URL.format(isin=isin), headers=CABECERAS, timeout=30)
    if r.status_code != 200:
        return None
    precio = re.search(r'mod-ui-data-list__value">\s*([0-9][0-9.,]*)\s*<', r.text)
    fecha = re.search(r"as of ([A-Z][a-z]{2} \d{1,2} \d{4})", r.text)
    if not precio:
        return None
    vl = float(precio.group(1).replace(",", ""))
    f = datetime.strptime(fecha.group(1), "%b %d %Y").strftime("%Y-%m-%d") if fecha else datetime.now().strftime("%Y-%m-%d")
    return {"isin": isin, "fecha": f, "vl": vl, "fuente": "ft.com"}
