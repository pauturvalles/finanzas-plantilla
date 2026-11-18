"""Diagnóstico de los fondos de MyInvestor: qué hay en la hoja y qué calcula el código con ello. No escribe nada.

    python scripts/diagnostico_myinvestor.py

Imprime, en este orden: 1) las últimas anotaciones del pipeline sobre MyInvestor y el patrimonio; 2) lo que hay hoy en
`patrimonio_detalle` para MyInvestor; 3) tus filas de `fondos_inicial`, `conciliaciones`, `costes_fiscales` y
`costes_fiscales_base`; 4) los últimos valores liquidativos de cada fondo, con la fecha tal como está en la hoja y como se lee;
5) lo que sale de recalcular ahora la posición y el coste con esos datos. Pega la salida tal cual.
"""
import os
import sys

import pandas as pd
import yaml
from dotenv import load_dotenv

RAIZ = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(RAIZ, "src"))
load_dotenv()
from finanzas import patrimonio as P  # noqa: E402
from finanzas.sheets import Hoja, a_fecha  # noqa: E402

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
pd.set_option("display.max_colwidth", 90)

cfg = yaml.safe_load(open(os.path.join(RAIZ, "config", "cuentas.yaml"), encoding="utf-8"))["inversion"]
isins = [f["isin"] for f in cfg["myinvestor_fondos"]]
hoja = Hoja()


def mostrar(titulo: str, df: pd.DataFrame, cols: list[str] | None = None, n: int | None = None) -> None:
    print(f"\n--- {titulo} ---")
    if df is None or df.empty:
        print("(vacío)")
        return
    d = df if cols is None else df[[c for c in cols if c in df.columns]]
    print((d.tail(n) if n else d).to_string(index=False))


def con_isin(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or "isin" not in df.columns:
        return pd.DataFrame()
    return df[df["isin"].astype(str).str.strip().str.upper().isin(isins)]


print("=== 1. Anotaciones del pipeline (la última ejecución) ===")
reg = hoja.leer("registro")
if not reg.empty:
    ult = reg[reg["fecha"] == reg["fecha"].max()]
    print("ejecución:", ult["fecha"].iloc[0])
    mostrar("MyInvestor, valores liquidativos, patrimonio y errores",
            ult[ult["fuente"].astype(str).str.contains("MyInvestor|Valores|VL |Patrimonio|Series", case=False, regex=True) | (ult["estado"] == "error")],
            ["fuente", "estado", "filas", "detalle"])

print("\n=== 2. patrimonio_detalle (lo que ve el dashboard) ===")
det = hoja.leer("patrimonio_detalle")
if not det.empty:
    mostrar("filas de MyInvestor", det[det["categoria"].astype(str).str.contains("MyInvestor")])

print("\n=== 3. Tus pestañas de partida ===")
mostrar("fondos_inicial", con_isin(hoja.leer("fondos_inicial")))
mostrar("conciliaciones", con_isin(hoja.leer("conciliaciones")))
cf = hoja.leer("costes_fiscales")
mostrar("costes_fiscales (todas las filas)", cf)
mostrar("costes_fiscales_base", con_isin(hoja.leer("costes_fiscales_base")))

print("\n=== 4. Valores liquidativos: últimos de cada fondo ===")
vls = hoja.leer("valores_liquidativos")
if not vls.empty:
    vls = vls.assign(leida=a_fecha(vls["fecha"]).dt.strftime("%Y-%m-%d"))
    for isin in isins:
        g = vls[vls["isin"].astype(str).str.strip().str.upper() == isin].sort_values("leida")
        mostrar(f"{isin} ({len(g)} filas)", g.rename(columns={"fecha": "fecha_en_la_hoja"}), n=6)

print("\n=== 5. Recalculado ahora con esos datos ===")
try:
    mov = hoja.leer("movimientos")
    mi = P.myinvestor(mov, cfg, hoja.leer("fondos_inicial"), hoja.leer("conciliaciones"), vls.drop(columns=["leida"], errors="ignore"))
    mostrar("posición (myinvestor)", mi, ["referencia", "cantidad", "valor", "aportado", "coste_fiscal", "fecha_valor", "metodo"])
    d = P.detalle(mi.to_dict("records"), [], pd.DataFrame(), lambda i: None)
    d2, _ = P.costes_por_activo(d, cf, hoja.leer("costes_fiscales_base"))
    mostrar("coste tras aplicar costes_fiscales", d2, ["activo", "valor", "coste", "coste_plataforma", "origen_coste"])
except Exception as e:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    print(f"ERROR al recalcular: {type(e).__name__}: {e}")
