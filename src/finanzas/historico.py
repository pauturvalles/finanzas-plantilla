"""Reconstrucción del valor del patrimonio mes a mes desde el primer dato (DECISIONES §7 quater).

valor(mes) = Σ participaciones(mes) × precio(mes) + saldos de las cuentas (conjunta al 50 %).

- Participaciones: operaciones de Portfolio Performance hasta su última fecha; después, se
  interpola linealmente hasta las participaciones oficiales de hoy (salvo Trade Republic, que
  sale exacto de su CSV).
- Precios: CSV de precios históricos de PP (fondos) y velas diarias públicas de Binance (cripto).
- Saldos: Excel del Sabadell y CSV de Revolut (columna de saldo); Trade Republic, hacia atrás
  desde el saldo de hoy con su CSV.
"""
from __future__ import annotations

import io
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ES_FONDO = re.compile(r"^([A-Z]{2}[A-Z0-9]{9}\d|0P|N\d{4}$)")
ESTABLES_USD = {"USDT", "USDC", "BUSD", "TUSD", "DAI", "USDG", "FDUSD", "USDP"}


# ------------------------------------------------------------------ precios
def leer_precios_pp(carpeta: Path) -> dict[str, pd.Series]:
    """CSV «Fecha;Cotización» de PP, uno por valor; el nombre del fichero es el ISIN o código."""
    out = {}
    for f in sorted(Path(carpeta).glob("*.csv")):
        t = f.read_text(encoding="utf-8-sig").splitlines()
        if not t or not t[0].lower().startswith("fecha;"):
            continue
        df = pd.read_csv(io.StringIO("\n".join(t)), sep=";", dtype=str)
        s = pd.Series(pd.to_numeric(df.iloc[:, 1].str.replace(".", "", regex=False).str.replace(",", ".", regex=False), errors="coerce").values,
                      index=pd.to_datetime(df.iloc[:, 0], errors="coerce")).dropna().sort_index()
        out[f.stem.upper()] = s[~s.index.duplicated()]
    return out


def identificar_sin_nombre(precios: dict[str, pd.Series], ops: pd.DataFrame, claves: list[str]) -> dict[str, tuple[str, float]]:
    """Para ficheros sin ISIN (p. ej. «null.csv»), busca el valor de PP cuyos precios de compra
    (importe ÷ cantidad) se parecen más a esa serie. Devuelve {fichero: (activo, error relativo)}."""
    res = {}
    compras = ops[ops["tipo"].isin(["compra", "venta"]) & (ops["cantidad"].abs() > 0)].copy()
    compras["precio"] = compras["importe"].abs() / compras["cantidad"].abs()
    compras["fecha"] = pd.to_datetime(compras["fecha"])
    con_fichero = set(precios)
    for clave in claves:
        serie, mejor = precios[clave], (None, np.inf)
        for activo, g in compras.groupby("activo"):
            if activo in con_fichero or not ES_FONDO.match(str(activo)):
                continue
            p = serie.reindex(g["fecha"], method="ffill").values
            ok = ~np.isnan(p)
            if ok.sum() < 3:
                continue
            err = float(np.median(np.abs(g["precio"].values[ok] / p[ok] - 1)))
            if err < mejor[1]:
                mejor = (activo, err)
        if mejor[0]:
            res[clave] = mejor
    return res


def precios_binance(activo: str, desde: pd.Timestamp) -> pd.Series:
    """Cierre diario en EUR de una cripto con la API pública de Binance (sin cuenta).
    Prueba el par contra EUR; si no existe, contra USDT y convierte con EURUSDT."""
    def velas(simbolo: str) -> pd.Series:
        out, ini = [], int(desde.timestamp() * 1000)
        while True:
            r = requests.get("https://data-api.binance.vision/api/v3/klines", timeout=30,
                             params={"symbol": simbolo, "interval": "1d", "startTime": ini, "limit": 1000})
            if r.status_code != 200:
                return pd.Series(dtype=float)
            lote = r.json()
            if not lote:
                break
            out += lote
            ini = lote[-1][0] + 86_400_000
            if len(lote) < 1000:
                break
            time.sleep(0.3)
        return pd.Series([float(k[4]) for k in out], index=pd.to_datetime([k[0] for k in out], unit="ms")) if out else pd.Series(dtype=float)

    if activo == "EUR":
        return pd.Series(dtype=float)
    eur = velas(f"{activo}EUR")
    if len(eur) and eur.index.min() <= desde + pd.Timedelta(days=31):
        return eur
    usdt = velas(f"{activo}USDT") if activo not in ESTABLES_USD else None
    eurusdt = velas("EURUSDT")
    if activo in ESTABLES_USD:
        return (1 / eurusdt).dropna()
    if usdt is None or usdt.empty or eurusdt.empty:
        return eur
    conv = (usdt / eurusdt.reindex(usdt.index, method="ffill")).dropna()
    return conv.combine_first(eur) if len(eur) else conv


# ------------------------------------------------------------------ participaciones
def categoria_pp(cuenta: str, activo: str, alias: dict) -> str:
    activo = alias.get(activo, activo)
    if not ES_FONDO.match(str(activo)):
        return "Cripto"
    if cuenta == "Indexa":
        return "Plan de pensiones Indexa" if (str(activo).startswith("0P") or str(activo).startswith("N")) else "Fondos Indexa"
    return {"MyInvestor": "Fondos MyInvestor", "Sabadell": "Fondos Sabadell", "Trade_Republic": "ETF y acciones Trade Republic"}.get(cuenta, cuenta)


