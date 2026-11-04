"""Extracto CSV de Revolut (app → cuenta → Extractos → Excel/CSV), para el pasado que Enable
Banking no trae. La cuenta se reconoce por el nombre del fichero: con «conjunta» → REV_CONJ;
si no → REV_PERS. Solo la subcuenta principal («Actual»); los movimientos con las huchas son
traspasos internos.

Cabecera: Tipo,Producto,Fecha de inicio,Fecha de finalización,Descripción,Importe,Comisión,Divisa,State,Saldo
"""
from __future__ import annotations

import io

import pandas as pd


def es_export(contenido: bytes) -> bool:
    return contenido[:60].decode("utf-8-sig", errors="ignore").startswith("Tipo,Producto,Fecha de inicio")


def leer(contenido: bytes, nombre_fichero: str = "") -> pd.DataFrame:
    t = pd.read_csv(io.BytesIO(contenido), dtype=str, keep_default_na=False)
    t = t[t["State"].str.upper().isin(["COMPLETADO", "COMPLETED"]) & t["Producto"].isin(["Actual", "Current"])]
    importe = pd.to_numeric(t["Importe"], errors="coerce").fillna(0) - pd.to_numeric(t["Comisión"], errors="coerce").fillna(0)
    t = t[importe.round(2) != 0]
    importe = importe[t.index]
    cuenta = "REV_CONJ" if "conjunta" in nombre_fichero.lower() else "REV_PERS"
    return pd.DataFrame({
        "cuenta": cuenta,
        "fecha": pd.to_datetime(t["Fecha de inicio"]).dt.strftime("%Y-%m-%d"),
        "concepto": t["Descripción"].str.strip(),
        "contraparte": "",
        "iban_contraparte": "",
        "importe": importe.round(2),
        "moneda": t["Divisa"].replace("", "EUR"),
        "tipo_origen": t["Tipo"],
        "fuente": "revolut_csv",
    })
