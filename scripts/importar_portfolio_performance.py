"""Importa los CSV de Portfolio Performance (uno por cuenta de valores) y los sube a la hoja.

Uso, en local (los ficheros no salen de tu ordenador salvo a tu propia hoja de Google):
    python -X utf8 scripts/importar_portfolio_performance.py RUTA/A/LA/CARPETA
    python -X utf8 scripts/importar_portfolio_performance.py RUTA --sin-subir   # solo revisar

Cada fichero debe llamarse como la cuenta de valores: «Indexa.csv», «MyInvestor.csv»,
«Binance.csv»… Escribe dos pestañas: `pp_operaciones` (todo normalizado) y
`pp_posiciones` (cantidad y coste por cuenta y activo según PP), que luego se concilian
con las fuentes oficiales.
"""
import argparse
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()
from finanzas.fuentes import portfolio_performance as pp  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("carpeta")
ap.add_argument("--sin-subir", action="store_true", help="solo muestra el resumen, no escribe en la hoja")
a = ap.parse_args()

ops = pp.leer_carpeta(a.carpeta)
if ops.empty:
    sys.exit("No hay CSV en esa carpeta.")
print(f"{len(ops)} operaciones en {ops['cuenta_pp'].nunique()} cuentas: {', '.join(sorted(ops['cuenta_pp'].unique()))}")
print("Tipos:", ops["tipo"].value_counts().to_dict())
raros = ops[ops["tipo"].str.startswith("desconocido") | ops["fecha"].isna() | ops["cantidad"].isna()]
if len(raros):
    print(f"\n⚠️  {len(raros)} filas con tipo, fecha o cantidad que no he sabido leer (se suben igual para revisarlas):")
    print(raros[["cuenta_pp", "fecha", "tipo", "activo", "cantidad", "importe"]].head(20).to_string(index=False))
ops = pp.emparejar_traspasos(ops)
mov = ops[ops["tipo"].isin(pp.ENTRADAS + pp.SALIDAS)]
emp = mov[mov["pareja"] != ""]
print(f"\nTraspasos y entregas: {len(mov)} movimientos, {len(emp) // 2} parejas identificadas.")
if len(emp):
    vista = emp[emp["tipo"].isin(pp.SALIDAS)][["fecha", "activo", "cuenta_pp", "pareja", "cantidad", "importe"]]
    print(vista.rename(columns={"cuenta_pp": "desde", "pareja": "hacia"}).to_string(index=False))
ops = pp.clasificar_sueltos(ops)
sueltos = ops[ops["clase"] != ""]
if len(sueltos):
    print(f"\nSin pareja ({len(sueltos)}), por motivo:", sueltos["clase"].value_counts().to_dict())
    for clase, g in sueltos.groupby("clase"):
        print(f"\n  [{clase}]")
        print(g[["fecha", "cuenta_pp", "tipo", "activo", "cantidad", "importe", "nota"]].to_string(index=False))
pos = pp.posiciones(ops)
print("\nPosiciones según PP (cantidad > 0):")
print(pos[pos["cantidad"].round(8) != 0].to_string(index=False))

if not a.sin_subir:
    from finanzas.sheets import Hoja
    h = Hoja()
    h.escribir("pp_operaciones", ops)
    h.escribir("pp_posiciones", pos)
    print("\nSubido a las pestañas pp_operaciones y pp_posiciones.")
