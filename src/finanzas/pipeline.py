"""Ejecución completa: extraer → guardar raw → clasificar → escribir la hoja.

    python -m finanzas.pipeline            # todo
    python -m finanzas.pipeline --sin-bancos   # solo reclasificar (tras editar reglas o ajustes)

Idempotente: la capa raw se une por `tx_id` y todo lo demás se recalcula entero desde
raw + reglas + ajustes. Ejecutarlo dos veces seguidas deja la hoja igual.
"""
from __future__ import annotations

import argparse
import json
import os
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml

from . import fiscal, patrimonio, reglas
from .fuentes import (coverflex, enable_banking, indexa, kraken, myinvestor, revolut_csv, revolutx, sabadell, trade_republic,
                      trade_republic_csv, valores_liquidativos)
from .modelo import COLUMNAS_MOV, COLUMNAS_RAW, asignar_ids, posibles_duplicados, resolver_fuentes, upsert, vacio_raw
from .sheets import Hoja, a_numero, ficheros_de_carpeta

RAIZ = Path(__file__).resolve().parents[2]
CORTE_MES = "2026-10"          # hasta septiembre de 2026 manda el histórico revisado a mano
HISTORICO_DESDE = "2025-10"    # primer mes del histórico revisado a mano
INICIO_DESCARGA = date(2026, 9, 20)
SOLAPE = timedelta(days=10)     # se vuelve a pedir un margen para recoger apuntes tardíos


def _cfg() -> dict:
    return yaml.safe_load((RAIZ / "config" / "cuentas.yaml").read_text(encoding="utf-8"))


def _historico(cfg: dict) -> pd.DataFrame:
    """Histórico revisado a mano (opcional, config «historico.fichero»). Sin él, todo lo clasifica el motor."""
    hc = cfg.get("historico") or {}
    fich = RAIZ / (hc.get("fichero") or "datos/historico/no_existe.csv")
    if not hc.get("fichero", "x") or not fich.exists():
        return pd.DataFrame(columns=COLUMNAS_MOV)
    h = pd.read_csv(fich, keep_default_na=False)
    ids = {c["nombre"].replace(" efectivo", ""): c["id"] for c in cfg["cuentas"]}
    h = h.assign(tx_id="hist-" + h["id"].astype(str), cuenta=h["cuenta"].map(lambda n: ids.get(n, n)),
                 contraparte="", iban_contraparte="", moneda="EUR", tipo_origen="", fuente="historico",
                 regla="historico")
    return h[COLUMNAS_MOV]


