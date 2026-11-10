"""Reconstruye el valor del patrimonio a fin de cada mes desde el primer dato y lo sube a la hoja
(pestaña `patrimonio_historico_reconstruido`). Se ejecuta una vez, en local:

    python -X utf8 scripts/reconstruir_historico.py C:\\ruta\\historico_patrimonio
    python -X utf8 scripts/reconstruir_historico.py C:\\ruta\\historico_patrimonio --sin-subir

En la carpeta (fuera del repo):
  - precios históricos de PP: un CSV «Fecha;Cotización» por valor, con el ISIN en el nombre
    (un «null.csv» sin ISIN se identifica comparando con tus precios de compra);
  - CSV de Revolut: el de la conjunta con «conjunta» en el nombre (cuenta al 50 %);
  - Excel del Sabadell (las dos cuentas);
  - CSV de transacciones de Trade Republic.
Lee de la hoja: pp_operaciones, patrimonio_detalle (cantidades de hoy), saldos y valores_liquidativos.
Sube: patrimonio_historico_reconstruido (por categoría) y, para comparar rentabilidades, precios_historicos
(precio de cada activo a fin de mes) y patrimonio_historico_activos (lo que valía cada activo).
"""
import argparse
import os
import sys
from pathlib import Path

import pandas as pd
import yaml
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()
from finanzas import historico as H  # noqa: E402
from finanzas.fuentes import trade_republic_csv as TRC  # noqa: E402
from finanzas.sheets import Hoja, a_numero  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("carpeta")
ap.add_argument("--sin-subir", action="store_true")
a = ap.parse_args()
carpeta = Path(a.carpeta)
cfg = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "cuentas.yaml").read_text(encoding="utf-8"))
alias = cfg["inversion"].get("alias_activos") or {}
hoja = Hoja()

# --- operaciones de PP (sin Trade Republic: su CSV es exacto)
ops = hoja.leer("pp_operaciones")
for c in ("cantidad", "importe"):
    ops[c] = a_numero(ops[c])
ops = ops[ops["cuenta_pp"] != "Trade_Republic"]
pp_hasta = pd.to_datetime(ops["fecha"]).max()

# --- ficheros locales
csvs = {f: f.read_bytes() for f in carpeta.glob("*.csv")}
tr = [b for b in csvs.values() if TRC.es_export(b)]
revolut = {f: b for f, b in csvs.items() if b[:40].decode("utf-8-sig", "ignore").startswith("Tipo,Producto")}
sabadell = [f.read_bytes() for f in list(carpeta.glob("*.xls")) + list(carpeta.glob("*.xlsx"))]
precios = H.leer_precios_pp(carpeta)
fuente_precio = {k: "Portfolio Performance" for k in precios}

inicio = min([pd.to_datetime(ops["fecha"]).min()] + [pd.Timestamp("2100-01-01")])
meses = pd.date_range(inicio.to_period("M").to_timestamp("M"), pd.Timestamp.today().to_period("M").to_timestamp("M"), freq="ME")
print(f"Meses: {meses[0]:%Y-%m} → {meses[-1]:%Y-%m} ({len(meses)}). PP hasta {pp_hasta:%Y-%m-%d}.")

# Ficheros de precios sin ISIN
sin_nombre = [k for k in precios if not H.ES_FONDO.match(k)]
for k, (activo, err) in H.identificar_sin_nombre(precios, ops, sin_nombre).items():
    print(f"«{k}.csv» parece ser {activo} (diferencia mediana con tus precios de compra: {err:.1%})" + ("" if err < 0.02 else " — revísalo"))
    if err < 0.05:
        precios[activo] = precios.pop(k)
# Los códigos de PP traducidos a los oficiales (config «alias_activos») también para los precios.
for pp_cod, oficial in alias.items():
    if pp_cod in precios and oficial not in precios:
        precios[oficial] = precios[pp_cod]

# --- participaciones mes a mes
part = H.participaciones_mensuales(ops, meses, alias)
det = hoja.leer("patrimonio_detalle")
if not det.empty:
    det["cantidad"] = a_numero(det["cantidad"])
    of = det[det["categoria"].isin(["Fondos Indexa", "Plan de pensiones Indexa", "Fondos MyInvestor", "Cripto"]) & (det["activo"] != "EUR")]
    part = H.extender_hasta_hoy(part, of[["categoria", "activo", "cantidad"]], pp_hasta, meses)
