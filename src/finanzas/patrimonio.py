"""Valoración de inversiones (DECISIONES §7).

MyInvestor no se puede conectar por Enable Banking (server_error en su pasarela), así que:

  participaciones = último punto de partida + compras posteriores
  valor           = participaciones × último valor liquidativo

- Punto de partida: la fila más reciente de `fondos_inicial` o de `conciliaciones`
  (con `participaciones`, o con `valor_real`, que se divide por el VL de ese día).
- Compras posteriores: si hay movimientos «Suscripcion lic» de la cuenta MYINV se usan
  tal cual; si no, se deducen de las transferencias a la cuenta de MyInvestor (IBAN_MYINV)
  repartidas con los pesos de config/cuentas.yaml.
"""
from __future__ import annotations

import os
import re

import pandas as pd

from .sheets import a_fecha, a_numero


def _fecha(s: pd.Series) -> pd.Series:
    return a_fecha(s)


def _vl(vls: pd.DataFrame, isin: str, fecha: pd.Timestamp) -> float | None:
    """Valor liquidativo del día o, si no lo hay, el último anterior."""
    v = vls[(vls["isin"] == isin) & (vls["fecha"] <= fecha)]
    return float(v.sort_values("fecha")["vl"].iloc[-1]) if len(v) else None


def _ts(x) -> pd.Timestamp | None:
    """Fecha de la configuración (YAML la lee como date o como texto) → Timestamp, o None si falta."""
    return pd.Timestamp(str(x)) if x not in (None, "") else None


def _en_ventana(fecha: pd.Timestamp, f: dict) -> bool:
    """¿Cuenta una compra de esa fecha para el fondo? `desde` y `hasta` (ambos opcionales, inclusive) separan
    clases del mismo fondo que comparten nombre en el extracto (D hasta el traspaso, S desde entonces)."""
    d, h = _ts(f.get("desde")), _ts(f.get("hasta"))
    return (d is None or fecha >= d) and (h is None or fecha <= h)


def vl_conocidos(cfg_inv: dict) -> pd.DataFrame:
    """Valores liquidativos de las operaciones de traspaso (los de la pantalla de órdenes de MyInvestor): sirven de
    suelo si la descarga pública falla y para valorar un fondo nuevo desde el día de su entrada."""
    filas = []
    for t in cfg_inv.get("myinvestor_traspasos") or []:
        for isin, fecha, vl in ((t["origen"], t["fecha_salida"], t.get("vl_origen")), (t["destino"], t["fecha_entrada"], t.get("vl_destino"))):
            if vl:
                filas.append({"isin": isin, "fecha": str(fecha), "vl": float(vl), "fuente": "orden de traspaso"})
    return pd.DataFrame(filas, columns=["isin", "fecha", "vl", "fuente"])


