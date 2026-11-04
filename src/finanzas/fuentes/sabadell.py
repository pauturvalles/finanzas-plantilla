"""Export Excel de movimientos del Sabadell (web → Cuentas → Consulta de movimientos → Excel).

Mientras la conexión del Sabadell en Enable Banking tenga incidencias. La cuenta se
identifica por el IBAN de la cabecera (comparado con IBAN_SAB_CORR / IBAN_SAB_AHO) o,
si no, por el nombre del fichero («corriente» / «ahorro»).
"""
from __future__ import annotations

import io
import os

import pandas as pd


def _cuenta(cabecera: str, nombre_fichero: str) -> str:
    c = cabecera.replace(" ", "").upper()
    for cuenta, var in (("SAB_CORR", "IBAN_SAB_CORR"), ("SAB_AHO", "IBAN_SAB_AHO")):
        iban = os.environ.get(var, "").replace(" ", "").upper()
        if iban and c and (iban.startswith(c) or c.startswith(iban) or c[-4:] == iban[-4:]):
            return cuenta
    n = nombre_fichero.lower()
    if "ahorro" in n:
        return "SAB_AHO"
    if "corriente" in n:
        return "SAB_CORR"
    raise ValueError(f"No sé de qué cuenta del Sabadell es «{nombre_fichero}»: pon «corriente» o «ahorro» en el nombre.")


def leer(contenido: bytes, nombre_fichero: str = "") -> pd.DataFrame:
    bruto = pd.read_excel(io.BytesIO(contenido), header=None, dtype=object)
    fila_cab = bruto.index[bruto[0].astype(str).str.strip().eq("F. Operativa")][0]
    cuenta_txt = str(bruto.loc[bruto[0].astype(str).str.startswith("Cuenta"), 1].iloc[0]) if bruto[0].astype(str).str.startswith("Cuenta").any() else ""
    cuenta = _cuenta(cuenta_txt, nombre_fichero)
    x = bruto.iloc[fila_cab + 1:].copy()
    x.columns = [str(c).strip() for c in bruto.iloc[fila_cab]]
    x = x[x["F. Operativa"].notna()]
    return pd.DataFrame({
        "cuenta": cuenta,
        "fecha": pd.to_datetime(x["F. Operativa"], dayfirst=True).dt.strftime("%Y-%m-%d"),
        "concepto": x["Concepto"].astype(str).str.strip(),
        "contraparte": "",
        "iban_contraparte": "",
        "importe": pd.to_numeric(x["Importe"]).round(2),
        "moneda": "EUR",
        "tipo_origen": "",
        "fuente": "sabadell_xls",
    })


def ultimo_saldo(contenido: bytes, nombre_fichero: str = "") -> dict | None:
    """Saldo tras el movimiento más reciente del Excel (para el patrimonio mientras no haya API)."""
    bruto = pd.read_excel(io.BytesIO(contenido), header=None, dtype=object)
    fila_cab = bruto.index[bruto[0].astype(str).str.strip().eq("F. Operativa")][0]
    cuenta_txt = str(bruto.loc[bruto[0].astype(str).str.startswith("Cuenta"), 1].iloc[0]) if bruto[0].astype(str).str.startswith("Cuenta").any() else ""
    x = bruto.iloc[fila_cab + 1:].copy()
    x.columns = [str(c).strip() for c in bruto.iloc[fila_cab]]
    x = x[x["F. Operativa"].notna()]
    if x.empty or "Saldo" not in x.columns:
        return None
    x["_f"] = pd.to_datetime(x["F. Operativa"], dayfirst=True)
    ultimos = x[x["_f"] == x["_f"].max()]
    descendente = x["_f"].iloc[0] >= x["_f"].iloc[-1]  # el Excel suele venir del más reciente al más antiguo
    u = ultimos.iloc[0] if descendente else ultimos.iloc[-1]
    return {"cuenta": _cuenta(cuenta_txt, nombre_fichero), "tipo": "XLS", "fecha": u["_f"].strftime("%Y-%m-%d"), "saldo": float(u["Saldo"])}
