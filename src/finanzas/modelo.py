"""Esquema común de movimientos.

Todas las fuentes se normalizan a estas columnas (capa "raw"). La clave `tx_id` es
determinista: el mismo movimiento produce siempre el mismo id, así que volver a
descargar o reprocesar nunca duplica filas.
"""
from __future__ import annotations

import hashlib

import pandas as pd

COLUMNAS_RAW = [
    "tx_id",        # clave estable
    "cuenta",       # id de config/cuentas.yaml (SAB_CORR, REV_CONJ, COVERFLEX…)
    "fecha",        # fecha de operación, YYYY-MM-DD
    "concepto",     # texto libre del banco
    "contraparte",  # nombre del ordenante/beneficiario si lo hay
    "iban_contraparte",
    "importe",      # negativo = sale dinero
    "moneda",
    "tipo_origen",  # tipo que da la fuente (p. ej. 'Comida', 'Transporte' en Coverflex)
    "fuente",       # enable_banking, coverflex_csv, historico…
]

COLUMNAS_MOV = COLUMNAS_RAW + [
    "mes",              # mes al que se imputa (la nómina de los días 1–5 va al mes anterior)
    "flujo",            # Gasto, Ingreso, Traspaso interno, Ahorro / inversión, Bizum personas, Compensación de compra
    "categoria",
    "subcategoria",
    "comercio",         # nombre limpio del comercio (contraparte o concepto)
    "tipo_gasto",       # Fijo, Variable, Extraordinario
    "naturaleza",       # Esencial, Discrecional, Extraordinario
    "pct_imputado",
    "importe_imputado",
    "regla",            # qué regla o ajuste decidió la clasificación
    "nota",
]


def tx_id(cuenta: str, fecha: str, importe: float, concepto: str,
          ref: str | None = None, ocurrencia: int = 0) -> str:
    """Id determinista. Si la fuente da una referencia propia, se usa; si no, un hash de
    los campos que identifican el movimiento más el número de ocurrencia (dos cargos
    idénticos el mismo día son movimientos distintos)."""
    if ref:
        base = f"{cuenta}|{ref}"
    else:
        base = f"{cuenta}|{fecha}|{importe:.2f}|{concepto.strip().upper()}|{ocurrencia}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def asignar_ids(df: pd.DataFrame) -> pd.DataFrame:
    """Calcula `tx_id` para filas sin referencia propia, numerando las repeticiones
    exactas en el orden en que llegan de la fuente."""
    df = df.copy()
    df["_oc"] = df.groupby(["cuenta", "fecha", "importe", "concepto"]).cumcount()
    ref = df["ref"] if "ref" in df.columns else pd.Series([None] * len(df), index=df.index)
    df["tx_id"] = [
        tx_id(c, f, float(i), str(t), r if isinstance(r, str) and r else None, int(o))
        for c, f, i, t, r, o in zip(df.cuenta, df.fecha, df.importe, df.concepto, ref, df._oc)
    ]
    return df.drop(columns=[c for c in ("_oc", "ref") if c in df.columns])


def vacio_raw() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNAS_RAW)


def upsert(actual: pd.DataFrame, nuevos: pd.DataFrame) -> pd.DataFrame:
    """Une por `tx_id`: lo nuevo sustituye a lo existente con el mismo id.
    Ejecutarlo dos veces con los mismos datos da el mismo resultado."""
    if actual is None or actual.empty:
        res = nuevos.copy()
    else:
        res = pd.concat([actual[~actual.tx_id.isin(nuevos.tx_id)], nuevos], ignore_index=True)
    return res.drop_duplicates("tx_id", keep="last").sort_values(["fecha", "cuenta", "tx_id"]).reset_index(drop=True)


# Si una misma cuenta llega por varias fuentes (p. ej. Sabadell por Excel y, cuando vuelva
# a funcionar, por Enable Banking), en cada día manda la fuente con más prioridad.
PRIORIDAD_FUENTES = ["enable_banking", "trade_republic_csv", "trade_republic_pdf", "myinvestor_csv", "coverflex_csv", "sabadell_xls", "revolut_csv"]


# Excepciones por cuenta: en Trade Republic, Enable Banking no trae el comercio, así que sus
# movimientos son provisionales y los sustituye el CSV (o el PDF) en cuanto cubre ese día.
PRIORIDAD_POR_CUENTA = {"TR": ["trade_republic_csv", "trade_republic_pdf", "enable_banking"]}


def resolver_fuentes(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Quita los movimientos de fuentes secundarias en los días en que una fuente con más
    prioridad ya trae datos de esa cuenta. Devuelve (raw limpio, filas descartadas)."""
    if raw.empty:
        return raw, raw.iloc[0:0]
    rango = {f: i for i, f in enumerate(PRIORIDAD_FUENTES)}
    def prio(cuenta, fuente):
        orden = PRIORIDAD_POR_CUENTA.get(cuenta)
        if orden:
            return orden.index(fuente) if fuente in orden else len(orden)
        return rango.get(fuente, len(rango))
    r = raw.assign(_p=[prio(c, f) for c, f in zip(raw["cuenta"], raw["fuente"])])
    mejor = r.groupby(["cuenta", "fecha"])["_p"].transform("min")
    quitar = r["_p"] > mejor
    # Cuentas con fuente provisional (Trade Republic por Enable Banking): el fichero cubre todos los
    # días hasta su última fecha, y la fecha contable de la API puede ir 1–3 días por detrás de la
    # de la operación. Se quitan los provisionales hasta el último día del fichero y, en los días
    # siguientes, los que tengan una operación del fichero del mismo importe a ≤ 4 días.
    for cuenta, orden in PRIORIDAD_POR_CUENTA.items():
        prov = orden[-1]
        fich = r[(r["cuenta"] == cuenta) & (r["fuente"] != prov)]
        if fich.empty:
            continue
        hasta = fich["fecha"].max()
        p_ = (r["cuenta"] == cuenta) & (r["fuente"] == prov)
        quitar |= p_ & (r["fecha"] <= hasta)
        f_fich = pd.to_datetime(fich["fecha"])
        usados = set()
        for i in r.index[p_ & (r["fecha"] > hasta)]:
            d = (pd.to_datetime(r.at[i, "fecha"]) - f_fich).dt.days
            c = fich[((fich["importe"].astype(float) - float(r.at[i, "importe"])).abs() < 0.005) & d.between(0, 4) & ~fich.index.isin(usados)]
            if len(c):
                usados.add(c.index[0])
                quitar.at[i] = True
    return r.loc[~quitar].drop(columns="_p"), r.loc[quitar].drop(columns="_p")


def posibles_duplicados(raw: pd.DataFrame) -> pd.DataFrame:
    """Movimientos de la misma cuenta, día e importe que llegan de fuentes distintas, o con
    conceptos casi iguales: para revisar a mano (no se borra nada automáticamente)."""
    if raw.empty:
        return raw
    g = raw.groupby(["cuenta", "fecha", "importe"])
    dup = raw[g["tx_id"].transform("count") > 1]
    return dup[dup.groupby(["cuenta", "fecha", "importe"])["fuente"].transform("nunique") > 1].sort_values(["cuenta", "fecha", "importe"])