def myinvestor(mov: pd.DataFrame, cfg_inv: dict, iniciales: pd.DataFrame,
               conciliaciones: pd.DataFrame, vls: pd.DataFrame) -> pd.DataFrame:
    """Posición de cada fondo de MyInvestor. Además de la foto inicial y las compras:
    - `desde`/`hasta` de cada fondo: ventana de fechas de sus compras (una clase y otra con el mismo nombre).
    - `myinvestor_compras_confirmadas`: compras con sus participaciones reales (pantalla de órdenes); mandan sobre las
      estimadas hasta su última fecha.
    - `myinvestor_traspasos`: un traspaso entre fondos (p. ej. clase D → S) mueve las participaciones y el coste fiscal del
      origen al destino; no es una compra ni una venta. Lo «invertido» en la plataforma del destino arranca en el valor con
      que llega el traspaso (`importe`), que es como lo cuenta la web de MyInvestor."""
    fondos = cfg_inv["myinvestor_fondos"]
    por_isin = {f["isin"]: f for f in fondos}
    traspasos = cfg_inv.get("myinvestor_traspasos") or []
    confirmadas = cfg_inv.get("myinvestor_compras_confirmadas") or []
    vls = pd.concat([vls, vl_conocidos(cfg_inv)], ignore_index=True) if len(traspasos) else vls
    vls = vls.assign(fecha=_fecha(vls["fecha"]), vl=a_numero(vls["vl"])).dropna(subset=["fecha", "vl"])
    if iniciales.empty or "isin" not in iniciales.columns:
        iniciales = pd.DataFrame(columns=["isin", "fecha", "participaciones", "invertido"])
    iniciales = iniciales.assign(isin=iniciales["isin"].astype(str).str.strip().str.upper())
    ini = iniciales.assign(fecha=_fecha(iniciales["fecha"]), participaciones=a_numero(iniciales["participaciones"]),
                           invertido=a_numero(iniciales["invertido"]) if "invertido" in iniciales else 0.0,
                           coste_fiscal=a_numero(iniciales["coste_fiscal"]) if "coste_fiscal" in iniciales else float("nan"))
    con = conciliaciones.copy()
    con["fecha"] = _fecha(con["fecha"]) if len(con) else con.get("fecha")
    for c in ("participaciones", "valor_real"):
        con[c] = a_numero(con[c]) if c in con.columns and len(con) else float("nan")

    m = mov.copy()
    m["fecha"] = pd.to_datetime(m["fecha"])
    m["importe"] = m["importe"].astype(float)
    # Suscripciones: cargos de la cuenta MYINV cuyo concepto es un fondo (export CSV) o «Suscripcion lic…»
    patrones = "|".join(f"(?:{f['nombre_en_extracto']})" for f in fondos)
    subs_reales = m[(m["cuenta"] == "MYINV") & (m["importe"] < 0) & m["concepto"].str.upper().str.contains(patrones, regex=True)].copy()
    # Fecha de valor (día del VL aplicado) si viene del export: tipo_origen = «valor=AAAA-MM-DD».
    if "tipo_origen" in subs_reales.columns:
        fv = pd.to_datetime(subs_reales["tipo_origen"].fillna("").str.extract(r"valor=(\d{4}-\d{2}-\d{2})")[0], errors="coerce")
        subs_reales["fecha"] = fv.fillna(subs_reales["fecha"])
    iban = os.environ.get("IBAN_MYINV", "").replace(" ", "")
    transf = m[(m["cuenta"] != "MYINV") & (m["importe"] < 0) & iban.__ne__("")
               & m.get("iban_contraparte", pd.Series("", index=m.index)).fillna("").str.replace(" ", "").eq(iban)] if iban else m.iloc[0:0]
    fraccion, dias = float(cfg_inv.get("myinvestor_fraccion_invertida", 1.0)), int(cfg_inv.get("myinvestor_dias_ejecucion", 2))
    sin_fecha = pd.Timestamp("1900-01-01")

    def estado(isin: str, corte: pd.Timestamp | None = None, con_salidas: bool = True, _pila: tuple = ()) -> dict:
        """Posición de un fondo a una fecha (`corte`; sin ella, hoy). Sin salidas: la que había justo antes de salir
        en un traspaso, que es lo que se lleva el destino."""
        f = por_isin[isin]
        clave, peso = f["nombre_en_extracto"].upper(), float(f.get("peso", 0))
        i = ini[ini["isin"] == isin]
        entra_por_traspaso = any(t["destino"] == isin for t in traspasos)
        notas, origen = [], "foto inicial"
        if i.empty:
            if not entra_por_traspaso:
                vistos = ", ".join(sorted(set(ini["isin"]))) or "ninguno"
                return {"falta_foto": f"falta la foto inicial (ISIN en fondos_inicial: {vistos})", "clave": clave}
            base_f = foto_f = sin_fecha
            base_p, invertido, cf0, origen = 0.0, 0.0, 0.0, "cero (entra por traspaso)"
        else:
            foto_f = base_f = i["fecha"].iloc[0]
            base_p = float(i["participaciones"].iloc[0])
            invertido = float(i["invertido"].iloc[0]) if pd.notna(i["invertido"].iloc[0]) else 0.0
            cf0 = i["coste_fiscal"].iloc[0]
            # La conciliación más reciente sustituye al punto de partida.
            c = con[(con["isin"] == isin) & con["fecha"].notna()] if len(con) else con
            if len(c) and c["fecha"].max() > base_f:
                u = c.sort_values("fecha").iloc[-1]
                if pd.notna(u["participaciones"]):
                    base_f, base_p, origen = u["fecha"], float(u["participaciones"]), "conciliación"
                elif pd.notna(u["valor_real"]) and _vl(vls, isin, u["fecha"]):
                    base_f, base_p, origen = u["fecha"], float(u["valor_real"]) / _vl(vls, isin, u["fecha"]), "conciliación"

        # Compras (importe, participaciones o None si hay que calcularlas con el VL): reales si existen; si no, deducidas.
        reales = subs_reales[subs_reales["concepto"].str.upper().str.contains(clave, regex=True)]
        if len(reales):
            compras = [(r["fecha"], -r["importe"], None) for _, r in reales.iterrows()]
            tipo = "compras reales"
        else:
            compras = [(r["fecha"] + pd.Timedelta(days=dias), -r["importe"] * fraccion * peso, None) for _, r in transf.iterrows()]
            tipo = "compras deducidas"
        compras = [c_ for c_ in compras if _en_ventana(c_[0], f)]
        conf = [(pd.Timestamp(str(c_["fecha"])), float(c_["importe"]), float(c_["participaciones"])) for c_ in confirmadas if c_["isin"] == isin]
        if conf:  # lo confirmado manda sobre lo estimado hasta su última fecha
            ultima = max(c_[0] for c_ in conf)
            compras = [c_ for c_ in compras if c_[0] > ultima] + conf
            tipo += " + órdenes confirmadas"
        compras = [c_ for c_ in compras if corte is None or c_[0] <= corte]

        part, sin_vl = base_p, 0
        for fecha, importe, p_conf in compras:
            if fecha <= base_f:
                continue
            if p_conf is not None:
                part += p_conf
                continue
            v = _vl(vls, isin, fecha)
            if v:
                part += importe / v
            else:
                sin_vl += 1
        nuevas = [(fe, imp) for fe, imp, _ in compras if fe > foto_f]
        inv = invertido + sum(imp for _, imp in nuevas)
        cf = cf0 + sum(imp for _, imp in nuevas) if pd.notna(cf0) else float("nan")

        # Traspasos: lo que entra se lleva participaciones, lo invertido y el coste fiscal del origen; lo que sale, lo resta.
        for t in traspasos:
            fe_sal, fe_ent = _ts(t["fecha_salida"]), _ts(t["fecha_entrada"])
            if t["destino"] == isin and t["origen"] not in _pila and (corte is None or fe_ent <= corte):
                o = estado(t["origen"], corte=fe_sal, con_salidas=False, _pila=_pila + (isin,))
                frac = min(1.0, float(t["participaciones_origen"]) / o["part"]) if o.get("part", 0) > 0 else 1.0
                part += float(t["participaciones_destino"])
                inv += float(t.get("importe", o.get("inv", 0.0) * frac))  # la plataforma cuenta lo invertido desde el valor con que llega
                cf = cf + o["cf"] * frac if pd.notna(cf) and pd.notna(o.get("cf", float("nan"))) else float("nan")
                notas.append(f"traspaso de {t['origen']} el {fe_ent:%d/%m/%Y} ({float(t['participaciones_destino']):.2f} part.)")
            if t["origen"] == isin and con_salidas and (corte is None or fe_sal <= corte):
                o = estado(isin, corte=fe_sal, con_salidas=False, _pila=_pila + (isin,))
                frac = min(1.0, float(t["participaciones_origen"]) / o["part"]) if o.get("part", 0) > 0 else 1.0
                part -= float(t["participaciones_origen"])
                inv -= o.get("inv", 0.0) * frac
                cf = cf - o["cf"] * frac if pd.notna(cf) and pd.notna(o.get("cf", float("nan"))) else float("nan")
                notas.append(f"traspaso a {t['destino']} el {fe_sal:%d/%m/%Y} ({float(t['participaciones_origen']):.2f} part.)")
        return {"part": part, "inv": inv, "cf": cf, "base_f": base_f, "origen": origen, "tipo": tipo, "sin_vl": sin_vl,
                "notas": notas, "clave": clave}

    filas = []
    for f in fondos:
        isin = f["isin"]
        e = estado(isin)
        if "falta_foto" in e:
            filas.append({"plataforma": "MyInvestor", "producto": e["clave"], "referencia": isin, "valor": None,
                          "aportado": None, "metodo": e["falta_foto"]})
            continue
        part = e["part"]
        salio = any(t["origen"] == isin for t in traspasos)
        if salio and abs(part) < 0.05:
            continue  # traspasado entero: la posición vive ahora en el fondo de destino
        hoy = _vl(vls, isin, pd.Timestamp.today().normalize() + pd.Timedelta(days=1))
        v_ = vls[(vls["isin"] == isin)]
        fecha_vl = v_["fecha"].max().strftime("%Y-%m-%d") if len(v_) else ""
        desde = "" if e["base_f"] == sin_fecha else f" {e['base_f']:%d/%m/%Y}"
        metodo = f"estimado desde {e['origen']}{desde}, {e['tipo']}, {part:.4f} part." \
            + "".join(f", {n}" for n in e["notas"]) \
            + (f" ({e['sin_vl']} compras sin VL)" if e["sin_vl"] else "") \
            + (f" — queda un residuo de {part:.2f} part. tras el traspaso: revisa la foto inicial" if salio and part > 0.05 else "")
        filas.append({"plataforma": "MyInvestor", "producto": e["clave"], "referencia": isin, "activo": isin, "cantidad": round(part, 6),
                      "valor": round(part * hoy, 2) if hoy else None, "aportado": round(e["inv"], 2),
                      "coste_fiscal": round(e["cf"], 2) if pd.notna(e["cf"]) else None,
                      "valor_fondos": round(part * hoy, 2) if hoy else None, "fecha_valor": fecha_vl, "metodo": metodo})
    return pd.DataFrame(filas)


