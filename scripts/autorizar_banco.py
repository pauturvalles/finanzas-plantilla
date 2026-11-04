"""Autoriza (o renueva) el acceso de solo lectura a un banco con Enable Banking.

Uso, en tu ordenador (no en GitHub Actions):
    python scripts/autorizar_banco.py --listar            # ver nombres exactos de bancos en ES
    python scripts/autorizar_banco.py "Banco Sabadell"    # abrir la autorización

1. Se abre la web de tu banco y apruebas el acceso (Enable Banking nunca ve tu contraseña).
2. Te redirige a REDIRECT_URL con ?code=… en la barra de direcciones: copia la URL entera y pégala aquí.
3. El script muestra los uid de tus cuentas. Añádelos al secreto EB_ACCOUNTS, por ejemplo:
   {"SAB_CORR": "uid-1", "SAB_AHO": "uid-2", "REV_CONJ": "uid-3"}
Repite cuando caduque el consentimiento (el registro de la hoja mostrará errores de esa cuenta).
"""
import argparse
import sys
import urllib.parse
import webbrowser

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dotenv import load_dotenv  # noqa: E402

from finanzas.fuentes.enable_banking import EnableBanking  # noqa: E402

load_dotenv()
ap = argparse.ArgumentParser()
ap.add_argument("banco", nargs="?")
ap.add_argument("--pais", default="ES")
ap.add_argument("--listar", action="store_true")
ap.add_argument("--dias", type=int, default=180, help="duración del consentimiento; algunos bancos fallan si es larga (prueba 90)")
ap.add_argument("--redirect", default="https://localhost/finanzas-callback",
                help="debe coincidir con la redirect URL registrada en tu aplicación de Enable Banking")
a = ap.parse_args()

eb = EnableBanking()
if a.listar or not a.banco:
    for b in eb.bancos(a.pais):
        print(b["name"])
    sys.exit(0)

url = eb.iniciar_autorizacion({"name": a.banco, "country": a.pais}, a.redirect, dias=a.dias)
print("Abriendo:", url)
webbrowser.open(url)
vuelta = input("Pega aquí la URL completa a la que te ha redirigido el banco: ").strip()
u = urllib.parse.urlparse(vuelta)
params = {**urllib.parse.parse_qs(u.fragment), **urllib.parse.parse_qs(u.query)}
if "code" not in params:
    if "error" in params:
        print("El banco ha devuelto un error:", params.get("error"), params.get("error_description", ""))
    else:
        print("La URL pegada no lleva ?code=… — ¿es la de localhost/finanzas-callback? Parámetros recibidos:", list(params))
    sys.exit(1)
code = params["code"][0]
ses = eb.crear_sesion(code)
print("\nsession_id:", ses["session_id"])
for c in ses.get("accounts", []):
    print(f"  uid={c['uid']}  iban={c.get('account_id', {}).get('iban')}  nombre={c.get('name')}")
