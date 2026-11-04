"""Fiscalidad (DECISIONES §7 bis). Orientativo: para declarar, revisar con un asesor.

Cripto — FIFO por activo, sumando todos los exchanges (los traspasos entre cuentas propias
no son ventas: el coste viaja con las monedas):
  - compras y ventas de PP hasta su última fecha por plataforma; después, las oficiales
    (Kraken ledger, Revolut X);
  - cada permuta cripto-cripto es una venta (PP la registra como venta + compra con su valor);
  - entradas sin compra (staking, airdrops, promociones): lote con el valor apuntado en PP;
    staking de Kraken y unidades de más sin registrar: coste 0 (criterio conservador);
  - comisiones pagadas en cripto, restos y pérdidas (delisting, quiebra): salen sin precio.
Fondos — no se calcula FIFO: la gestora calcula la ganancia y retiene al reembolsar. Se
estima con el coste fiscal oficial (el que arrastran los traspasos).
"""
from __future__ import annotations

from collections import deque

import numpy as np
import pandas as pd

TRAMOS_AHORRO = [(6_000, 0.19), (50_000, 0.21), (200_000, 0.23), (300_000, 0.27), (float("inf"), 0.30)]
FIAT = {"EUR", "USD", "GBP"}


def cuota_ahorro(base: float) -> float:
    """Cuota de la base del ahorro (escala estatal + autonómica, territorio común, 2025–2026)."""
    if base <= 0:
        return 0.0
    cuota, desde = 0.0, 0.0
    for hasta, tipo in TRAMOS_AHORRO:
        tramo = min(base, hasta) - desde
        if tramo <= 0:
            break
        cuota += tramo * tipo
        desde = hasta
    return round(cuota, 2)


def _eventos(pp_ops: pd.DataFrame, oficiales: pd.DataFrame, pp_hasta: dict) -> pd.DataFrame:
    """Une PP y operaciones oficiales en una lista de eventos de cripto: alta (lote) o baja."""
    ev = []
    if not pp_ops.empty:
        p = pp_ops.copy()
        for c in ("cantidad", "importe"):
            p[c] = pd.to_numeric(p[c], errors="coerce")
        p = p[~p["activo"].str.match(r"^([A-Z]{2}[A-Z0-9]{9}\d|0P|N\d{4}$)") & ~p["activo"].isin(FIAT)]  # solo cripto
        for _, r in p.iterrows():
            t, clase, q, imp = r["tipo"], str(r.get("clase", "")), abs(r["cantidad"] or 0), abs(r["importe"] or 0)
            if not q or t.startswith("traspaso") or r.get("pareja"):
                continue  # traspasos entre cuentas propias: no cambian el coste
            if t in ("compra", "entrada"):
                origen = "compra" if t == "compra" else (clase or "entrada")
                ev.append((r["fecha"], r["activo"], "alta", q, imp, origen, "PP"))
            elif t == "venta":
                ev.append((r["fecha"], r["activo"], "venta", q, imp, "venta", "PP"))
            else:  # salida sin pareja: comisión, resto, pérdida…
                ev.append((r["fecha"], r["activo"], "baja", q, 0.0, clase or "salida", "PP"))
    if not oficiales.empty:
        o = oficiales.copy()
        for c in ("cantidad", "importe"):
            o[c] = pd.to_numeric(o[c], errors="coerce")
        corte = o["plataforma"].map(lambda x: (pp_hasta or {}).get(x, "0000"))
        o = o[(o["fecha"] > corte) | (o["tipo"] == "ingreso_en_especie")]
        for _, r in o.iterrows():
            q = abs(r["cantidad"] or 0)
            if not q or r["activo"] in FIAT:
                continue
            if r["tipo"] == "compra":
                ev.append((r["fecha"], r["activo"], "alta", q, abs(r["importe"]) if pd.notna(r["importe"]) else 0.0,
                           "compra" if pd.notna(r["importe"]) else "compra sin importe en EUR", r["plataforma"]))
            elif r["tipo"] == "venta":
                ev.append((r["fecha"], r["activo"], "venta", q, abs(r["importe"]) if pd.notna(r["importe"]) else np.nan, "venta", r["plataforma"]))
            elif r["tipo"] == "ingreso_en_especie":
                ev.append((r["fecha"], r["activo"], "alta", q, 0.0, "staking (coste 0)", r["plataforma"]))
    e = pd.DataFrame(ev, columns=["fecha", "activo", "evento", "cantidad", "importe", "origen", "fuente"])
    orden = {"alta": 0, "baja": 1, "venta": 2}  # el mismo día, primero las altas
    return e.assign(_o=e["evento"].map(orden)).sort_values(["fecha", "_o"], kind="stable").drop(columns="_o").reset_index(drop=True)