# ----------------------------------------------------------------- conciliación con Portfolio Performance
PLATAFORMA_PP = {"Indexa": "Indexa Capital", "MyInvestor": "MyInvestor", "Trade_Republic": "Trade Republic",
                 "Kraken": "Kraken", "Revolut_X": "Revolut X", "RevolutX": "Revolut X"}


def conciliar(pp_pos: pd.DataFrame, oficiales: pd.DataFrame, tol_fondos: float = 0.001, tol_cripto: float = 0.01,
              alias: dict | None = None, ops_oficiales: pd.DataFrame | None = None, pp_hasta: dict | None = None,
              pp_desactualizado: list | None = None, explicadas: dict | None = None) -> pd.DataFrame:
    """Compara las posiciones de Portfolio Performance con las oficiales.
    - Fondos (ISIN): por plataforma y activo.
    - Cripto: por activo, sumando todas las cuentas (las monedas se mueven entre exchanges).
    Manda la oficial; `estado` dice si cuadra (diferencia relativa ≤ 0,1 % en fondos y ≤ 1 % en
    cripto, donde las comisiones de red dejan restos)."""
    if pp_pos.empty or oficiales.empty:
        return pd.DataFrame()
    pp_ = pp_pos.assign(cantidad=a_numero(pp_pos["cantidad"]))
    if alias:
        pp_["activo"] = pp_["activo"].replace(alias)
    # Cripto registrada fuera de PP: compras/ventas oficiales posteriores a la última fecha de PP y
    # todas las recompensas de staking (PP no las tiene).
    if ops_oficiales is not None and not ops_oficiales.empty:
        o = ops_oficiales.assign(cantidad=a_numero(ops_oficiales["cantidad"]))
        # Por plataforma: si PP no tiene esa cuenta (p. ej. Revolut X), cuentan todas sus operaciones.
        corte = o["plataforma"].map(lambda p: (pp_hasta or {}).get(p, "0000"))
        nuevas = o["tipo"].isin(["compra", "venta"]) & (o["fecha"] > corte)
        o = o[nuevas | (o["tipo"] == "ingreso_en_especie")]
        pp_ = pd.concat([pp_, pd.DataFrame({"cuenta_pp": "oficial posterior", "activo": o["activo"], "cantidad": o["cantidad"]})], ignore_index=True)
    of = oficiales.assign(cantidad=a_numero(oficiales["cantidad"]))
    es_fondo = lambda a: bool(re.match(r"^([A-Z]{2}[A-Z0-9]{9}\d|N\d{4})$", str(a)) or str(a).startswith("0P"))  # noqa: E731
    pp_["plataforma"] = pp_["cuenta_pp"].map(PLATAFORMA_PP).fillna("Cripto")
    of = of.assign(plataforma=of["plataforma"].where(of["activo"].map(es_fondo), "Cripto"))
    pp_ = pp_.assign(plataforma=pp_["plataforma"].where(pp_["activo"].map(es_fondo), "Cripto"))
    a = pp_.groupby(["plataforma", "activo"], as_index=False)["cantidad"].sum().rename(columns={"cantidad": "cantidad_pp"})
    b = of.groupby(["plataforma", "activo"], as_index=False).agg(cantidad_oficial=("cantidad", "sum"), valor_oficial=("valor", lambda v: a_numero(v).sum()))
    c = a.merge(b, on=["plataforma", "activo"], how="outer")
    c = c[(c["cantidad_pp"].fillna(0).abs() > 1e-6) | (c["cantidad_oficial"].fillna(0).abs() > 1e-6)]
    dif = c["cantidad_oficial"].fillna(0) - c["cantidad_pp"].fillna(0)
    rel = dif.abs() / c[["cantidad_pp", "cantidad_oficial"]].abs().max(axis=1).replace(0, float("nan"))
    c["diferencia"] = dif
    c["estado"] = "cuadra"
    tol = c["plataforma"].map(lambda p: tol_cripto if p == "Cripto" else tol_fondos)
    c.loc[rel > tol, "estado"] = "no cuadra"
    # Plataformas donde PP dejó de actualizarse: tener más participaciones oficiales es lo esperado.
    stale = c["plataforma"].isin(pp_desactualizado or []) & (c["estado"] == "no cuadra") & (dif > 0)
    c.loc[stale, "estado"] = "PP desactualizado (manda oficial)"
    c.loc[c["cantidad_oficial"].isna(), "estado"] = "solo en PP"
    c.loc[c["cantidad_pp"].isna(), "estado"] = "solo oficial"
    # Diferencias ya revisadas a mano (config `diferencias_explicadas`): activo → motivo.
    for activo, motivo in (explicadas or {}).items():
        c.loc[(c["activo"] == activo) & (c["estado"] != "cuadra"), "estado"] = f"explicada: {motivo}"
    return c.sort_values(["estado", "plataforma", "activo"]).reset_index(drop=True)


