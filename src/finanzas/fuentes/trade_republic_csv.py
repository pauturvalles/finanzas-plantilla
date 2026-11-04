"""Export CSV de transacciones de Trade Republic (app → Perfil → Actividad / Exportar).

Mejor que el extracto PDF: trae el comercio, el IBAN de la contraparte, una referencia
estable (`transaction_id`) y el detalle de cada compra de valores (ISIN, títulos, precio,
comisión e impuestos). De aquí salen tanto los movimientos de efectivo como la cartera.
"""
from __future__ import annotations

import io

import pandas as pd

CABECERA = '"datetime","date","account_type"'


def es_export(contenido: bytes) -> bool:
    return contenido[:200].decode("utf-8-sig", errors="ignore").lstrip().startswith(CABECERA)


def _leer(contenido: bytes) -> pd.DataFrame:
    t = pd.read_csv(io.StringIO(contenido.decode("utf-8-sig")), dtype=str, keep_default_na=False)
    for c in ("amount", "fee", "tax", "shares", "price"):
        t[c] = pd.to_numeric(t[c].replace("", None), errors="coerce")
    return t


def _concepto(r) -> str:
    tipo, sym, nom, sh = r["type"], r["symbol"], r["name"], r["shares"]
    if r["category"] == "TRADING":
        accion = "Sell trade" if tipo == "SELL" or (pd.notna(sh) and sh < 0) else "Buy trade"
        return f"{accion} {sym} {nom}, quantity: {abs(sh):.6f}"
    if tipo == "DIVIDEND":
        return f"Cash Dividend for ISIN {sym} {nom}".strip()
    if tipo == "BENEFITS_SAVEBACK":
        return "Saveback cash reward"
    if tipo == "STOCKPERK":
        return f"Stockperk {nom}".strip()
    if tipo.startswith("CARD_TRANSACTION"):
        return (r["description"] or r["name"]).strip()
    return (r["description"] or r["name"] or tipo).strip()


def movimientos(contenido: bytes) -> pd.DataFrame:
    """Movimientos de efectivo (esquema raw). Importe = amount + comisión + impuestos."""
    t = _leer(contenido)
    t["efectivo"] = t["amount"].fillna(0) + t["fee"].fillna(0) + t["tax"].fillna(0)
    t = t[t["efectivo"].round(2) != 0]
    return pd.DataFrame({
        "cuenta": "TR",
        "fecha": t["date"],
        "concepto": t.apply(_concepto, axis=1),
        "contraparte": t["counterparty_name"],
        "iban_contraparte": t["counterparty_iban"],
        "importe": t["efectivo"].round(2),
        "moneda": t["currency"].replace("", "EUR"),
        "tipo_origen": t["type"],
        "fuente": "trade_republic_csv",
        "ref": t["transaction_id"],
    })


def valores(contenido: bytes) -> pd.DataFrame:
    """Operaciones con valores: compras, ventas, desdoblamientos y traspasos internos (MIGRATION)."""
    t = _leer(contenido)
    t = t[t["category"].isin(["TRADING", "DELIVERY", "CORPORATE_ACTION"]) & (t["symbol"] != "")]
    return pd.DataFrame({
        "fecha": t["date"], "isin": t["symbol"].str.upper(), "nombre": t["name"], "tipo": t["type"],
        "cantidad": t["shares"], "precio": t["price"],
        # Coste de una compra con comisión e impuestos; en una venta, lo cobrado neto.
        "importe": (t["amount"].fillna(0) + t["fee"].fillna(0) + t["tax"].fillna(0)).abs(),
        "id": t["transaction_id"],
    })