def fifo_cripto(pp_ops: pd.DataFrame, oficiales: pd.DataFrame, pp_hasta: dict | None = None,
                posiciones_hoy: dict | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Devuelve (ventas, lotes abiertos).
    ventas: una fila por venta o permuta, con coste FIFO y ganancia, y el ejercicio fiscal.
    lotes:  lo que queda de cada compra; si `posiciones_hoy` tiene más unidades que los lotes,
            la diferencia se añade como lote de coste 0 («sin registrar»)."""
    ev = _eventos(pp_ops, oficiales, pp_hasta or {})
    lotes: dict[str, deque] = {}
    ventas = []
    for _, r in ev.iterrows():
        cola = lotes.setdefault(r["activo"], deque())
        if r["evento"] == "alta":
            cola.append([r["fecha"], r["cantidad"], r["importe"], r["origen"]])
            continue
        falta, coste = r["cantidad"], 0.0
        while falta > 1e-12 and cola:
            lote = cola[0]
            usa = min(falta, lote[1])
            coste += lote[2] * usa / lote[1] if lote[1] else 0.0
            lote[2] -= lote[2] * usa / lote[1] if lote[1] else 0.0
            lote[1] -= usa
            falta -= usa
            if lote[1] <= 1e-12:
                cola.popleft()
        if r["evento"] == "venta":
            ventas.append({"fecha": r["fecha"], "ejercicio": int(str(r["fecha"])[:4]), "activo": r["activo"], "cantidad": r["cantidad"],
                           "importe_venta": r["importe"], "coste_fifo": round(coste, 2),
                           "ganancia": round(r["importe"] - coste, 2) if pd.notna(r["importe"]) else np.nan,
                           "sin_lote": round(falta, 10), "fuente": r["fuente"]})
    filas = [{"activo": a, "fecha_compra": l[0], "cantidad": l[1], "coste": round(l[2], 2), "origen": l[3]}
             for a, cola in lotes.items() for l in cola if l[1] > 1e-10]
    lot = pd.DataFrame(filas, columns=["activo", "fecha_compra", "cantidad", "coste", "origen"])
    for activo, cant in (posiciones_hoy or {}).items():
        extra = cant - lot.loc[lot["activo"] == activo, "cantidad"].sum()
        if extra > 1e-8:
            lot.loc[len(lot)] = [activo, "", extra, 0.0, "sin registrar (coste 0)"]
    return pd.DataFrame(ventas), lot


def simular_venta(lotes: pd.DataFrame, activo: str, cantidad: float, precio: float) -> dict:
    """Ganancia FIFO de vender hoy `cantidad` de `activo` a `precio` (sin tocar los lotes)."""
    l = lotes[lotes["activo"] == activo].copy()
    l["fecha_orden"] = l["fecha_compra"].replace("", "9999")
    falta, coste = cantidad, 0.0
    for _, r in l.sort_values("fecha_orden").iterrows():
        usa = min(falta, r["cantidad"])
        coste += r["coste"] * usa / r["cantidad"] if r["cantidad"] else 0.0
        falta -= usa
        if falta <= 1e-12:
            break
    ingreso = cantidad * precio
    return {"ingreso": round(ingreso, 2), "coste": round(coste, 2), "ganancia": round(ingreso - coste, 2), "sin_lote": max(falta, 0.0)}
