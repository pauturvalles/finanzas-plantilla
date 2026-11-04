"""Kraken: saldos con una clave de API de solo consulta («Query Funds»), sin permisos
para operar ni retirar. Valoración en EUR con los precios públicos.

Kraken da cada activo con su código interno y separa las variantes:
  XXBT / XBT.M (bitcoin), XETH / ETH2.S (ether en staking), ADA / ADA.S, DOT / DOT.S…
Aquí se agrupan por activo base (ADA + ADA.S = ADA) y se valoran así:
  1. con el par del activo contra EUR, si existe;
  2. si no, contra USD y se convierte a EUR con el par EUR/USD.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
import urllib.parse

import requests

API = "https://api.kraken.com"
EUR = {"ZEUR", "EUR"}
USD = {"ZUSD", "USD"}
# Variantes cuyo nombre no lleva a su activo base quitando el sufijo.
ALIAS = {"ETH2": "XETH", "ETH": "XETH", "XBT": "XXBT", "BTC": "XXBT", "EUR": "ZEUR", "USD": "ZUSD"}


def _privado(path: str) -> dict:
    return _privado_params(path, {})


def _privado_params(path: str, params: dict) -> dict:
    nonce = str(int(time.time() * 1000))
    datos = urllib.parse.urlencode({"nonce": nonce, **params})
    msg = path.encode() + hashlib.sha256((nonce + datos).encode()).digest()
    firma = hmac.new(base64.b64decode(os.environ["KRAKEN_API_SECRET"]), msg, hashlib.sha512)
    r = requests.post(API + path, data=datos, timeout=60, headers={
        "API-Key": os.environ["KRAKEN_API_KEY"],
        "API-Sign": base64.b64encode(firma.digest()).decode(),
        "Content-Type": "application/x-www-form-urlencoded",
    })
    r.raise_for_status()
    j = r.json()
    if j.get("error"):
        raise RuntimeError(j["error"])
    return j["result"]


def _publico(path: str, **params) -> dict:
    j = requests.get(API + path, params=params, timeout=60).json()
    if j.get("error"):
        raise RuntimeError(j["error"])
    return j["result"]


def _base(codigo: str, altnames: dict[str, str]) -> str:
    """'ADA.S' → 'ADA', 'ETH2.S' → 'XETH', 'XBT.M' → 'XXBT', 'XETH' → 'XETH'."""
    c = codigo.split(".")[0]
    c = ALIAS.get(c, c)
    return altnames.get(c, c)  # por si viene el altname (ETH) en vez del código (XETH)


def valorar_eur(cantidades: dict[str, float]) -> list[dict]:
    """Valora en EUR un diccionario {código: cantidad} con precios públicos de Kraken.
    Acepta códigos de Kraken (XXBT, ADA.S, ETH2.S…) o comunes (BTC, ETH). Agrupa variantes."""
    activos = _publico("/0/public/Assets")
    altnames = {v.get("altname", k): k for k, v in activos.items()}
    pares = _publico("/0/public/AssetPairs")
    agrupado: dict[str, float] = {}
    for codigo, cant in cantidades.items():
        b = _base(codigo, altnames)
        agrupado[b] = agrupado.get(b, 0.0) + cant

    def par(base: str, quotes: set[str]) -> str | None:
        for nombre, p in pares.items():
            if p.get("base") == base and p.get("quote") in quotes and not nombre.endswith(".d"):
                return nombre
        return None

    necesarios = {b: par(b, EUR) or par(b, USD) for b in agrupado if b not in EUR}
    eurusd = par("ZEUR", USD)
    consulta = sorted({p for p in necesarios.values() if p} | ({eurusd} if eurusd else set()))
    precios = {k: float(v["c"][0]) for k, v in _publico("/0/public/Ticker", pair=",".join(consulta)).items()} if consulta else {}
    usd_a_eur = 1 / precios[eurusd] if eurusd in precios else None
    filas = []
    for b, cant in sorted(agrupado.items()):
        if b in EUR:
            valor = cant
        else:
            p = necesarios.get(b)
            precio = precios.get(p) if p else None
            if precio is None:
                valor = None
            elif pares[p]["quote"] in EUR:
                valor = cant * precio
            else:
                valor = cant * precio * usd_a_eur if usd_a_eur else None
        nombre = activos.get(b, {}).get("altname", b)
        comun = {"XBT": "BTC", "XDG": "DOGE"}.get(nombre, nombre)
        filas.append({"activo": comun, "codigo": b, "cantidad": cant, "valor": round(valor, 2) if valor is not None else None})
    return filas


def ledger(desde_ts: float = 0) -> list[dict]:
    """Movimientos del ledger (requiere el permiso «Query Ledger Entries»): operaciones, staking,
    depósitos y retiradas. Se pagina de 50 en 50."""
    out, ofs = [], 0
    while True:
        r = _privado_params("/0/private/Ledgers", {"start": int(desde_ts), "ofs": ofs})
        lote = list(r.get("ledger", {}).items())
        out += [dict(v, id=k) for k, v in lote]
        ofs += len(lote)
        if not lote or ofs >= int(r.get("count", 0)):
            return out
        time.sleep(2)  # límite de consultas de la API privada


def posiciones() -> list[dict]:
    saldos = {k: float(v) for k, v in _privado("/0/private/Balance").items() if float(v) > 0}
    return [{"plataforma": "Kraken", "producto": f["activo"], "referencia": f["codigo"], "activo": f["activo"],
             "cantidad": f["cantidad"], "valor": f["valor"], "aportado": None,
             "metodo": f"api: {f['cantidad']:.8g} {f['activo']}" + ("" if f["valor"] is not None else " (sin precio)")}
            for f in valorar_eur(saldos)]
