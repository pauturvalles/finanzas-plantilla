"""Export CSV de la cuenta de efectivo de MyInvestor (web → Mi cuenta → movimientos).

Columnas: Fecha de operación;Fecha de valor;Concepto;Importe;Divisa (separador «;»,
fechas dd/mm/aaaa, decimales con coma). Las suscripciones llevan como concepto el nombre
del fondo; su fecha de valor es el día cuyo valor liquidativo se aplica, y se guarda en
`tipo_origen` como «valor=AAAA-MM-DD» para la valoración (patrimonio.py).
"""
from __future__ import annotations

import io

import pandas as pd


def es_export_myinvestor(contenido: bytes) -> bool:
    cab = contenido[:200].decode("utf-8-sig", errors="ignore")
    return cab.startswith("Fecha de operaci") and "Fecha de valor;Concepto;Importe" in cab


def leer(contenido: bytes) -> pd.DataFrame:
    c = pd.read_csv(io.StringIO(contenido.decode("utf-8-sig")), sep=";", dtype=str, keep_default_na=False)
    c.columns = ["fecha_op", "fecha_valor", "concepto", "importe", "divisa"][: len(c.columns)]
    c["importe"] = c["importe"].str.replace(".", "", regex=False).str.replace(",", ".", regex=False).astype(float)
    c = c[(c["importe"] != 0) | (c["concepto"].str.strip() != "")]
    c = c[c["importe"] != 0]
    fop = pd.to_datetime(c["fecha_op"], dayfirst=True)
    fval = pd.to_datetime(c["fecha_valor"], dayfirst=True)
    return pd.DataFrame({
        "cuenta": "MYINV",
        "fecha": fop.dt.strftime("%Y-%m-%d"),
        "concepto": c["concepto"].str.strip(),
        "contraparte": "",
        "iban_contraparte": "",
        "importe": c["importe"].round(2),
        "moneda": c.get("divisa", "EUR"),
        "tipo_origen": "valor=" + fval.dt.strftime("%Y-%m-%d"),
        "fuente": "myinvestor_csv",
    })