# ----------------------------------------------------------------- Trade Republic (valores)
RE_TR = re.compile(r"(Savings plan execution|Buy trade|Sell trade)\s+([A-Z]{2}[A-Z0-9]{9}\d)\s+(.+?),.*?quantity:\s*(?:Operar\s*)?([\d.]+)", re.I)


def trade_republic(mov: pd.DataFrame) -> list[dict]:
    """Posiciones en valores de Trade Republic deducidas de sus extractos: cada compra o venta
    lleva el ISIN y la cantidad en el concepto. Valor = cantidad × precio de la última operación."""
    t = mov[mov["cuenta"] == "TR"]
    ops = []
    for _, r in t.iterrows():
        m = RE_TR.search(str(r["concepto"]))
        if m:
            q = float(m.group(4)) * (-1 if m.group(1).lower().startswith("sell") else 1)
            ops.append({"fecha": r["fecha"], "activo": m.group(2).upper(), "nombre": m.group(3).strip(), "cantidad": q,
                        "importe": abs(float(r["importe"]))})
    if not ops:
        return []
    o = pd.DataFrame(ops).sort_values("fecha")
    filas = []
    for isin, g in o.groupby("activo"):
        ult = g.iloc[-1]
        precio = ult["importe"] / abs(ult["cantidad"]) if ult["cantidad"] else None
        cant = g["cantidad"].sum()
        aportado = g.loc[g["cantidad"] > 0, "importe"].sum() - g.loc[g["cantidad"] < 0, "importe"].sum()
        filas.append({"plataforma": "Trade Republic", "producto": ult["nombre"], "referencia": isin, "activo": isin,
                      "cantidad": round(cant, 6), "valor": round(cant * precio, 2) if precio else None, "aportado": round(aportado, 2),
                      "coste_fiscal": round(aportado, 2),
                      "metodo": f"extractos: {cant:.6f} uds. × precio de la última compra ({ult['fecha']})"})
    return filas


