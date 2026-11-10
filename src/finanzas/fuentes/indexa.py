"""Indexa Capital: API oficial de solo lectura (token personal).

Token: indexacapital.com → Configuración de usuario → Aplicaciones →
«Token estrictamente personal de acceso a la API de Indexa».
Comprueba los nombres de campo con la primera respuesta real (`python -m finanzas.fuentes.indexa`).
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd
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


CATEGORIA_CUENTA = {"mutual": "Fondos Indexa", "pension": "Plan de pensiones Indexa"}


def rentabilidad_mensual() -> list[dict]:
    """Índice de rentabilidad ponderada en el tiempo que calcula Indexa (neto de comisiones, con su efectivo):
    `/accounts/{n}/performance` → return.index, diario ({"20210119": valor, …}). Se guarda el último valor de
    cada mes; con él, el dashboard da exactamente las rentabilidades mensuales y anuales de la web de Indexa."""
    filas = []
    for cta in _get("/users/me").get("accounts", []):
        idx = ((_get(f"/accounts/{cta.get('account_number')}/performance").get("return") or {}).get("index")) or {}
        if not idx:
            continue
        s = pd.Series({pd.Timestamp(str(k)): float(v) for k, v in idx.items() if v is not None}).sort_index()
        ult = s.groupby(s.index.to_period("M")).tail(1)
        cat = CATEGORIA_CUENTA.get(cta.get("type"), cta.get("type") or "Indexa")
        filas += [{"categoria": cat, "fecha": f.strftime("%Y-%m-%d"), "indice": round(v, 8)} for f, v in ult.items()]
    return filas


if __name__ == "__main__":  # inspección manual de la respuesta
    from dotenv import load_dotenv
    load_dotenv()
    json.dump(_get("/users/me"), sys.stdout, indent=1, ensure_ascii=False)