vls_hoja = hoja.leer("valores_liquidativos")
if tr:
    v = pd.concat([TRC.valores(b) for b in tr]).drop_duplicates("id")
    v["fecha"] = pd.to_datetime(v["fecha"])
    for isin, g in v.groupby("isin"):
        acum = g.set_index("fecha")["cantidad"].fillna(0).sort_index().cumsum()
        for mes in meses:
            q = acum[acum.index <= mes]
            part.loc[len(part)] = [mes, "ETF y acciones Trade Republic", isin, float(q.iloc[-1]) if len(q) else 0.0]
        if isin not in precios:  # sin fichero de PP: precios de sus compras + el de hoy, interpolados
            p = g.dropna(subset=["precio"]).set_index("fecha")["precio"]
            p = p[~p.index.duplicated(keep="last")]
            hoy_p = None
            if not det.empty and (det["activo"] == isin).any():
                d_ = det[det["activo"] == isin]
                v_, q_ = a_numero(d_["valor"]).sum(), d_["cantidad"].sum()
                hoy_p = v_ / q_ if q_ else None
            if hoy_p is None and not vls_hoja.empty and (vls_hoja["isin"] == isin).any():  # precio apuntado en la hoja
                u_ = vls_hoja[vls_hoja["isin"] == isin].assign(f=lambda d: pd.to_datetime(d["fecha"], format="mixed", dayfirst=True)).sort_values("f").iloc[-1]
                hoy_p = float(a_numero(pd.Series([u_["vl"]])).iloc[0])
            precios[isin] = H.completar_con_precio_actual(p, hoy_p, pd.Timestamp.today())
            fuente_precio[isin] = "compras interpoladas (aproximado)"
            print(f"Precios de {isin}: tus compras" + (" + precio de hoy, interpolados" if hoy_p else " (sin precio de hoy)"))

# --- precios de cripto (Binance, público)
for activo in sorted(set(part.loc[part["categoria"] == "Cripto", "activo"]) - {"EUR"} - set(precios)):
    s = H.precios_binance(activo, meses[0] - pd.Timedelta(days=40))
    if len(s):
        precios[activo] = s
        fuente_precio[activo] = "Binance"
        print(f"Precios de {activo}: Binance ({s.index.min():%Y-%m} → {s.index.max():%Y-%m})")

# --- valores liquidativos guardados en la hoja (fondos): alargan la serie de PP más allá de su última fecha
if not vls_hoja.empty:
    v_ = vls_hoja.assign(f=pd.to_datetime(vls_hoja["fecha"], format="mixed", dayfirst=True, errors="coerce"),
                         p=a_numero(vls_hoja["vl"])).dropna(subset=["f", "p"])
    for isin, g in v_.groupby("isin"):
        s_ = g.set_index("f")["p"].sort_index()
        s_ = s_[~s_.index.duplicated(keep="last")]
        if isin in precios and len(precios[isin]):
            s_ = pd.concat([precios[isin], s_[s_.index > precios[isin].index.max()]])
        else:
            fuente_precio[isin] = "valores liquidativos"
        precios[isin] = s_

# --- valor de cada activo a fin de mes
part = part[part["cantidad"].abs() > 1e-9]
sin_precio = set()
def precio(activo, mes):
    if activo == "EUR":
        return 1.0
    s = precios.get(activo)
    if s is None or s.empty:
        sin_precio.add(activo)
        return float("nan")
    s = s[s.index <= mes + pd.Timedelta(hours=23)]
    return float(s.iloc[-1]) if len(s) else float(precios[activo].iloc[0])
part["valor"] = [q * precio(a_, m) for q, a_, m in zip(part["cantidad"], part["activo"], part["mes"])]
if sin_precio:
    print("⚠️  Sin precios (cuentan como 0):", ", ".join(sorted(sin_precio)))

# --- efectivo
efectivo = pd.Series(0.0, index=meses)
for f, b in revolut.items():
    pct = 0.5 if "conjunta" in f.name.lower() else 1.0
    efectivo += H.a_fin_de_mes(H.saldos_revolut(b, pct), meses)
    print(f"Saldos de {f.name} ({'50 %' if pct == 0.5 else '100 %'})")
for f in list(carpeta.glob("*.xls")) + list(carpeta.glob("*.xlsx")):
    efectivo += H.a_fin_de_mes(H.saldos_sabadell(f.read_bytes()), meses)
    print(f"Saldos de {f.name}")