# ----------------------------------------------------------------- Kraken ledger → operaciones
ALIAS_KRAKEN = {"XXBT": "BTC", "XBT": "BTC", "XETH": "ETH", "ETH2": "ETH", "ZEUR": "EUR", "ZUSD": "USD", "XXDG": "DOGE"}


def activo_kraken(codigo: str) -> str:
    """XXBT → BTC, ADA.S → ADA, y variantes de staking con plazo: DOT28.S → DOT, KSM07.S → KSM."""
    original = str(codigo).upper()
    c = original.split(".")[0]
    if "." in original and re.fullmatch(r"[A-Z]{2,}\d{2}", c):
        c = c[:-2]
    return ALIAS_KRAKEN.get(c, c)


def kraken_operaciones(ledger: list[dict]) -> pd.DataFrame:
    """Ledger de Kraken → filas de `cripto_operaciones`: compras/ventas (con su importe en EUR si la
    contrapartida es en euros), recompensas de staking (ingreso en especie), depósitos y retiradas.
    Los movimientos internos entre saldo y staking se descartan."""
    if not ledger:
        return pd.DataFrame()
    l = pd.DataFrame(ledger)
    l["neto"] = l["amount"].astype(float) - l["fee"].astype(float)
    l["activo"] = l["asset"].map(activo_kraken)
    eur_por_ref = l[l["activo"] == "EUR"].groupby("refid")["neto"].sum()
    filas = []
    for _, r in l.iterrows():
        tipo, sub = r.get("type", ""), r.get("subtype", "")
        if r["activo"] == "EUR" and tipo == "trade":
            continue  # la pata en euros ya va como importe de la operación
        if tipo == "trade" or tipo == "spend" or tipo == "receive":
            t = "compra" if r["neto"] > 0 else "venta"
        elif tipo in ("staking", "earn") and r["neto"] > 0 and sub not in ("allocation", "deallocation", "migration"):
            t = "ingreso_en_especie"
        elif tipo == "deposit":
            t = "deposito"
        elif tipo == "withdrawal":
            t = "retirada"
        else:
            continue  # transfer, staking interno, ajustes…
        filas.append({"fecha": pd.to_datetime(float(r["time"]), unit="s").strftime("%Y-%m-%d"), "plataforma": "Kraken", "tipo": t,
                      "activo": r["activo"], "cantidad": r["neto"], "precio": None,
                      "importe": round(abs(eur_por_ref.get(r["refid"], float("nan"))), 2) if t in ("compra", "venta") else None,
                      "divisa": "EUR", "id": "kraken-" + str(r["id"]), "fuente": "kraken_ledger"})
    return pd.DataFrame(filas)


# ----------------------------------------------------------------- detalle y categorías
CATEGORIAS = {  # (plataforma, cuenta/producto) → categoría para agrupar
    ("Indexa Capital", "Fondos indexados"): "Fondos Indexa",
    ("Indexa Capital", "Plan de pensiones"): "Plan de pensiones Indexa",
    ("MyInvestor", None): "Fondos MyInvestor",
    ("Trade Republic", None): "ETF y acciones Trade Republic",
    ("Kraken", None): "Cripto", ("Revolut X", None): "Cripto",
    ("Cuentas", None): "Efectivo en cuentas",
}


