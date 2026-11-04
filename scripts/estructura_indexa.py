"""Imprime la ESTRUCTURA de las respuestas de Indexa (campos y tipos), sin valores."""
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
load_dotenv()
from finanzas.fuentes.indexa import _get  # noqa: E402


def estructura(x, pref="", prof=0):
    """Recorre el JSON mostrando claves y tipos; de las listas, solo el primer elemento."""
    if prof > 4:
        return
    if isinstance(x, dict):
        for k, v in x.items():
            print(f"{pref}{k}: {type(v).__name__}" + (f" [{len(v)}]" if isinstance(v, list) else ""))
            estructura(v, pref + "  ", prof + 1)
    elif isinstance(x, list) and x:
        estructura(x[0], pref + "  ", prof + 1)


yo = _get("/users/me")
print("=== /users/me ===")
estructura(yo)

cuentas = yo.get("accounts", [])
print(f"\n{len(cuentas)} cuentas. Tipo de cada una:", [c.get("type") for c in cuentas])
if cuentas:
    print("\n=== /accounts/{n}/portfolio (primera cuenta) ===")
    estructura(_get(f"/accounts/{cuentas[0]['account_number']}/portfolio"))