if tr:
    sal = hoja.leer("saldos")
    hoy_tr = float(a_numero(sal.loc[sal["cuenta"] == "TR", "saldo"]).iloc[0]) if not sal.empty and (sal["cuenta"] == "TR").any() else None
    if hoy_tr is not None:
        trb = pd.concat([TRC._leer(b) for b in tr]).drop_duplicates("transaction_id")
        # El saldo de hoy puede incluir movimientos posteriores al último día del CSV: se descuentan
        # los provisionales de Enable Banking de esos días para enlazar bien hacia atrás.
        fin_csv = trb["date"].max()
        raw = hoja.leer("raw")
        if not raw.empty:
            post = raw[(raw["cuenta"] == "TR") & (raw["fuente"] == "enable_banking") & (raw["fecha"] > fin_csv)]
            ajuste = float(a_numero(post["importe"]).sum()) if len(post) else 0.0
            if ajuste:
                print(f"Trade Republic: {len(post)} movimientos posteriores al CSV ({fin_csv}) suman {ajuste:,.2f} €: se descuentan del saldo de hoy.")
                hoy_tr -= ajuste
        print(f"Trade Republic: saldo de partida {hoy_tr:,.2f} € a {fin_csv} (compáralo con la app; el histórico de su efectivo se calcula hacia atrás desde aquí).")
        buf = trb.to_csv(index=False).encode()
        efectivo += H.a_fin_de_mes(H.saldos_trade_republic(buf, hoy_tr), meses)
    else:
        print("⚠️  No encuentro el saldo de hoy de Trade Republic en la pestaña «saldos»: su efectivo no se reconstruye.")

res = part.groupby(["mes", "categoria"], as_index=False)["valor"].sum()
res = pd.concat([res, pd.DataFrame({"mes": meses, "categoria": "Efectivo en cuentas", "valor": efectivo.values})], ignore_index=True)
res["mes"] = res["mes"].dt.strftime("%Y-%m-%d")
res["valor"] = res["valor"].round(2)
tabla = res.pivot_table(index="mes", columns="categoria", values="valor", aggfunc="sum").fillna(0)
print("\nFin de año:")
print(tabla[tabla.index.str[5:7] == "12"].assign(TOTAL=lambda t: t.sum(axis=1)).round(0).to_string())
print("\nÚltimo mes:")
print(tabla.tail(1).T.round(0).to_string())
# --- series para comparar rentabilidades: precio de cada activo a fin de mes y lo que valía cada uno
from finanzas import rentabilidad as RT  # noqa: E402
filas_p = []
for activo in sorted(set(part["activo"]) - {"EUR"}):
    s_ = precios.get(activo)
    if s_ is None or s_.empty:
        continue
    for mes in meses:
        if mes + pd.Timedelta(hours=23) < s_.index.min():
            continue  # antes de su primer precio no hay rentabilidad que calcular
        filas_p.append({"activo": activo, "fecha": mes.strftime("%Y-%m-%d"), "precio": round(precio(activo, mes), 6),
                        "fuente": fuente_precio.get(activo, "")})
precios_m = pd.DataFrame(filas_p)
# El mes en curso, con el precio de hoy (patrimonio_detalle): los ficheros de PP suelen acabar antes.
if not det.empty and len(precios_m):
    d_hoy = det.assign(valor=a_numero(det["valor"]))
    d_hoy = d_hoy[(d_hoy["cantidad"] > 0) & (d_hoy["valor"] > 0) & (d_hoy["activo"] != "EUR")].groupby("activo")[["valor", "cantidad"]].sum()
    p_hoy = (d_hoy["valor"] / d_hoy["cantidad"]).round(6)
    ult = precios_m["fecha"] == meses[-1].strftime("%Y-%m-%d")
    precios_m.loc[ult & precios_m["activo"].isin(p_hoy.index), "precio"] = precios_m.loc[ult, "activo"].map(p_hoy)
valores_m = part[(part["activo"] != "EUR") & part["valor"].notna()].assign(mes=lambda d: d["mes"].dt.strftime("%Y-%m-%d"))
valores_m = valores_m[["mes", "categoria", "activo", "cantidad", "valor"]].round({"valor": 2})
print(f"\nSeries de rentabilidad: {precios_m['activo'].nunique() if len(precios_m) else 0} activos con precio, {len(valores_m)} filas de posiciones.")
aprox = sorted(k for k, v in fuente_precio.items() if v.startswith("compras") and k in set(precios_m.get("activo", [])))
if aprox:
    print("   Con precio aproximado (sus compras interpoladas; la rentabilidad mes a mes no es real):", ", ".join(aprox))

if not a.sin_subir:
    hoja.escribir("patrimonio_historico_reconstruido", res)
    hoja.escribir("precios_historicos", RT.actualizar_mes(hoja.leer("precios_historicos"), precios_m, "fecha", ["activo"]))
    hoja.escribir("patrimonio_historico_activos", RT.sustituir_mes(hoja.leer("patrimonio_historico_activos"), valores_m, "mes"))
    print("\nSubido a patrimonio_historico_reconstruido, precios_historicos y patrimonio_historico_activos.")
