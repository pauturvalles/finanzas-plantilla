"""Presupuesto anual: el presupuesto mensual llevado a un año (o a lo que va de año), para que un gasto que
llega de golpe (un seguro anual, una revisión, ropa en rebajas) no salga «superado» un mes y «a cero» los demás.
Función pura: el dashboard le pasa los gastos y la tabla de presupuestos."""
from __future__ import annotations

import pandas as pd


def meses_del_periodo(anio: int, hoy: pd.Timestamp, primer_mes: str | None = None) -> tuple[float, float, str, str]:
    """(meses transcurridos, meses que cubre el año, primer mes, último mes).

    El año empieza en enero o en el primer mes con datos si es posterior (p. ej. si clasificas desde febrero).
    En el año en curso, el mes actual cuenta por la parte transcurrida (día 15 de 30 → 0,5)."""
    inicio = pd.Period(f"{anio}-01", "M")
    if primer_mes and pd.Period(primer_mes, "M") > inicio:
        inicio = pd.Period(primer_mes, "M")
    fin_anio = pd.Period(f"{anio}-12", "M")
    cubre = float(max(0, fin_anio.ordinal - inicio.ordinal + 1))
    actual = pd.Period(hoy, "M")
    if anio < hoy.year:
        transcurridos, ultimo = cubre, fin_anio
    elif anio == hoy.year:
        transcurridos = max(0.0, actual.ordinal - inicio.ordinal + hoy.day / actual.days_in_month)
        ultimo = actual
    else:
        transcurridos, ultimo = 0.0, inicio
    return transcurridos, cubre, inicio.strftime("%Y-%m"), ultimo.strftime("%Y-%m")


def tabla_anual(gastos: pd.DataFrame, pres: pd.Series, anio: int, hoy: pd.Timestamp, primer_mes: str | None = None,
                media_mensual: pd.Series | None = None) -> tuple[pd.DataFrame, dict]:
    """Por categoría con presupuesto: presupuesto del año, presupuesto a la fecha (parte proporcional), gastado,
    uso, proyección a final de año y estado.

    `gastos`: columnas mes ("AAAA-MM"), categoria, importe (positivo = gasto). `pres`: presupuesto mensual por
    categoría. `media_mensual`: gasto medio mensual por categoría para proyectar lo que queda de año (si falta,
    se proyecta al ritmo de lo que va de año)."""
    transc, cubre, ini, fin = meses_del_periodo(anio, hoy, primer_mes)
    g = gastos[(gastos["mes"] >= ini) & (gastos["mes"] <= fin)]
    gastado = g.groupby("categoria")["importe"].sum()
    restante_meses = max(0.0, cubre - transc)
    filas = []
    for cat, mensual in pres.items():
        anual, a_fecha = mensual * cubre, mensual * transc
        hecho = float(gastado.get(cat, 0.0))
        ritmo = (media_mensual.get(cat) if media_mensual is not None and cat in media_mensual else None)
        if ritmo is None or pd.isna(ritmo):
            ritmo = hecho / transc if transc else 0.0
        proy = hecho + ritmo * restante_meses
        if hecho > a_fecha:
            estado = "🔴 por encima"
        elif proy > anual * 1.0001:
            estado = "🟠 va a pasarse"
        else:
            estado = "🟢 dentro"
        filas.append({"Categoría": cat, "Presupuesto del año": anual, "Presupuesto a la fecha": a_fecha, "Gastado": hecho,
                      "Usado": hecho / a_fecha if a_fecha else float("nan"), "Margen a la fecha": a_fecha - hecho,
                      "Proyección a fin de año": proy, "Estado": estado})
    tab = pd.DataFrame(filas)
    info = {"meses_transcurridos": transc, "meses_del_anio": cubre, "desde": ini, "hasta": fin,
            "gastado_sin_presupuesto": float(gastado.drop(labels=list(pres.index), errors="ignore").sum())}
    return tab, info
