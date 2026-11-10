"""Diagnóstico: por qué la rentabilidad mes a mes del dashboard no coincide con la de Indexa.

    python -X utf8 scripts/diagnostico_rentabilidad.py [CARPETA_PRECIOS_PP]

Imprime (solo porcentajes, fechas y nombres de campo; ningún importe):
  1. La estructura de la respuesta de rentabilidad de la API de Indexa (para poder usar la suya directamente).
  2. La rentabilidad mes a mes que calcula el dashboard para «Fondos Indexa», y la de cada fondo en 2025–2026.
  3. Con la carpeta de precios de Portfolio Performance: formato de las fechas y si faltan precios a fin de mes.
"""
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
load_dotenv()
from finanzas import rentabilidad as R  # noqa: E402
from finanzas.sheets import Hoja, a_numero  # noqa: E402

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 30)


def estructura(x, pref="", prof=0):
    if prof > 5:
        return
    if isinstance(x, dict):
        for k, v in list(x.items())[:40]:
            print(f"{pref}{k}: {type(v).__name__}" + (f" [{len(v)}]" if isinstance(v, (list, dict)) else ""))
            estructura(v, pref + "  ", prof + 1)
    elif isinstance(x, list) and x:
        estructura(x[0], pref + "  ", prof + 1)


# 1. API de Indexa
print("=== 1. API de Indexa: rentabilidad ===")
try:
    from finanzas.fuentes.indexa import _get
    for cta in _get("/users/me").get("accounts", []):
        n, tipo = cta.get("account_number"), cta.get("type")
        for ruta in (f"/accounts/{n}/performance",):
            try:
                print(f"\n--- {ruta.replace(n, '{' + tipo + '}')} ---")
                estructura(_get(ruta))
            except Exception as e:  # noqa: BLE001
                print(f"   no disponible: {type(e).__name__}: {e}"[:200])
except Exception as e:  # noqa: BLE001
    print(f"   sin acceso a Indexa: {type(e).__name__}: {e}"[:200])

# 2. Lo que calcula el dashboard
print("\n=== 2. Lo que calcula el dashboard ===")
hoja = Hoja()
ph, va = hoja.leer("precios_historicos"), hoja.leer("patrimonio_historico_activos")
if ph.empty or va.empty:
    sys.exit("No hay series en la hoja: ejecuta antes scripts/reconstruir_historico.py.")
ph["precio"], va["valor"] = a_numero(ph["precio"]), a_numero(va["valor"])
print("Fuentes de precio:", ph.groupby("fuente")["activo"].nunique().to_dict() if "fuente" in ph.columns else "-")
r = R.rent_activos(ph)
for cat in ["Fondos Indexa", "Plan de pensiones Indexa", "Fondos MyInvestor"]:
    m = va["categoria"] == cat
    if not m.any():
        continue
    activos = sorted(va.loc[m, "activo"].unique())
    print(f"\n--- {cat}: {', '.join(activos)}")
    tab = R.tabla_mes_a_mes(R.recortar(R.rent_grupo(va[m], r)))
    print((tab * 100).round(1).to_string())
    print(f"\nCada fondo (2025-2026, %), y su peso a final del mes anterior:")
    cols = [a for a in activos if a in r.columns]
    print((r.loc["2025-01-31":, cols] * 100).round(1).to_string())
    w = va[m].assign(mes=R._fin_de_mes(va.loc[m, "mes"])).groupby(["mes", "activo"])["valor"].sum().unstack()
    print(((w.div(w.sum(axis=1), axis=0)).loc["2024-12-31":] * 100).round(0).to_string())

# 3. Precios de Portfolio Performance
if len(sys.argv) > 1:
    print("\n=== 3. Ficheros de precios de PP ===")
    activos_ix = set(va.loc[va["categoria"].str.contains("Indexa", na=False), "activo"])
    for f in sorted(Path(sys.argv[1]).glob("*.csv")):
        if f.stem.upper() not in activos_ix:
            continue
        lineas = f.read_text(encoding="utf-8-sig").splitlines()
        print(f"\n{f.name}: {len(lineas) - 1} precios. Primeras fechas tal cual: {[l.split(';')[0] for l in lineas[1:4]]}")
        fechas = pd.to_datetime(pd.Series([l.split(";")[0] for l in lineas[1:]]), errors="coerce")
        fechas_d = pd.to_datetime(pd.Series([l.split(";")[0] for l in lineas[1:]]), errors="coerce", dayfirst=True)
        iso = pd.Series([l.split(";")[0] for l in lineas[1:]]).str.match(r"^\d{4}-\d{2}-\d{2}")
        if (~iso).any() and (fechas != fechas_d).any():
            print(f"   ⚠️  {int((fechas != fechas_d).sum())} fechas se leen distinto según día/mes: posible confusión dd/mm ↔ mm/dd")
        f_ = fechas.dropna()
        ult = f_.groupby(f_.dt.to_period("M")).max()
        fin = ult.index.to_timestamp("M")
        dias = (fin - pd.DatetimeIndex(ult.values)).days
        tarde = pd.Series(dias, index=ult.index.astype(str))
        tarde = tarde[tarde.index >= "2021-01"]
        print(f"   Días entre el último precio de cada mes y el fin de mes: mediana {tarde.median():.0f}, máximo {tarde.max()} "
              f"({', '.join(f'{k}: {v}' for k, v in tarde[tarde > 5].items()) or 'ninguno de más de 5'})")