def categoria(plataforma: str, cuenta: str | None = None) -> str:
    return CATEGORIAS.get((plataforma, cuenta)) or CATEGORIAS.get((plataforma, None)) or plataforma


def detalle(pos: list[dict], indexa_det: list[dict], manuales: pd.DataFrame, precio_isin) -> pd.DataFrame:
    """Una fila por activo (fondo, ETF, cripto, cuenta) con su categoría: base de las vistas del dashboard."""
    filas = []
    for d in indexa_det:
        filas.append({"categoria": categoria("Indexa Capital", d["cuenta"]), "plataforma": "Indexa Capital", "activo": d["activo"],
                      "nombre": d["nombre"], "cantidad": d["cantidad"], "valor": d["valor"], "coste": d["coste"],
                      "coste_plataforma": d["coste"], "fecha_valor": d.get("fecha", ""), "fuente": "API Indexa"})
    for p in pos:
        plat = p.get("plataforma")
        if plat == "Indexa Capital":
            if p.get("efectivo"):
                filas.append({"categoria": categoria(plat, p.get("producto")), "plataforma": plat, "activo": "EUR",
                              "nombre": f"Efectivo ({p.get('producto')})", "cantidad": p["efectivo"], "valor": p["efectivo"],
                              "coste": p["efectivo"], "coste_plataforma": p["efectivo"], "fecha_valor": p.get("fecha_valor", ""), "fuente": "API Indexa"})
            continue
        filas.append({"categoria": categoria(plat), "plataforma": plat, "activo": p.get("activo") or p.get("producto"),
                      "nombre": p.get("producto"), "cantidad": p.get("cantidad"), "valor": p.get("valor"),
                      "coste": p.get("coste_fiscal") if plat != "Cuentas" else p.get("valor"),
                      # Coste en la plataforma actual (incluye el valor con que llegaron los traspasos).
                      "coste_plataforma": p.get("aportado") if p.get("aportado") is not None else (p.get("coste_fiscal") if plat != "Cuentas" else p.get("valor")),
                      "fecha_valor": p.get("fecha_valor", ""), "fuente": p.get("metodo", "")})
    for _, m in (manuales if manuales is not None else pd.DataFrame()).iterrows():
        cant = a_numero(pd.Series([m.get("cantidad", "")])).iloc[0]
        valor = a_numero(pd.Series([m.get("valor", "")])).iloc[0]
        fuente = f"manual ({m.get('fecha', '')})"
        isin = str(m.get("isin", "")).strip().upper()
        if isin and pd.notna(cant):
            pr = precio_isin(isin)
            if pr:
                valor, fuente = cant * pr["vl"], f"cantidad manual × precio público del {pr['fecha']}"
        filas.append({"categoria": m.get("categoria") or "Otros", "plataforma": m.get("plataforma", ""), "activo": isin or m.get("nombre", ""),
                      "nombre": m.get("nombre", ""), "cantidad": cant, "valor": valor,
                      "coste": a_numero(pd.Series([m.get("coste_fiscal", "")])).iloc[0],
                      "coste_plataforma": a_numero(pd.Series([m.get("coste_fiscal", "")])).iloc[0], "fecha_valor": m.get("fecha", ""), "fuente": fuente})
    return pd.DataFrame(filas)