def participaciones_mensuales(ops: pd.DataFrame, meses: pd.DatetimeIndex, alias: dict) -> pd.DataFrame:
    """Participaciones a fin de cada mes por (categoría, activo) según PP."""
    o = ops.copy()
    o["fecha"] = pd.to_datetime(o["fecha"])
    o["categoria"] = [categoria_pp(c, a, alias) for c, a in zip(o["cuenta_pp"], o["activo"])]
    o["activo"] = o["activo"].replace(alias)
    o = o[o["cantidad"].notna()]
    filas = []
    for (cat, act), g in o.groupby(["categoria", "activo"]):
        acum = g.set_index("fecha")["cantidad"].sort_index().cumsum()
        for mes in meses:
            v = acum[acum.index <= mes]
            filas.append({"mes": mes, "categoria": cat, "activo": act, "cantidad": float(v.iloc[-1]) if len(v) else 0.0})
    return pd.DataFrame(filas)


def extender_hasta_hoy(part: pd.DataFrame, oficiales: pd.DataFrame, pp_hasta: pd.Timestamp, meses: pd.DatetimeIndex) -> pd.DataFrame:
    """Después de la última fecha de PP, interpola cada activo hasta la cantidad oficial de hoy."""
    part = part.copy()
    if oficiales is None or oficiales.empty:
        return part
    hoy, posteriores = meses[-1], [m for m in meses if m > pp_hasta]
    if not posteriores:
        return part
    base = part[part["mes"] == max(m for m in meses if m <= pp_hasta)].set_index(["categoria", "activo"])["cantidad"]
    fin = oficiales.groupby(["categoria", "activo"])["cantidad"].sum()
    claves = base.index.union(fin.index)
    filas = []
    for k in claves:
        a, b = float(base.get(k, 0.0)), float(fin.get(k, 0.0))
        for m in posteriores:
            f = (m - pp_hasta).days / max((hoy - pp_hasta).days, 1)
            filas.append({"mes": m, "categoria": k[0], "activo": k[1], "cantidad": a + (b - a) * min(f, 1.0)})
    return pd.concat([part[part["mes"] <= pp_hasta], pd.DataFrame(filas)], ignore_index=True)


# ------------------------------------------------------------------ saldos
def saldos_revolut(contenido: bytes, pct: float) -> pd.Series:
    """Saldo diario (todas las subcuentas sumadas) de un CSV de Revolut, multiplicado por `pct`."""
    t = pd.read_csv(io.BytesIO(contenido), dtype=str, keep_default_na=False)
    t = t[t["State"].str.upper().isin(["COMPLETADO", "COMPLETED"]) & (t["Saldo"] != "")]
    t["f"] = pd.to_datetime(t["Fecha de finalización"].where(t["Fecha de finalización"] != "", t["Fecha de inicio"]))
    t["s"] = pd.to_numeric(t["Saldo"], errors="coerce")
    ult = t.sort_values("f").groupby([t["Producto"], t["f"].dt.normalize()])["s"].last().unstack(0).ffill().fillna(0)
    return ult.sum(axis=1) * pct


def saldos_sabadell(contenido: bytes) -> pd.Series:
    bruto = pd.read_excel(io.BytesIO(contenido), header=None, dtype=object)
    fila = bruto.index[bruto[0].astype(str).str.strip().eq("F. Operativa")][0]
    x = bruto.iloc[fila + 1:].copy()
    x.columns = [str(c).strip() for c in bruto.iloc[fila]]
    x = x[x["F. Operativa"].notna()]
    x["f"] = pd.to_datetime(x["F. Operativa"], dayfirst=True)
    x = x.iloc[::-1] if x["f"].iloc[0] >= x["f"].iloc[-1] else x  # del más antiguo al más reciente
    return x.groupby("f")["Saldo"].last().astype(float)


def saldos_trade_republic(contenido: bytes, saldo_hoy: float) -> pd.Series:
    """Saldo diario hacia atrás: saldo de hoy − movimientos posteriores a cada día."""
    t = pd.read_csv(io.BytesIO(contenido), dtype=str, keep_default_na=False)
    imp = sum(pd.to_numeric(t[c].replace("", None), errors="coerce").fillna(0) for c in ("amount", "fee", "tax"))
    diario = imp.groupby(pd.to_datetime(t["date"])).sum().sort_index()
    return saldo_hoy - diario[::-1].cumsum()[::-1].shift(-1, fill_value=0)


def a_fin_de_mes(serie: pd.Series, meses: pd.DatetimeIndex) -> pd.Series:
    if serie.empty:
        return pd.Series(0.0, index=meses)
    s = serie.sort_index()
    return pd.Series([float(s[s.index <= m].iloc[-1]) if (s.index <= m).any() else 0.0 for m in meses], index=meses)


def completar_con_precio_actual(serie: pd.Series, precio_hoy: float | None, hoy: pd.Timestamp) -> pd.Series:
    """Para valores con pocos precios (solo los de tus compras), añade el de hoy e interpola
    en el tiempo entre puntos: mejor que arrastrar el precio de una compra de hace años."""
    if precio_hoy is None or not np.isfinite(precio_hoy):
        return serie
    s = serie.copy()
    s.loc[hoy.normalize()] = precio_hoy
    s = s[~s.index.duplicated(keep="last")].sort_index()
    diario = s.resample("D").mean().interpolate("time")
    return diario
