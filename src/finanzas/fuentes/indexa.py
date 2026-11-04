"""Indexa Capital: API oficial de solo lectura (token personal).

Token: indexacapital.com → Configuración de usuario → Aplicaciones →
«Token estrictamente personal de acceso a la API de Indexa».
Comprueba los nombres de campo con la primera respuesta real (`python -m finanzas.fuentes.indexa`).
"""
from __future__ import annotations

import json
import os
import sys

import requests

API = "https://api.indexacapital.com"


def _get(path: str) -> dict:
    r = requests.get(API + path, headers={"X-AUTH-TOKEN": os.environ["INDEXA_TOKEN"]}, timeout=60)
    r.raise_for_status()
    return r.json()


PRODUCTOS = {"mutual": "Fondos indexados", "pension": "Plan de pensiones"}


def posiciones() -> list[dict]:
    """Una fila por cuenta de Indexa (fondos y plan de pensiones).

    valor    = portfolio.total_amount (fondos + efectivo de la cuenta)
    aportado = entradas − salidas de dinero (lo que has metido neto)
    """
    filas = []
    for cta in _get("/users/me").get("accounts", []):
        num = cta.get("account_number")
        port = _get(f"/accounts/{num}/portfolio").get("portfolio", {})
        entradas, salidas = float(port.get("inflows") or 0), float(port.get("outflows") or 0)
        filas.append({
            "plataforma": "Indexa Capital",
            "producto": PRODUCTOS.get(cta.get("type"), cta.get("type") or "cuenta"),
            "referencia": num,
            "valor": round(float(port.get("total_amount") or 0), 2),          # fondos + efectivo de la cuenta
            "valor_fondos": round(float(port.get("instruments_amount") or 0), 2),  # solo fondos (lo comparable con el coste)
            "efectivo": round(float(port.get("cash_amount") or 0), 2),
            "fecha_valor": port.get("date", ""),
            "aportado": round(entradas - salidas, 2) if entradas else None,
            "metodo": f"api Indexa, valoración del {port.get('date', '')}",
        })
    return filas


def posiciones_detalle() -> list[dict]:
    """Una fila por fondo de cada cuenta: participaciones, valor y coste según Indexa."""
    filas = []
    for cta in _get("/users/me").get("accounts", []):
        num = cta.get("account_number")
        r = _get(f"/accounts/{num}/portfolio")
        for ia in r.get("instrument_accounts", []):
            for p in ia.get("positions", []):
                ins = p.get("instrument") or {}
                filas.append({
                    "plataforma": "Indexa Capital", "cuenta": PRODUCTOS.get(cta.get("type"), cta.get("type")),
                    "activo": (ins.get("isin_code") or ins.get("identifier") or "").upper(),
                    "nombre": ins.get("name") or ins.get("description") or "",
                    "cantidad": float(p.get("titles") or 0), "valor": float(p.get("amount") or 0),
                    "coste": float(p.get("cost_amount") or 0), "fecha": p.get("date", ""),
                })
    return filas


if __name__ == "__main__":  # inspección manual de la respuesta
    from dotenv import load_dotenv
    load_dotenv()
    json.dump(_get("/users/me"), sys.stdout, indent=1, ensure_ascii=False)