def por_categoria(det: pd.DataFrame, costes_manual: pd.DataFrame, historico: pd.DataFrame, aportado_hoy: dict) -> pd.DataFrame:
    """Suma por categoría. Si hay un coste fiscal apuntado a mano para una categoría (pestaña
    `costes_fiscales`, copiado de la web de la entidad), se usa ese + lo aportado desde esa fecha."""
    d_ = det.assign(valor=a_numero(det["valor"]), coste=a_numero(det["coste"]),
                    coste_plataforma=a_numero(det["coste_plataforma"]) if "coste_plataforma" in det.columns else float("nan"))
    c = d_.groupby("categoria", as_index=False).agg(
        valor=("valor", "sum"), coste_fiscal=("coste", lambda x: x.sum(min_count=1)),
        valor_compra=("coste_plataforma", lambda x: x.sum(min_count=1)), fecha_valor=("fecha_valor", "max"))
    c["nota"] = ""
    if "origen_coste" in det.columns:
        fis = det[det["origen_coste"].astype(str).str.startswith("fiscal")].groupby("categoria").size()
        for cat_, n in fis.items():
            c.loc[c["categoria"] == cat_, "nota"] = f"coste fiscal por fondo ({n} fondos, de la web)"
    if costes_manual is not None and not costes_manual.empty:
        cm = costes_manual.assign(fecha=_fecha(costes_manual["fecha"]), coste_fiscal=a_numero(costes_manual["coste_fiscal"]))
        if "isin" in cm.columns:
            cm = cm[cm["isin"].astype(str).str.strip() == ""]
        con_activo = set(det.loc[det.get("origen_coste", pd.Series("", index=det.index)).astype(str).str.startswith("fiscal"), "categoria"])
        cm = cm[~cm["categoria"].isin(con_activo)]
        for _, m in cm.sort_values("fecha").groupby("categoria").tail(1).iterrows():
            ap_hoy = aportado_hoy.get(m["categoria"])
            ap_ant = None
            if historico is not None and not historico.empty and "categoria" in historico.columns:
                h = historico[(historico["categoria"] == m["categoria"]) & (_fecha(historico["fecha"]) <= m["fecha"])]
                if len(h):
                    ap_ant = a_numero(h.sort_values("fecha")["aportado"]).iloc[-1]
            delta = (ap_hoy - ap_ant) if (ap_hoy is not None and ap_ant is not None and pd.notna(ap_ant)) else 0.0
            # El efectivo de la cuenta no tiene plusvalía: su coste es su valor.
            efectivo = a_numero(det.loc[(det["categoria"] == m["categoria"]) & (det["activo"] == "EUR"), "valor"]).sum()
            c.loc[c["categoria"] == m["categoria"], "coste_fiscal"] = m["coste_fiscal"] + delta + efectivo
            c.loc[c["categoria"] == m["categoria"], "nota"] = f"coste fiscal de la web a {m['fecha']:%d/%m/%Y}" + (f" + {delta:,.0f} € aportados después" if delta else "")
    return c


def trade_republic_cartera(ops: pd.DataFrame, vls: pd.DataFrame) -> list[dict]:
    """Cartera de Trade Republic a partir del CSV de transacciones: FIFO por ISIN (coste fiscal de
    lo que queda) y valor = títulos × último precio conocido (pestaña `valores_liquidativos` o, si
    no hay, el de la última compra). Los traspasos internos (MIGRATION) no cambian nada; un
    desdoblamiento (SPLIT) reparte los títulos nuevos entre los lotes sin cambiar el coste."""
    if ops is None or ops.empty:
        return []
    o = ops.assign(cantidad=pd.to_numeric(ops["cantidad"], errors="coerce").fillna(0.0),
                   importe=pd.to_numeric(ops["importe"], errors="coerce").fillna(0.0),
                   precio=pd.to_numeric(ops["precio"], errors="coerce")).sort_values(["fecha", "id"])
    v = vls.assign(fecha=_fecha(vls["fecha"]), vl=a_numero(vls["vl"])).dropna(subset=["vl"]) if vls is not None and not vls.empty else pd.DataFrame()
    filas = []
    for isin, g in o.groupby("isin"):
        lotes: list[list[float]] = []  # [cantidad, coste]
        for _, r in g.iterrows():
            if r["tipo"] in ("MIGRATION", "DELIVERY_INBOUND", "DELIVERY_OUTBOUND"):
                continue
            if r["tipo"] == "SPLIT":
                total = sum(l[0] for l in lotes)
                if total > 0:
                    f = (total + r["cantidad"]) / total
                    for l in lotes:
                        l[0] *= f
                continue
            if r["cantidad"] > 0:
                lotes.append([r["cantidad"], r["importe"]])
            elif r["cantidad"] < 0:
                falta = -r["cantidad"]
                while falta > 1e-12 and lotes:
                    usa = min(falta, lotes[0][0])
                    lotes[0][1] -= lotes[0][1] * usa / lotes[0][0]
                    lotes[0][0] -= usa
                    falta -= usa
                    if lotes[0][0] <= 1e-12:
                        lotes.pop(0)
        cant = sum(l[0] for l in lotes)
        if cant <= 1e-9:
            continue
        coste = sum(l[1] for l in lotes)
        precio, fecha_p, origen = None, "", ""
        if not v.empty and (v["isin"] == isin).any():
            u = v[v["isin"] == isin].sort_values("fecha").iloc[-1]
            precio, fecha_p, origen = float(u["vl"]), f"{u['fecha']:%Y-%m-%d}", "precio publicado"
        compras = g[(g["cantidad"] > 0) & g["precio"].notna()]
        if precio is None and len(compras):
            u = compras.iloc[-1]
            precio, fecha_p, origen = float(u["precio"]), u["fecha"], "precio de la última compra"
        filas.append({"plataforma": "Trade Republic", "producto": g["nombre"].iloc[-1], "referencia": isin, "activo": isin,
                      "cantidad": round(cant, 6), "valor": round(cant * precio, 2) if precio else None,
                      "aportado": round(coste, 2), "coste_fiscal": round(coste, 2), "fecha_valor": fecha_p,
                      "metodo": f"CSV de Trade Republic: {cant:.6f} títulos × {origen}"})
    return filas


