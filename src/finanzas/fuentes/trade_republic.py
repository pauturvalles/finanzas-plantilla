"""Extracto de cuenta de Trade Republic en PDF (DECISIONES §1).

Enable Banking solo devuelve importe y fecha para Trade Republic, sin comercio ni
concepto, así que los movimientos salen del PDF que genera la app
(Perfil → Documentos / extracto de cuenta) y Enable Banking se usa solo para el saldo.

El signo de cada movimiento se deduce de la variación del saldo, que viene en cada
línea: así no dependemos de en qué columna cae el importe al extraer el texto.
"""
from __future__ import annotations

import io
import re

import pandas as pd
import pdfplumber

MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
         "sept": 9, "sep": 9, "oct": 10, "nov": 11, "dic": 12}
TIPOS = ["Transacción con tarjeta", "Transferencia", "Interés", "Rentabilidad", "Regalo",
         "Operar", "Comisión", "Bonificación"]
RE_FECHA = re.compile(r"^\s*(\d{2}) (ene|feb|mar|abr|may|jun|jul|ago|sept|sep|oct|nov|dic)\b(.*)$")
RE_ANO = re.compile(r"^\s*(\d{4})\b(.*)$")
RE_EUR = re.compile(r"(-?\d{1,3}(?:\.\d{3})*,\d{2}) €")
FIN = ("Trade Republic Bank GmbH", "RESUMEN DEL BALANCE", "NOTAS SOBRE", "Creado en")


def _num(s: str) -> float:
    return float(s.replace(".", "").replace(",", "."))


def leer(contenido: bytes) -> pd.DataFrame:
    with pdfplumber.open(io.BytesIO(contenido)) as pdf:
        lineas = [l for p in pdf.pages for l in (p.extract_text(layout=True) or "").splitlines()]
    texto = "\n".join(lineas)
    m = re.search(r"Cuenta corriente\s+" + RE_EUR.pattern, texto)
    if not m:
        raise ValueError("No encuentro el balance inicial: ¿es un extracto de cuenta de Trade Republic?")
    saldo = _num(m.group(1))

    bloques, actual = [], None
    for l in lineas:
        f = RE_FECHA.match(l)
        if f:
            if actual:
                bloques.append(actual)
            actual = {"d": int(f.group(1)), "m": MESES[f.group(2)], "ano": None, "txt": [f.group(3)]}
            continue
        if actual is None:
            continue
        if any(x in l for x in FIN):
            bloques.append(actual); actual = None
            continue
        a = RE_ANO.match(l)
        if a and actual["ano"] is None:
            actual["ano"] = int(a.group(1)); actual["txt"].append(a.group(2))
        else:
            actual["txt"].append(l)
    if actual:
        bloques.append(actual)

    filas = []
    for b in bloques:
        t = re.sub(r"\s+", " ", " ".join(b["txt"])).strip()
        imp = RE_EUR.findall(t)
        if len(imp) < 2 or b["ano"] is None:
            continue
        valor, nuevo = _num(imp[-2]), _num(imp[-1])
        delta = round(nuevo - saldo, 2)
        signo = 1 if abs(delta - valor) < 0.011 else -1 if abs(delta + valor) < 0.011 else 0
        if signo == 0:
            raise ValueError(f"El saldo no cuadra en: {t[:80]}")
        saldo = nuevo
        desc = RE_EUR.sub("", t)
        # En el PDF el tipo ocupa dos líneas y se intercala con la descripción
        # ("Transacción TROPICO BCN con tarjeta"): se detecta y se quita donde esté.
        if "Transacción" in desc and "con tarjeta" in desc:
            tipo, desc = "Transacción con tarjeta", desc.replace("Transacción", "").replace("con tarjeta", "")
        else:
            tipo = next((x for x in TIPOS[1:] if re.search(rf"\b{x}\b", desc)), "")
            if tipo:
                desc = re.sub(rf"\b{tipo}\b", "", desc, count=1)
        desc = re.sub(r"\s+", " ", desc).strip()
        filas.append({"cuenta": "TR", "fecha": f"{b['ano']:04d}-{b['m']:02d}-{b['d']:02d}", "concepto": desc,
                      "contraparte": "", "iban_contraparte": (re.search(r"\(([A-Z]{2}\d{2}[0-9A-Z]{10,30})\)", desc) or [None, ""])[1],
                      "importe": round(signo * valor, 2), "moneda": "EUR", "tipo_origen": tipo,
                      "fuente": "trade_republic_pdf", "saldo_tras": nuevo})
    df = pd.DataFrame(filas)
    # Referencia estable: fecha + importe + saldo resultante identifica cada apunte aunque
    # dos extractos se solapen.
    df["ref"] = df["fecha"] + "|" + df["importe"].map(lambda v: f"{v:.2f}") + "|" + df["saldo_tras"].map(lambda v: f"{v:.2f}")
    return df.drop(columns="saldo_tras")