def ejecutar(sin_bancos: bool = False) -> None:
    cfg, hoja = _cfg(), Hoja()
    hoja.crear_pestanas_manuales()
    registro, ahora = [], datetime.now(timezone.utc).isoformat(timespec="seconds")

    def anotar(fuente, estado, filas=0, detalle=""):
        registro.append({"fecha": ahora, "fuente": fuente, "estado": estado, "filas": filas, "detalle": detalle[:300]})

    raw = hoja.leer("raw")
    raw = raw if not raw.empty else vacio_raw()
    if not raw.empty:
        raw["importe"] = a_numero(raw["importe"])
        raw = raw.astype({c: str for c in COLUMNAS_RAW if c != "importe"})

    reg = hoja.leer("reglas")
    if reg.empty:  # primera ejecución: siembra las reglas acordadas
        reg = pd.read_csv(RAIZ / "config" / "reglas_comercios.csv", keep_default_na=False)
        hoja.escribir("reglas", reg)
    reg["prioridad"] = pd.to_numeric(reg["prioridad"], errors="coerce").fillna(999)

    nuevos, saldos = [], []
    tr_valores = pd.DataFrame()
    # --- bancos (Enable Banking) ---------------------------------------------------
    if not sin_bancos:
        eb = enable_banking.EnableBanking()
        sesiones = json.loads(os.environ.get("EB_ACCOUNTS", "{}"))  # {"SAB_CORR": "<account uid>", …}
        solo_saldo = {c["id"] for c in cfg["cuentas"] if c.get("movimientos_desde") and c["movimientos_desde"] != "enable_banking"
                      and not c.get("provisional_enable_banking")}
        for cuenta, uid in sesiones.items():
            try:
                df, desde = pd.DataFrame(), "—"
                if cuenta not in solo_saldo:
                    ult = raw.loc[raw["cuenta"] == cuenta, "fecha"].max() if not raw.empty else None
                    desde = max(INICIO_DESCARGA, date.fromisoformat(ult) - SOLAPE) if isinstance(ult, str) else INICIO_DESCARGA
                    df = enable_banking.normalizar(cuenta, eb.movimientos(uid, desde))
                    nuevos.append(df)
                for b in eb.saldos(uid):
                    saldos.append({"cuenta": cuenta, "tipo": b.get("balance_type"), "fecha": b.get("reference_date") or ahora[:10],
                                   "saldo": float(b["balance_amount"]["amount"])})
                anotar(cuenta, "ok", len(df), "solo saldo" if cuenta in solo_saldo else f"desde {desde}")
            except Exception as e:  # una fuente caída no para las demás
                anotar(cuenta, "error", detalle=f"{type(e).__name__}: {e}")
        # --- Coverflex (exports en la carpeta de Drive) ----------------------------
        try:
            csvs = ficheros_de_carpeta(".csv")
            mi = [myinvestor.leer(b) for _, b in csvs if myinvestor.es_export_myinvestor(b)]
            trc = [b for _, b in csvs if trade_republic_csv.es_export(b)]
            rv = [revolut_csv.leer(b, n) for n, b in csvs if revolut_csv.es_export(b)]
            if rv:
                nuevos.append(pd.concat(rv).drop_duplicates())
                anotar("REVOLUT (CSV)", "ok", sum(map(len, rv)), f"{len(rv)} ficheros")
            cf = []
            for n_, b_ in csvs:  # el resto de CSV se lee como Coverflex; un fichero raro no tumba a los demás
                if myinvestor.es_export_myinvestor(b_) or trade_republic_csv.es_export(b_) or revolut_csv.es_export(b_):
                    continue
                try:
                    cf.append(coverflex.leer(b_))
                except Exception as e:
                    anotar(f"CSV «{n_}»", "error", detalle=f"no se reconoce: {type(e).__name__}: {e}")
            if trc:
                nuevos.append(pd.concat([trade_republic_csv.movimientos(b) for b in trc]).drop_duplicates("ref"))
                tr_valores = pd.concat([trade_republic_csv.valores(b) for b in trc]).drop_duplicates("id")
                hoja.escribir("tr_valores", tr_valores)  # para las ejecuciones «--sin-bancos», que no leen Drive
                anotar("TR (CSV)", "ok", len(tr_valores), f"{len(trc)} ficheros; {len(tr_valores)} operaciones con valores")
            if cf:
                nuevos.append(pd.concat(cf).drop_duplicates("ref"))
            if mi:
                # Exports solapados: se quitan duplicados exactos antes de numerar ocurrencias.
                nuevos.append(pd.concat(mi).drop_duplicates())
            anotar("COVERFLEX", "ok", sum(map(len, cf)), f"{len(cf)} ficheros")
            anotar("MYINV (CSV)", "ok", sum(map(len, mi)), f"{len(mi)} ficheros")
        except Exception as e:
            anotar("COVERFLEX / MYINV", "error", detalle=f"{type(e).__name__}: {e}")
        try:
            fich_sab = [(n, b) for ext in (".xls", ".xlsx") for n, b in ficheros_de_carpeta(ext)]
            sab = [sabadell.leer(b, n) for n, b in fich_sab]
            # Saldo del Excel más reciente de cada cuenta (si Enable Banking no da el del Sabadell).
            ult: dict = {}
            for n, b in fich_sab:
                u = sabadell.ultimo_saldo(b, n)
                if u and (u["cuenta"] not in ult or u["fecha"] > ult[u["cuenta"]]["fecha"]):
                    ult[u["cuenta"]] = u
            saldos += [u for c_, u in ult.items() if not any(x["cuenta"] == c_ for x in saldos)]
            if sab:
                nuevos.append(pd.concat(sab).drop_duplicates())
            anotar("SABADELL (Excel)", "ok", sum(map(len, sab)), f"{len(sab)} ficheros")
        except Exception as e:
            anotar("SABADELL (Excel)", "error", detalle=f"{type(e).__name__}: {e}")
        # --- Trade Republic (extractos PDF en la misma carpeta) ---------------------
        try:
            tr = [trade_republic.leer(b) for _, b in ficheros_de_carpeta(".pdf")]
            if tr:
                nuevos.append(pd.concat(tr).drop_duplicates("ref"))
            anotar("TR (PDF)", "ok", sum(map(len, tr)), f"{len(tr)} ficheros")
        except Exception as e:
            anotar("TR (PDF)", "error", detalle=f"{type(e).__name__}: {e}")

    if nuevos:
        raw = upsert(raw, asignar_ids(pd.concat(nuevos, ignore_index=True))[COLUMNAS_RAW])

    # --- clasificación ------------------------------------------------------------
    # Una sola fuente por cuenta y día (evita duplicados entre Excel y Enable Banking, p. ej.).
    raw_limpio, descartadas = resolver_fuentes(raw)
    if len(descartadas):
        anotar("Duplicados entre fuentes", "ok", len(descartadas), "descartadas por haber una fuente prioritaria ese día")
    mov_nuevos = reglas.clasificar(raw_limpio, reg, hoja.leer("ajustes"), cfg) if not raw_limpio.empty else pd.DataFrame(columns=COLUMNAS_MOV)
    # Meses clasificados por el motor: desde `clasificar_desde` hasta el inicio del histórico revisado,
    # y desde el corte en adelante. El histórico revisado a mano (si existe) manda siempre.
    desde_cfg = cfg.get("clasificar_desde", CORTE_MES)
    hc = cfg.get("historico") or {}
    if hc.get("fichero") and (RAIZ / hc["fichero"]).exists():
        h_desde, corte = hc.get("desde", HISTORICO_DESDE), hc.get("corte", CORTE_MES)
        mov_nuevos = mov_nuevos[(mov_nuevos["mes"] >= corte) | ((mov_nuevos["mes"] >= desde_cfg) & (mov_nuevos["mes"] < h_desde))]
    else:  # sin histórico revisado: el motor clasifica todo desde «clasificar_desde»
        mov_nuevos = mov_nuevos[mov_nuevos["mes"] >= desde_cfg]
    mov = pd.concat([_historico(cfg), mov_nuevos], ignore_index=True)

    # --- patrimonio ---------------------------------------------------------------
    pos, det_indexa = [], []
    for nombre, f, var in (("Indexa", indexa.posiciones, "INDEXA_TOKEN"), ("Kraken", kraken.posiciones, "KRAKEN_API_KEY")):
        if not os.environ.get(var):
            anotar(nombre, "sin configurar", detalle=f"falta {var}")
            continue
        try:
            p = f(); pos += p; anotar(nombre, "ok", len(p))
        except Exception as e:
            anotar(nombre, "error", detalle=f"{type(e).__name__}: {e}")
    # Valores liquidativos: se añade el último publicado de cada fondo (uno por isin y fecha).
    vls = hoja.leer("valores_liquidativos")
    nuevos_vl = []
    if tr_valores.empty:  # sin lectura de Drive en esta ejecución: la última cartera guardada
        tr_valores = hoja.leer("tr_valores")
    isins_tr = sorted(set(tr_valores["isin"])) if not tr_valores.empty else []
    for isin in isins_tr:
        try:
            v = valores_liquidativos.ultimo_vl(isin)
            if v:
                nuevos_vl.append(v)
        except Exception:
            pass  # acciones y algunos ETF no tienen ficha de fondo: se usa el último precio conocido
    for f in cfg["inversion"]["myinvestor_fondos"]:
        try:
            v = valores_liquidativos.ultimo_vl(f["isin"])
            if v:
                nuevos_vl.append(v)
            else:
                anotar(f"VL {f['isin']}", "error", detalle="no encontrado en ft.com")
        except Exception as e:
            anotar(f"VL {f['isin']}", "error", detalle=f"{type(e).__name__}: {e}")
    if nuevos_vl:
        n = pd.DataFrame(nuevos_vl)
        vls = pd.concat([vls, n.astype({"vl": object})], ignore_index=True) if not vls.empty else n
        vls["clave"] = vls["isin"].astype(str) + "|" + pd.to_datetime(vls["fecha"], format="mixed", dayfirst=True, errors="coerce").dt.strftime("%Y-%m-%d").fillna(vls["fecha"].astype(str))
        vls = vls.drop_duplicates("clave", keep="last").drop(columns="clave")
        hoja.escribir("valores_liquidativos", vls)
        anotar("Valores liquidativos", "ok", len(nuevos_vl))
    try:
        mi = patrimonio.myinvestor(mov, cfg["inversion"], hoja.leer("fondos_inicial"),
                                   hoja.leer("conciliaciones"), vls)
        pos += mi.to_dict("records"); anotar("MyInvestor", "ok", len(mi))
    except Exception as e:
        anotar("MyInvestor", "error", detalle=f"{type(e).__name__}: {e}")
    # Detalle por activo para la conciliación con Portfolio Performance.
    oficiales = [dict(p_, plataforma="Kraken") for p_ in pos if p_.get("plataforma") == "Kraken"]
    if os.environ.get("INDEXA_TOKEN"):
        try:
            det = indexa.posiciones_detalle()
            det_indexa = det
            oficiales += det
            coste_cta = pd.DataFrame(det).groupby("cuenta")["coste"].sum().to_dict() if det else {}
            for p_ in pos:
                if p_.get("plataforma") == "Indexa Capital" and p_.get("producto") in coste_cta:
                    p_["coste_fiscal"] = round(coste_cta[p_["producto"]], 2) if p_["producto"] != "Plan de pensiones" else None
        except Exception as e:
            anotar("Indexa (detalle)", "error", detalle=f"{type(e).__name__}: {e}")
    ops_cripto = hoja.leer("cripto_operaciones")
    nuevas_ops: list[pd.DataFrame] = []
    # Kraken: ledger (operaciones y staking); necesita el permiso «Query Ledger Entries».
    if os.environ.get("KRAKEN_API_KEY"):
        try:
            prev = ops_cripto[ops_cripto["plataforma"] == "Kraken"] if not ops_cripto.empty else ops_cripto
            desde = (pd.to_datetime(prev["fecha"]).max() - pd.Timedelta(days=2)).timestamp() if not prev.empty else 0
            k = patrimonio.kraken_operaciones(kraken.ledger(desde))
            if not k.empty:
                nuevas_ops.append(k)
            anotar("Kraken (ledger)", "ok", len(k))
        except Exception as e:
            sin_permiso = "Permission denied" in str(e)
            anotar("Kraken (ledger)", "sin configurar" if sin_permiso else "error",
                   detalle="añade el permiso «Query Ledger Entries» a la clave" if sin_permiso else f"{type(e).__name__}: {e}")
    # Revolut X: saldos valorados con precios públicos y operaciones propias.
    if os.environ.get("REVX_API_KEY") and os.environ.get("REVX_PRIVATE_KEY"):
        try:
            rx = kraken.valorar_eur(revolutx.saldos())
            for f in rx:
                pos.append({"plataforma": "Revolut X", "producto": f["activo"], "referencia": f["activo"], "activo": f["activo"],
                            "cantidad": f["cantidad"], "valor": f["valor"], "aportado": None, "metodo": f"api: {f['cantidad']:.8g} {f['activo']}"})
                oficiales.append({"plataforma": "Revolut X", "activo": f["activo"], "cantidad": f["cantidad"], "valor": f["valor"]})
            anotar("Revolut X", "ok", len(rx))
        except Exception as e:
            anotar("Revolut X", "error", detalle=f"{type(e).__name__}: {e}")
        try:  # operaciones aparte: si fallan, los saldos ya están anotados
            prev = ops_cripto[ops_cripto["plataforma"] == "Revolut X"] if not ops_cripto.empty else ops_cripto
            desde = pd.to_datetime(prev["fecha"]).max() - pd.Timedelta(days=2) if not prev.empty else pd.Timestamp(cfg["inversion"].get("revolutx_desde", "2024-01-01"))
            n = [revolutx.operaciones(sim, desde.to_pydatetime()) for sim in cfg["inversion"].get("revolutx_pares", ["BTC-EUR"])]
            n = [x for x in n if not x.empty]
            nuevas_ops += n
            anotar("Revolut X (operaciones)", "ok", sum(map(len, n)), f"desde {desde:%Y-%m-%d}")
        except Exception as e:
            anotar("Revolut X (operaciones)", "error", detalle=f"{type(e).__name__}: {e}")
    else:
        anotar("Revolut X", "sin configurar", detalle="faltan REVX_API_KEY / REVX_PRIVATE_KEY")
    if nuevas_ops:
        todo = pd.concat([ops_cripto] + [x.astype(str) for x in nuevas_ops], ignore_index=True) if not ops_cripto.empty \
            else pd.concat(nuevas_ops, ignore_index=True).astype(str)
        ops_cripto = todo.drop_duplicates("id", keep="last").sort_values("fecha")
        hoja.escribir("cripto_operaciones", ops_cripto)
    # Trade Republic: cartera de valores. Con el CSV de transacciones (preferido), FIFO exacto;
    # si no, deducida de los conceptos de los extractos PDF.
    cartera_tr = patrimonio.trade_republic_cartera(tr_valores, vls) if not tr_valores.empty else patrimonio.trade_republic(mov)
    for f in cartera_tr:
        pos.append(f)
        oficiales.append({"plataforma": "Trade Republic", "activo": f["activo"], "cantidad": f["cantidad"], "valor": f["valor"]})
    man = hoja.leer("inversiones_manual")
    for _, m in man.iterrows():
        pos.append({"plataforma": m["plataforma"], "producto": m["producto"], "referencia": "",
                    "valor": float(a_numero(pd.Series([m["valor"]])).iloc[0]), "aportado": None, "metodo": f"manual {m['fecha']}"})
    # Cuentas sin saldo en esta ejecución (p. ej. «--sin-bancos»): se usa el último guardado.
    previos = hoja.leer("saldos")
    if not previos.empty:
        con_saldo = {x["cuenta"] for x in saldos}
        for _, r in previos.iterrows():
            if r["cuenta"] not in con_saldo:
                saldos.append({"cuenta": r["cuenta"], "tipo": r.get("tipo") or None, "fecha": r.get("fecha", ""),
                               "saldo": float(a_numero(pd.Series([r["saldo"]])).iloc[0])})
    # Un saldo por cuenta: el contable si lo hay; si no, el siguiente tipo disponible.
    pref = {"CLBD": 0, "ITBD": 1, "CLAV": 2, "ITAV": 3, "XPCD": 4, "OTHR": 5}
    saldos = sorted(saldos, key=lambda x: pref.get(x["tipo"], 9))
    ya = set()
    for s in saldos:
        if s["cuenta"] not in ya:
            ya.add(s["cuenta"])
            pct = next(c.get("pct_titular", 1.0) for c in cfg["cuentas"] if c["id"] == s["cuenta"])
            pos.append({"plataforma": "Cuentas", "producto": s["cuenta"], "referencia": s["tipo"],
                        "valor": round(s["saldo"] * pct, 2), "aportado": None, "metodo": "api (tu parte)"})
    oficiales += [{"plataforma": "MyInvestor", "activo": r["activo"], "cantidad": r["cantidad"], "valor": r["valor"]}
                  for r in pos if r.get("plataforma") == "MyInvestor" and r.get("activo")]
    of_df = pd.DataFrame(oficiales)
    lotes_cripto = pd.DataFrame()
    if not of_df.empty:
        hoja.escribir("posiciones_oficiales", of_df)
        pp_ops = hoja.leer("pp_operaciones")
        pp_hasta = {}
        if not pp_ops.empty:  # última fecha de PP por plataforma (cuenta de PP → plataforma oficial)
            plat = pp_ops["cuenta_pp"].map(patrimonio.PLATAFORMA_PP).fillna(pp_ops["cuenta_pp"])
            pp_hasta = pp_ops.assign(plat=plat).groupby("plat")["fecha"].max().to_dict()
        conc = patrimonio.conciliar(hoja.leer("pp_posiciones"), of_df, alias=cfg["inversion"].get("alias_activos") or {},
                                    ops_oficiales=ops_cripto, pp_hasta=pp_hasta,
                                    pp_desactualizado=cfg["inversion"].get("pp_desactualizado", []),
                                    explicadas=cfg["inversion"].get("diferencias_explicadas") or {})
        if not conc.empty:
            hoja.escribir("conciliacion", conc)
            anotar("Conciliación PP", "ok", int((~conc["estado"].isin(["cuadra"]) & ~conc["estado"].str.startswith(("explicada", "PP desactualizado"))).sum()),
                   "filas sin explicar")
        # Fiscalidad de cripto: FIFO con PP + operaciones oficiales; unidades de más hoy → coste 0.
        try:
            cripto_hoy = of_df[of_df["plataforma"].isin(["Kraken", "Revolut X"])].groupby("activo")["cantidad"].sum().to_dict()
            ventas, lotes = fiscal.fifo_cripto(pp_ops, ops_cripto, pp_hasta, posiciones_hoy=cripto_hoy)
            lotes_cripto = lotes
            hoja.escribir("fiscal_ventas", ventas)
            hoja.escribir("fiscal_lotes", lotes)
            anotar("Fiscal (FIFO cripto)", "ok", len(ventas), f"{len(lotes)} lotes abiertos")
        except Exception as e:
            anotar("Fiscal (FIFO cripto)", "error", detalle=f"{type(e).__name__}: {e}")
    # Detalle por activo y resumen por categoría (vistas del dashboard).
    try:
        det_df = patrimonio.detalle(pos, det_indexa, hoja.leer("posiciones_manual"), valores_liquidativos.ultimo_vl)
        costes_m = hoja.leer("costes_fiscales")
        det_df, base_cf = patrimonio.costes_por_activo(det_df, costes_m, hoja.leer("costes_fiscales_base"))
        if not base_cf.empty:
            hoja.escribir("costes_fiscales_base", base_cf)
        det_df = patrimonio.coste_cripto(det_df, lotes_cripto)
        aportado_hoy: dict = {}
        for p_ in pos:
            if p_.get("aportado") is not None:
                k_ = patrimonio.categoria(p_["plataforma"], p_.get("producto"))
                aportado_hoy[k_] = aportado_hoy.get(k_, 0.0) + float(p_["aportado"])
        hist_cat = hoja.leer("patrimonio_categorias_historico")
        cat_df = patrimonio.por_categoria(det_df, costes_m, hist_cat, aportado_hoy)
        cat_df["aportado"] = cat_df["categoria"].map(aportado_hoy)
        hoja.escribir("patrimonio_detalle", det_df)
        hoja.escribir("patrimonio_categorias", cat_df)
        hc = hist_cat[hist_cat["fecha"] != ahora[:10]] if not hist_cat.empty else hist_cat
        hoja.escribir("patrimonio_categorias_historico", pd.concat([hc, cat_df.assign(fecha=ahora[:10]).astype(str)], ignore_index=True))
        anotar("Patrimonio por categoría", "ok", len(cat_df))
    except Exception as e:
        anotar("Patrimonio por categoría", "error", detalle=f"{type(e).__name__}: {e}")
    pat = pd.DataFrame(pos).drop_duplicates(["plataforma", "producto"], keep="first") if pos else pd.DataFrame()
    if not pat.empty:
        pat.insert(0, "fecha", ahora[:10])

    # --- escritura (cada pestaña se sobrescribe entera) ----------------------------
    hoja.escribir("raw", raw)
    hoja.escribir("movimientos", mov)
    hoja.escribir("pendientes", reglas.pendientes(mov_nuevos))
    hoja.escribir("posibles_duplicados", posibles_duplicados(raw_limpio[raw_limpio["fecha"] >= f"{CORTE_MES}-01"]))
    if saldos:
        hoja.escribir("saldos", pd.DataFrame(saldos))
    if not pat.empty:
        hoja.escribir("patrimonio", pat)
        # Histórico de patrimonio: una foto por día (re-ejecutar el mismo día la sustituye).
        hist = hoja.leer("patrimonio_historico")
        hist = hist[hist["fecha"] != ahora[:10]] if not hist.empty else hist
        hoja.escribir("patrimonio_historico", pd.concat([hist, pat.astype(str)], ignore_index=True))
    prev = hoja.leer("registro")
    hoja.escribir("registro", pd.concat([prev, pd.DataFrame(registro)], ignore_index=True).tail(500))
    errores = [r for r in registro if r["estado"] == "error"]
    print(f"Hecho: {len(mov)} movimientos, {len(errores)} fuentes con error.")
    for r in errores:
        print(" -", r["fuente"], r["detalle"])


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()  # en local lee el .env; en GitHub Actions no hay .env y usa los secretos
    ap = argparse.ArgumentParser()
    ap.add_argument("--sin-bancos", action="store_true", help="no descargar; solo reclasificar lo que ya hay")
    try:
        ejecutar(ap.parse_args().sin_bancos)
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
