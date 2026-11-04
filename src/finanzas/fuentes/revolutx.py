"""Revolut X (exchange de cripto de Revolut): saldos y compras/ventas propias.

API oficial: https://developer.revolut.com/docs/api/revolut-x-crypto-exchange
Autenticación con firma Ed25519: generas un par de claves en tu ordenador, registras la
pública en Revolut X (Perfil → API keys) y guardas aquí la privada y la API key.

    REVX_API_KEY      = la API key que te da Revolut X
    REVX_PRIVATE_KEY  = contenido de private.pem (Ed25519, PEM)

Firma = base64(Ed25519(timestamp_ms + MÉTODO + ruta desde /api/1.0 + query sin «?» + cuerpo)).
"""
from __future__ import annotations

import base64
import os
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
from cryptography.hazmat.primitives.serialization import load_pem_private_key

BASE = "https://revx.revolut.com"
PREFIJO = "/api/1.0"
VENTANA = timedelta(days=30)  # la API no deja pedir más de 30 días de operaciones por consulta


PAUSA = 1.5  # s entre consultas de operaciones: cada día del rango cuesta 1 «token» (máx. 100/s y 1.000/min)


def _get(ruta: str, **params) -> dict:
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    clave = load_pem_private_key(os.environ["REVX_PRIVATE_KEY"].encode(), password=None)
    for intento in range(6):
        ts = str(int(time.time() * 1000))  # la firma incluye la hora: se rehace en cada intento
        firma = base64.b64encode(clave.sign((ts + "GET" + PREFIJO + ruta + query).encode())).decode()
        r = requests.get(BASE + PREFIJO + ruta + (f"?{query}" if query else ""), timeout=60, headers={
            "X-Revx-API-Key": os.environ["REVX_API_KEY"], "X-Revx-Timestamp": ts, "X-Revx-Signature": firma,
            "Accept": "application/json"})
        if r.status_code == 429:  # límite de consultas: esperar lo que diga Retry-After (en ms) y repetir
            espera = float(r.headers.get("Retry-After", 0)) / 1000 or 5 * (intento + 1)
            time.sleep(min(max(espera, 1), 65))
            continue
        if not r.ok:
            raise RuntimeError(f"{r.status_code} en GET {ruta}: {r.text[:300]}")
        return r.json()
    raise RuntimeError(f"429 en GET {ruta}: límite de consultas superado tras varios reintentos")


def saldos() -> dict[str, float]:
    """{activo: cantidad total} (incluye lo reservado y en staking)."""
    datos = _get("/balances")
    datos = datos.get("data", datos) if isinstance(datos, dict) else datos
    return {d["currency"].upper(): float(d.get("total") or 0) for d in datos if float(d.get("total") or 0) > 0}


def operaciones(simbolo: str, desde: datetime) -> pd.DataFrame:
    """Compras y ventas propias de un par (p. ej. «BTC-EUR») desde una fecha, en tramos de 30 días."""
    filas, ini, fin_total = [], desde.replace(tzinfo=timezone.utc), datetime.now(timezone.utc)
    while ini < fin_total:
        fin = min(ini + VENTANA, fin_total)
        cursor = None
        while True:
            r = _get(f"/trades/private/{simbolo}", start_date=int(ini.timestamp() * 1000),
                     end_date=int(fin.timestamp() * 1000), cursor=cursor, limit=100)
            filas += r.get("data", [])
            cursor = (r.get("metadata") or {}).get("next_cursor")
            time.sleep(PAUSA)
            if not cursor:
                break
        ini = fin
    if not filas:
        return pd.DataFrame()
    t = pd.DataFrame(filas)
    cant, precio = t["q"].astype(float), t["p"].astype(float)
    signo = t.get("s", pd.Series("buy", index=t.index)).map({"buy": 1, "sell": -1}).fillna(1)
    return pd.DataFrame({
        "fecha": pd.to_datetime(t["tdt"], unit="ms").dt.strftime("%Y-%m-%d"),
        "plataforma": "Revolut X",
        "tipo": signo.map({1: "compra", -1: "venta"}),
        "activo": t["aid"].str.upper(),
        "cantidad": cant * signo,
        "precio": precio,
        "importe": (cant * precio).round(2),       # en la divisa del precio (EUR en BTC-EUR)
        "divisa": t.get("pc", "EUR"),
        "id": "revx-" + t["tid"].astype(str),
        "fuente": "revolutx_api",
    })