def costes_por_activo(det: pd.DataFrame, costes_manual: pd.DataFrame, base: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Aplica costes fiscales apuntados por fondo (filas de `costes_fiscales` con `isin`).

    En cada fila basta con uno de estos datos de la web de la entidad:
      - `coste_fiscal`, o
      - `plusvalia_latente` (+ opcionalmente `valor` en esa fecha): coste = valor − plusvalía.
    Para seguir al día sin volver a apuntarlo, se guarda el coste de la API en esa fecha
    (pestaña `costes_fiscales_base`) y se suma lo que haya cambiado después (compras nuevas).
    Devuelve (detalle con `coste` y `origen_coste` actualizados, base actualizada)."""
    det = det.copy()
    det["origen_coste"] = det.get("origen_coste", "")
    if costes_manual is None or costes_manual.empty or "isin" not in costes_manual.columns:
        return det, base if base is not None else pd.DataFrame(columns=["isin", "fecha", "coste_api"])
    cm = costes_manual[costes_manual["isin"].astype(str).str.strip() != ""].copy()
    if cm.empty:
        return det, base if base is not None else pd.DataFrame(columns=["isin", "fecha", "coste_api"])
    cm["isin"] = cm["isin"].astype(str).str.strip().str.upper()
    cm["fecha_dt"] = _fecha(cm["fecha"])
    for c in ("coste_fiscal", "plusvalia_latente", "valor"):
        cm[c] = a_numero(cm[c]) if c in cm.columns else float("nan")
    base = base.copy() if base is not None and not base.empty else pd.DataFrame(columns=["isin", "fecha", "coste_api"])
    det_coste = a_numero(det["coste"])
    det_valor = a_numero(det["valor"])
    for _, m in cm.sort_values("fecha_dt").groupby("isin").tail(1).iterrows():
        sel = det["activo"].astype(str).str.upper() == m["isin"]
        if not sel.any():
            continue
        coste_api_hoy = det_coste[sel].sum()
        b = base[(base["isin"] == m["isin"]) & (base["fecha"] == str(m["fecha"]))]
        if b.empty:  # primera vez que se ve esta fila: se toma el coste de la API de hoy como referencia
            base.loc[len(base)] = [m["isin"], str(m["fecha"]), coste_api_hoy]
            coste_api_base = coste_api_hoy
        else:
            coste_api_base = float(a_numero(b["coste_api"]).iloc[-1])
        if pd.notna(m["coste_fiscal"]):
            web = m["coste_fiscal"]
        elif pd.notna(m["plusvalia_latente"]):
            valor_web = m["valor"] if pd.notna(m["valor"]) else det_valor[sel].sum()
            web = valor_web - m["plusvalia_latente"]
        else:
            continue
        nuevo = web + (coste_api_hoy - coste_api_base)
        det.loc[sel, "coste"] = nuevo * (det_coste[sel] / coste_api_hoy if coste_api_hoy else 1 / sel.sum())
        det.loc[sel, "origen_coste"] = f"fiscal de la web a {m['fecha_dt']:%d/%m/%Y}" + \
            (f" + {coste_api_hoy - coste_api_base:,.0f} € comprados después" if abs(coste_api_hoy - coste_api_base) > 0.5 else "")
    return det, base


def coste_cripto(det: pd.DataFrame, lotes: pd.DataFrame) -> pd.DataFrame:
    """Coste fiscal de cada cripto = coste de sus lotes FIFO abiertos, repartido entre plataformas
    según la cantidad que hay en cada una (el FIFO es global por activo)."""
    if lotes is None or lotes.empty or det.empty:
        return det
    det = det.copy()
    det["origen_coste"] = det["origen_coste"] if "origen_coste" in det.columns else ""
    coste = lotes.assign(coste=a_numero(lotes["coste"])).groupby("activo")["coste"].sum()
    es = (det["categoria"] == "Cripto") & (det["activo"] != "EUR")
    cant = a_numero(det["cantidad"])
    total = cant[es].groupby(det.loc[es, "activo"]).sum()
    for i in det.index[es]:
        a = det.at[i, "activo"]
        if a in coste.index and total.get(a, 0):
            det.at[i, "coste"] = round(coste[a] * cant[i] / total[a], 2)
            det.at[i, "coste_plataforma"] = det.at[i, "coste"]
            det.at[i, "origen_coste"] = "FIFO de tus lotes (staking y sin registrar a coste 0)"
    eur_ = (det["categoria"] == "Cripto") & (det["activo"] == "EUR")
    det.loc[eur_, "coste"] = det.loc[eur_, "valor"]
    det.loc[eur_, "coste_plataforma"] = det.loc[eur_, "valor"]
    return det
