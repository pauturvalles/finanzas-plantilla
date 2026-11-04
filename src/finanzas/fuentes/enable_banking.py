"""Cliente mínimo de Enable Banking (PSD2, solo lectura).

Flujo:
1. Una vez por banco (y al caducar el consentimiento): `scripts/autorizar_banco.py`
   abre la web del banco, apruebas el acceso y se guarda el `session_id`.
2. En cada ejecución: con el `session_id` se leen saldos y movimientos.

Documentación: https://enablebanking.com/docs/api/reference/
Revisa los nombres de campo con la primera respuesta real: los bancos rellenan
`remittance_information`, `creditor`, `debtor`… de forma distinta.
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import date, datetime, timedelta, timezone

import jwt  # PyJWT
import pandas as pd
import requests

API = "https://api.enablebanking.com"


class EnableBanking:
    def __init__(self, application_id: str | None = None, private_key_pem: str | None = None):
        self.app_id = application_id or os.environ["EB_APPLICATION_ID"]
        self.key = private_key_pem or os.environ["EB_PRIVATE_KEY"]

    # --- autenticación de la aplicación -------------------------------------------
    def _token(self) -> str:
        ahora = int(time.time())
        return jwt.encode(
            {"iss": "enablebanking.com", "aud": "api.enablebanking.com", "iat": ahora, "exp": ahora + 3600},
            self.key,
            algorithm="RS256",
            headers={"kid": self.app_id},
        )

    def _get(self, path: str, **params) -> dict:
        r = requests.get(API + path, params=params, headers={"Authorization": f"Bearer {self._token()}"}, timeout=60)
        if not r.ok:
            raise RuntimeError(f"{r.status_code} en GET {path}: {r.text}")
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        r = requests.post(API + path, json=body, headers={"Authorization": f"Bearer {self._token()}"}, timeout=60)
        if not r.ok:
            raise RuntimeError(f"{r.status_code} en POST {path}: {r.text}")
        return r.json()

    # --- consentimiento (lo usa scripts/autorizar_banco.py) ---------------------------
    def bancos(self, country: str = "ES") -> list[dict]:
        return self._get("/aspsps", country=country).get("aspsps", [])

    def iniciar_autorizacion(self, aspsp: dict, redirect_url: str, dias: int = 180) -> str:
        info = next((b for b in self.bancos(aspsp["country"]) if b["name"] == aspsp["name"]), None)
        if info is None:
            raise ValueError(f"No encuentro '{aspsp['name']}' en {aspsp['country']}. Usa --listar para ver el nombre exacto.")
        max_seg = info.get("maximum_consent_validity")
        if max_seg:
            dias = min(dias, int(max_seg) // 86400)
        valido = (datetime.now(timezone.utc) + timedelta(days=dias)).isoformat()
        r = self._post("/auth", {
            "access": {"valid_until": valido},
            "aspsp": aspsp,
            "state": str(uuid.uuid4()),
            "redirect_url": redirect_url,
            "psu_type": "personal",
        })
        return r["url"]

    def crear_sesion(self, code: str) -> dict:
        """Devuelve {'session_id':…, 'accounts': [{'uid':…, 'account_id': {'iban':…}, …}]}."""
        return self._post("/sessions", {"code": code})

    # --- lectura de datos ---------------------------------------------------------
    def saldos(self, account_uid: str) -> list[dict]:
        return self._get(f"/accounts/{account_uid}/balances").get("balances", [])

    def movimientos(self, account_uid: str, desde: date) -> list[dict]:
        out, cont = [], None
        while True:
            params = {"date_from": desde.isoformat()}
            if cont:
                params["continuation_key"] = cont
            r = self._get(f"/accounts/{account_uid}/transactions", **params)
            out += r.get("transactions", [])
            cont = r.get("continuation_key")
            if not cont:
                return out


def normalizar(cuenta: str, txs: list[dict]) -> pd.DataFrame:
    """Convierte movimientos de Enable Banking al esquema raw (sin tx_id todavía)."""
    filas = []
    for t in txs:
        if t.get("status") not in (None, "BOOK"):
            continue  # solo movimientos contabilizados; los pendientes cambian de importe
        imp = float(t["transaction_amount"]["amount"])
        if t.get("credit_debit_indicator") == "DBIT":
            imp = -abs(imp)
        sale = imp < 0
        otra = (t.get("creditor") if sale else t.get("debtor")) or {}
        cta = (t.get("creditor_account") if sale else t.get("debtor_account")) or {}
        filas.append({
            "cuenta": cuenta,
            "fecha": t.get("booking_date") or t.get("value_date") or t.get("transaction_date"),
            "concepto": " ".join(t.get("remittance_information") or []) or otra.get("name", ""),
            "contraparte": otra.get("name", ""),
            "iban_contraparte": cta.get("iban", ""),
            "importe": round(imp, 2),
            "moneda": t["transaction_amount"].get("currency", "EUR"),
            "tipo_origen": t.get("bank_transaction_code", {}).get("description", "") if isinstance(t.get("bank_transaction_code"), dict) else "",
            "fuente": "enable_banking",
            "ref": t.get("entry_reference") or t.get("transaction_id") or "",
        })
    return pd.DataFrame(filas)
