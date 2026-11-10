"""Rentabilidad mes a mes de activos, plataformas y carteras (funciones puras, sin hoja ni red).

Dos datos de partida, los dos a fin de mes:
  - precios por activo (`precios_historicos`: activo, fecha, precio)  → rentabilidad del activo en sí;
  - valor de cada activo que tenías (`patrimonio_historico_activos`: mes, categoria, activo, valor).
La rentabilidad de un grupo (una plataforma, una categoría, todo lo invertido) en el mes t es la media de la
de sus activos ponderada por lo que valía cada uno a final del mes t−1. Es una rentabilidad «ponderada en el
tiempo» (la que publican los fondos e Indexa): no la mueven las aportaciones ni los reembolsos, así que sirve
para comparar plataformas o activos entre sí. La TIR (ponderada por el dinero) está en la tabla de rentabilidad.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MESES_ES = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]


def _fin_de_mes(fechas: pd.Series) -> pd.Series:
    return pd.to_datetime(fechas, errors="coerce", format="mixed").dt.to_period("M").dt.to_timestamp("M")


def precios_fin_de_mes(precios: pd.DataFrame) -> pd.DataFrame:
    """Último precio de cada mes por activo: índice = fin de mes, columnas = activo."""
    if precios.empty:
        return pd.DataFrame()
    p = precios.assign(fecha=pd.to_datetime(precios["fecha"], errors="coerce", format="mixed"),
                       precio=pd.to_numeric(precios["precio"], errors="coerce"))
    p = p.dropna(subset=["fecha", "precio"])
    p = p[p["precio"] > 0].sort_values("fecha")
    p["mes"] = p["fecha"].dt.to_period("M").dt.to_timestamp("M")
    return p.groupby(["mes", "activo"])["precio"].last().unstack("activo").sort_index()


def rent_activos(precios: pd.DataFrame) -> pd.DataFrame:
    """Rentabilidad mensual de cada activo (solo entre dos meses seguidos con precio)."""
    pm = precios_fin_de_mes(precios)
    if pm.empty:
        return pm
    pm = pm.reindex(pd.date_range(pm.index.min(), pm.index.max(), freq="ME"))
    return pm / pm.shift(1) - 1


def rent_grupo(valores: pd.DataFrame, rent: pd.DataFrame, activos: list[str] | None = None) -> pd.Series:
    """Rentabilidad mensual de un grupo de activos, ponderada por su valor a final del mes anterior.

    `valores`: columnas mes, activo, valor (lo que valía cada activo a fin de mes; varias filas por
    activo y mes se suman). `activos`: los del grupo (por defecto, todos). Un mes sin ningún activo con
    valor y rentabilidad queda vacío (NaN).
    """
    if valores.empty or rent.empty:
        return pd.Series(dtype=float)
    v = valores.assign(mes=_fin_de_mes(valores["mes"]), valor=pd.to_numeric(valores["valor"], errors="coerce"))
    if activos is not None:
        v = v[v["activo"].isin(activos)]
    w = v.groupby(["mes", "activo"])["valor"].sum().unstack("activo")
    w = w.reindex(rent.index)
    vacios = w.isna().all(axis=1)  # meses sin foto (p. ej. entre la reconstrucción y el pipeline): la del mes anterior
    w.loc[vacios] = w.ffill().loc[vacios]
    w = w.shift(1)  # peso = valor a final del mes anterior
    comunes = [c for c in w.columns if c in rent.columns]
    if not comunes:
        return pd.Series(dtype=float)
    w, r = w[comunes].where(lambda x: x > 0), rent[comunes]
    valido = w.notna() & r.notna()
    peso = w.where(valido).sum(axis=1)
    out = (w.where(valido) * r.where(valido)).sum(axis=1) / peso.replace(0, np.nan)
    return out.where(peso > 0)


def recortar(r: pd.Series, desde: pd.Timestamp | None = None) -> pd.Series:
    """Desde `desde` (fin de mes) y sin los meses vacíos del principio ni del final."""
    if r.dropna().empty:
        return r.dropna()
    r = r.loc[r.first_valid_index():r.last_valid_index()]
    if desde is not None:
        r = r[r.index >= desde]
    return r


def acumulada(r: pd.Series) -> pd.Series:
    """Rentabilidad acumulada al final de cada mes, con un punto 0 al final del mes anterior al primero."""
    r = r.dropna()
    if r.empty:
        return r
    inicio = pd.Series([0.0], index=[r.index[0] - pd.offsets.MonthEnd(1)])
    return pd.concat([inicio, (1 + r).cumprod() - 1])


def metricas(r: pd.Series) -> dict:
    """Rentabilidad del periodo, anualizada, volatilidad anual, peor caída y mejor/peor mes."""
    r = r.dropna()
    if r.empty:
        return {}
    n = len(r)
    total = float((1 + r).prod() - 1)
    curva = (1 + r).cumprod()
    caida = float((curva / curva.cummax().clip(lower=1) - 1).min())
    return {"Desde": r.index[0].strftime("%Y-%m"), "Meses": n, "Rentabilidad": total,
            "Anualizada": (1 + total) ** (12 / n) - 1 if n >= 12 else np.nan,
            "Volatilidad anual": float(r.std() * np.sqrt(12)) if n >= 3 else np.nan,
            "Peor caída": min(caida, 0.0), "Mejor mes": float(r.max()), "Peor mes": float(r.min())}


def tabla_mes_a_mes(r: pd.Series) -> pd.DataFrame:
    """Años en filas, meses en columnas (Ene…Dic) y la rentabilidad de cada año (encadenada) al final."""
    r = r.dropna()
    if r.empty:
        return pd.DataFrame()
    t = pd.DataFrame({"anio": r.index.year, "mes": r.index.month, "r": r.values})
    tab = t.pivot(index="anio", columns="mes", values="r").reindex(columns=range(1, 13))
    tab.columns = MESES_ES
    tab["Año"] = t.groupby("anio")["r"].apply(lambda x: (1 + x).prod() - 1)
    tab.index.name = None
    return tab.sort_index(ascending=False)


def actualizar_mes(existente: pd.DataFrame, nuevas: pd.DataFrame, col_fecha: str, claves: list[str]) -> pd.DataFrame:
    """Sustituye en `existente` las filas del mismo mes y las mismas claves que `nuevas` (una foto por mes:
    la última del mes manda). Sirve para que el pipeline vaya alargando las series sin duplicar."""
    if nuevas.empty:
        return existente
    if existente.empty:
        return nuevas.reset_index(drop=True)
    e, n = existente.copy(), nuevas.copy()
    e["_mes"], n["_mes"] = _fin_de_mes(e[col_fecha]), _fin_de_mes(n[col_fecha])
    clave = lambda d: d[claves].astype(str).agg("|".join, axis=1) + "|" + d["_mes"].astype(str)  # noqa: E731
    e = e[~clave(e).isin(set(clave(n)))]
    return pd.concat([e, n], ignore_index=True).drop(columns="_mes")


def sustituir_mes(existente: pd.DataFrame, nuevas: pd.DataFrame, col_fecha: str) -> pd.DataFrame:
    """Quita del histórico todas las filas del mes de `nuevas` y añade estas (para la foto de posiciones:
    si vendes algo a mitad de mes, su fila de principios de mes también debe desaparecer)."""
    if nuevas.empty:
        return existente
    if existente.empty:
        return nuevas.reset_index(drop=True)
    meses = set(_fin_de_mes(nuevas[col_fecha]))
    e = existente[~_fin_de_mes(existente[col_fecha]).isin(meses)]
    return pd.concat([e, nuevas], ignore_index=True)


def foto_mensual(det: pd.DataFrame, fecha: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """De `patrimonio_detalle` de hoy: (precio de cada activo, valor de cada activo por categoría).
    Sin efectivo: solo lo invertido con cantidad y valor."""
    d = det.copy()
    for c in ("cantidad", "valor"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d[(d["activo"].astype(str) != "EUR") & (d["categoria"] != "Efectivo en cuentas") & (d["cantidad"] > 0) & (d["valor"] > 0)]
    if d.empty:
        return pd.DataFrame(columns=["activo", "fecha", "precio", "fuente"]), pd.DataFrame(columns=["mes", "categoria", "activo", "cantidad", "valor"])
    p = d.groupby("activo", as_index=False)[["valor", "cantidad"]].sum()
    precios = pd.DataFrame({"activo": p["activo"], "fecha": fecha, "precio": (p["valor"] / p["cantidad"]).round(6), "fuente": "pipeline"})
    v = d.groupby(["categoria", "activo"], as_index=False)[["cantidad", "valor"]].sum()
    v.insert(0, "mes", fecha)
    v["valor"] = v["valor"].round(2)
    return precios, v


def con_indice_oficial(precios: pd.DataFrame, valores: pd.DataFrame, indices: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Sustituye, para cada categoría con índice oficial (p. ej. Indexa: `indexa_rentabilidad`), sus fondos por un
    único activo «<categoría> (oficial)» cuyo precio es ese índice y cuyo valor es la suma de la categoría.
    Así la categoría y todo lo que la incluye («Todo lo invertido»…) usan la rentabilidad oficial, con sus comisiones
    y su efectivo. Solo desde el primer mes con índice; los fondos sueltos siguen comparables por su precio."""
    if indices.empty or valores.empty:
        return precios, valores
    ind = indices.assign(indice=pd.to_numeric(indices["indice"], errors="coerce")).dropna(subset=["indice"])
    p_extra, v = [], valores.copy()
    v["_mes"] = _fin_de_mes(v["mes"])
    for cat, g in ind.groupby("categoria"):
        nombre = f"{cat} (oficial)"
        p_extra.append(pd.DataFrame({"activo": nombre, "fecha": g["fecha"], "precio": g["indice"], "fuente": "oficial"}))
        desde = _fin_de_mes(g["fecha"]).min()
        m = (v["categoria"] == cat) & (v["_mes"] >= desde)
        if not m.any():
            continue
        suma = v[m].groupby("_mes", as_index=False)["valor"].sum()
        nuevas = pd.DataFrame({"mes": suma["_mes"].dt.strftime("%Y-%m-%d"), "categoria": cat, "activo": nombre, "valor": suma["valor"], "_mes": suma["_mes"]})
        v = pd.concat([v[~m], nuevas], ignore_index=True)
    return pd.concat([precios, *p_extra], ignore_index=True), v.drop(columns="_mes")
