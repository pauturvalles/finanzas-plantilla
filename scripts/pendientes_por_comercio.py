"""Agrupa los movimientos sin clasificar por comercio, ordenados por importe, para resolverlos con
reglas en bloque (una regla resuelve todos los cargos de un comercio).

    python -X utf8 scripts/pendientes_por_comercio.py            # los 30 con más dinero
    python -X utf8 scripts/pendientes_por_comercio.py --top 60

La salida (comercio, cuenta, nº de cargos, total y un concepto de ejemplo) se puede pegar tal
cual para proponer la categoría de cada uno.
"""
import argparse
import os
import re
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()
from finanzas.sheets import Hoja, a_numero  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--top", type=int, default=30)
a = ap.parse_args()

p = Hoja().leer("pendientes")
if p.empty:
    sys.exit("No hay pendientes.")
p["importe"] = a_numero(p["importe"])


def comercio(c: str) -> str:
    palabras = [w for w in re.split(r"[\s*]+", str(c)) if w and not re.search(r"\d", w)]
    return " ".join(palabras[:3]).strip(" -.,").upper() or str(c)[:30]


p["comercio"] = p["concepto"].map(comercio)
g = p.groupby(["comercio", "cuenta"]).agg(cargos=("importe", "size"), total=("importe", "sum"), ejemplo=("concepto", "first"),
                                          desde=("fecha", "min"), hasta=("fecha", "max")).reset_index()
g["abs"] = g["total"].abs()
g = g.sort_values("abs", ascending=False)
cubierto = g["abs"].head(a.top).sum() / g["abs"].sum()
print(f"{len(p)} movimientos sin clasificar en {len(g)} comercios. Los {a.top} primeros suman el {cubierto:.0%} del dinero.\n")
print(g.head(a.top)[["comercio", "cuenta", "cargos", "total", "desde", "hasta", "ejemplo"]].to_string(index=False))
