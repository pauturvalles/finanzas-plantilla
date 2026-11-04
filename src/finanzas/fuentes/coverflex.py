"""Exports CSV de Coverflex (DECISIONES §5).

Se pueden dejar varios exports solapados en la carpeta de entrada: los ids son
deterministas, así que el mismo pago nunca entra dos veces.
"""
from __future__ import annotations

import io

import pandas as pd

TIPOS = {"Spanish Meal Card": "Comida", "Spanish Public Transport Card": "Transporte"}


def _producto(texto: str) -> str | None:
    t = str(texto).lower()
    if any(k in t for k in ("meal", "comida", "refei", "restaura", "food")):
        return "Comida"
    if "transport" in t:
        return "Transporte"
    return None


def _formato_movimientos(c: pd.DataFrame) -> pd.DataFrame:
    """Export «de movimientos» (2025): id, date, type, status, merchant, amount, is_debit, category, product…"""
    estado = c["status"].fillna("").str.lower()
    c = c[~estado.str.contains("reject|declin|fail|cancel|rechaz|anul")]
    debito = c["is_debit"].astype(str).str.lower().isin(["true", "1", "yes", "sí", "si"]) if "is_debit" in c.columns else c["amount"].astype(float) < 0
    c = c[debito & (c["merchant"].fillna("").str.strip() != "")]
    tipo = c["product"].map(_producto).fillna(c["category"].map(_producto)) if "product" in c.columns else c["category"].map(_producto)
    c, tipo = c[tipo.notna()], tipo[tipo.notna()]
    return pd.DataFrame({
        "cuenta": "COVERFLEX",
        "fecha": pd.to_datetime(c["date"].astype(str).str[:10]).dt.strftime("%Y-%m-%d"),
        "concepto": c["merchant"].str.strip(),
        "contraparte": "",
        "iban_contraparte": "",
        "importe": -c["amount"].astype(float).abs().round(2),
        "moneda": c["currency"].fillna("EUR") if "currency" in c.columns else "EUR",
        "tipo_origen": tipo,
        "fuente": "coverflex_csv",
        "ref": c["id"].astype(str),
    })


def leer(contenido: bytes | str) -> pd.DataFrame:
    if isinstance(contenido, bytes):
        contenido = contenido.decode("utf-8-sig")
    c = pd.read_csv(io.StringIO(contenido))
    c.columns = [x.strip().lstrip("\ufeff") for x in c.columns]
    if {"merchant", "amount", "date", "status"} <= set(c.columns):
        return _formato_movimientos(c)
    necesarias = {"name_category", "user_description", "transaction_date", "value"}
    if not necesarias <= set(c.columns):
        raise ValueError(f"formato de Coverflex desconocido; columnas: {', '.join(c.columns[:15])}")
    if "pocket_type" in c.columns:                                     # los exports antiguos no la traen
        c = c[c["pocket_type"] != "holding"]                          # duplicados de transporte
    c = c[c["name_category"].fillna("").str.strip() != ""]            # líneas sin comercio
    c = c[c["user_description"].isin(TIPOS)]
    return pd.DataFrame({
        "cuenta": "COVERFLEX",
        "fecha": pd.to_datetime(c["transaction_date"].str[:10]).dt.strftime("%Y-%m-%d"),
        "concepto": c["name_category"].str.strip(),
        "contraparte": "",
        "iban_contraparte": "",
        "importe": -c["value"].astype(float).round(2),
        "moneda": "EUR",
        "tipo_origen": c["user_description"].map(TIPOS),
        "fuente": "coverflex_csv",
        # La referencia del pago es estable entre exports.
        "ref": c["reference"].astype(str) if "reference" in c.columns else "",
    })
