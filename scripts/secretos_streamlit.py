"""Imprime, en formato TOML, los dos secretos que necesita el dashboard (a partir del .env).
Cópialo en Streamlit Cloud → Settings → Secrets o en .streamlit/secrets.toml. No lo guardes en el repo."""
import json
import os

from dotenv import load_dotenv

load_dotenv()
cuenta = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"])
print(f'SHEET_ID = "{os.environ["SHEET_ID"].strip()}"')
print("GOOGLE_SERVICE_ACCOUNT_JSON = '''")
print(json.dumps(cuenta, indent=1))
print("'''")
