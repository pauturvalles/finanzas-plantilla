"""Comprueba las reglas clave de DECISIONES.md y que todo es idempotente."""
from pathlib import Path

import pandas as pd
import yaml

from finanzas.modelo import COLUMNAS_RAW, asignar_ids, resolver_fuentes, upsert
from finanzas.reglas import SIN_CLASIFICAR, clasificar

RAIZ = Path(__file__).resolve().parents[1]
CFG = yaml.safe_load((RAIZ / "config" / "cuentas.yaml").read_text(encoding="utf-8"))
REGLAS = pd.read_csv(RAIZ / "config" / "reglas_comercios.csv", keep_default_na=False)
REGLAS["prioridad"] = pd.to_numeric(REGLAS["prioridad"])
# Personas de ejemplo para los tests (en tu config pondrás las tuyas).
CFG["compensan_compras"] = ["SAM P"]
CFG["familia"] = ["FAMILIAR A B"]


def _raw(filas):
    df = pd.DataFrame([dict(zip(["cuenta", "fecha", "concepto", "importe", "tipo_origen", "contraparte"], f)) for f in filas])
    df = df.assign(iban_contraparte="", moneda="EUR", fuente="test")
    df["tipo_origen"] = df["tipo_origen"].fillna("")
    df["contraparte"] = df["contraparte"].fillna("")
    return asignar_ids(df)[COLUMNAS_RAW]


RAW = _raw([
    ("REV_CONJ", "2026-10-02", "ALQUILER PISO OCTUBRE", -650.0, "", ""),                 # alquiler 50 %
    ("COVERFLEX", "2026-10-05", "GLOVO 05OCT", -28.40, "Comida", ""),                     # comida repuesta
    ("REV_CONJ", "2026-10-05", "To Alex Garcia Lopez", -28.40, "", ""),                      # reposición
    ("TR", "2026-10-07", "WWW.AMAZON*AB12CD34E", -57.60, "", ""),                         # pedido para Sam
    ("BANCO_CORR", "2026-10-08", "ABONO BIZUM DE SAM P R", 57.60, "", ""),                # compensación
    ("COVERFLEX", "2026-10-10", "IL POMODORO", -95.10, "Comida", ""),                     # comida de grupo
    ("BANCO_CORR", "2026-10-11", "ABONO BIZUM DE AMIGO X Y", 19.02, "", ""),
    ("BANCO_CORR", "2026-10-11", "ABONO BIZUM DE FAMILIAR A B", 50.00, "", ""),                # familia: no compensa
    ("TR", "2026-10-12", "COMERCIO DESCONOCIDO SL", -12.00, "", ""),                      # pendiente
    ("TR", "2026-10-12", "COMERCIO DESCONOCIDO SL", -12.00, "", ""),                      # cargo idéntico: no se fusiona
    ("REV_CONJ", "2026-10-27", "Bon preu To SAM PEREZ RUIZ", -36.89, "", "Sam Perez"),  # reposición a Sam con nota
    ("REV_CONJ", "2026-10-01", "Pago de PEREZ RUIZ SAM", 913.0, "", ""),                   # aportación de Sam
])


def test_ids_distintos_para_cargos_identicos():
    assert RAW["tx_id"].is_unique


def test_reglas_principales():
    m = clasificar(RAW, REGLAS, pd.DataFrame(), CFG).set_index("concepto")
    assert m.loc["ALQUILER PISO OCTUBRE", "importe_imputado"] == -325.0
    assert m.loc["GLOVO 05OCT", "pct_imputado"] == 0.0                     # lo pagó el titular, lo repone la conjunta
    rep = m.loc["To Alex Garcia Lopez"]
    assert rep["categoria"] == "Restauración y delivery" and rep["importe_imputado"] == -14.2
    assert m.loc["WWW.AMAZON*AB12CD34E", "pct_imputado"] == 0.0
    assert m.loc["ABONO BIZUM DE SAM P R", "flujo"] == "Compensación de compra"
    alba = m.loc["ABONO BIZUM DE AMIGO X Y"]
    assert alba["flujo"] == "Gasto" and alba["categoria"] == "Restauración y delivery"
    assert m.loc["ABONO BIZUM DE FAMILIAR A B", "flujo"] == "Bizum personas"
    assert (m.loc["COMERCIO DESCONOCIDO SL", "categoria"] == SIN_CLASIFICAR).all()
    assert m.loc["Pago de PEREZ RUIZ SAM", "categoria"] == "Aportación Sam a conjunta"
    bon = m.loc["Bon preu To SAM PEREZ RUIZ"]
    assert bon["categoria"] == "Supermercado y alimentación" and abs(bon["importe_imputado"] + 18.445) < 0.006


def test_ajuste_manual_manda():
    tx = RAW.loc[RAW["concepto"] == "COMERCIO DESCONOCIDO SL", "tx_id"].iloc[0]
    aj = pd.DataFrame([{"tx_id": tx, "categoria": "Golf", "subcategoria": "Material y ropa", "flujo": "",
                        "tipo_gasto": "", "pct_imputado": "", "nota": "bolas"}])
    m = clasificar(RAW, REGLAS, aj, CFG)
    assert m.loc[m["tx_id"] == tx, "categoria"].item() == "Golf"


def test_idempotencia():
    a = clasificar(RAW, REGLAS, pd.DataFrame(), CFG)
    b = clasificar(upsert(upsert(RAW, RAW), RAW), REGLAS, pd.DataFrame(), CFG)
    pd.testing.assert_frame_equal(a, b)


def test_reexportar_el_mismo_fichero_no_duplica():
    otra_vez = upsert(RAW, RAW.copy())
    assert len(otra_vez) == len(RAW)


def test_una_fuente_por_cuenta_y_dia():
    excel = _raw([("BANCO_CORR", "2026-11-03", "COMPRA TARJ. GLOVO", -20.0, "", "")]).assign(fuente="sabadell_xls")
    eb = _raw([("BANCO_CORR", "2026-11-03", "GLOVO 03NOV", -20.0, "", "")]).assign(fuente="enable_banking")
    solo_excel = _raw([("BANCO_CORR", "2026-11-04", "MERCADONA", -30.0, "", "")]).assign(fuente="sabadell_xls")
    limpio, fuera = resolver_fuentes(pd.concat([excel, eb, solo_excel], ignore_index=True))
    # Desde el primer día con API, el Excel de esa cuenta ya no cuenta (ni siquiera en días sin datos de la API).
    assert list(limpio["fuente"]) == ["enable_banking"] and len(fuera) == 2


def test_conciliacion_pp():
    from finanzas.patrimonio import conciliar
    pp_pos = pd.DataFrame({"cuenta_pp": ["Indexa", "Binance", "Coinbase_Pro", "MyInvestor", "Binance"],
                           "activo": ["IE00BFPM9V94", "BTC", "BTC", "IE00BD0NCM55", "ZIL"],
                           "cantidad": [41.78, 0.10, 0.05, 1554.52, 999]})
    of = pd.DataFrame({"plataforma": ["Indexa Capital", "Kraken", "Revolut X", "MyInvestor", "Kraken"],
                       "activo": ["IE00BFPM9V94", "BTC", "BTC", "IE00BD0NCM55", "ADA"],
                       "cantidad": [41.78, 0.08, 0.07, 1560.0, 300], "valor": [1, 1, 1, 1, 1]})
    c = conciliar(pp_pos, of).set_index("activo")
    assert c.loc["IE00BFPM9V94", "estado"] == "cuadra"
    assert c.loc["BTC", "estado"] == "cuadra"            # 0,15 en PP (dos exchanges) = 0,15 oficiales
    assert c.loc["IE00BD0NCM55", "estado"] == "no cuadra"
    assert c.loc["ZIL", "estado"] == "solo en PP" and c.loc["ADA", "estado"] == "solo oficial"


def test_trade_republic_valores():
    from finanzas.patrimonio import trade_republic
    mov = pd.DataFrame({"cuenta": ["TR", "TR", "TR"], "fecha": ["2026-06-02", "2026-07-02", "2025-12-09"],
                        "concepto": ["Savings plan execution IE00B4ND3602 iShares Physical Gold ETC, quantity: 0.662778",
                                     "Savings plan execution IE00B4ND3602 ISHARES PHYSICAL GOLD, quantity: 0.721917",
                                     "Buy trade DE0007100000 MERCEDES-BENZ GRP NA O.N., quantity: Operar 0.162919"],
                        "importe": [-50.0, -50.0, -10.99]})
    f = {r["activo"]: r for r in trade_republic(mov)}
    assert round(f["IE00B4ND3602"]["cantidad"], 6) == 1.384695 and f["IE00B4ND3602"]["aportado"] == 100
    assert abs(f["IE00B4ND3602"]["valor"] - 1.384695 * 50 / 0.721917) < 0.01
    assert "DE0007100000" in f


def test_kraken_ledger_y_staking():
    from finanzas.patrimonio import kraken_operaciones, conciliar
    led = [{"id": "L1", "refid": "R1", "time": 1735689600, "type": "trade", "subtype": "", "asset": "ZEUR", "amount": "-100", "fee": "0.26"},
           {"id": "L2", "refid": "R1", "time": 1735689600, "type": "trade", "subtype": "", "asset": "XXBT", "amount": "0.001", "fee": "0"},
           {"id": "L3", "refid": "R2", "time": 1735776000, "type": "staking", "subtype": "", "asset": "ADA.S", "amount": "5", "fee": "0"},
           {"id": "L4", "refid": "R3", "time": 1735776000, "type": "transfer", "subtype": "spottostaking", "asset": "ADA", "amount": "-10", "fee": "0"}]
    o = kraken_operaciones(led)
    assert list(o["tipo"]) == ["compra", "ingreso_en_especie"] and o.loc[0, "importe"] == 100.26 and o.loc[0, "activo"] == "BTC"
    pp_pos = pd.DataFrame({"cuenta_pp": ["Binance"], "activo": ["ADA"], "cantidad": [10.0]})
    of = pd.DataFrame({"plataforma": ["Kraken", "Kraken"], "activo": ["ADA", "BTC"], "cantidad": [15.0, 0.001], "valor": [1, 1]})
    c = conciliar(pp_pos, of, ops_oficiales=o.astype(str), pp_hasta={"Kraken": "2024-12-31"}).set_index("activo")
    assert c.loc["ADA", "estado"] == "cuadra" and c.loc["BTC", "estado"] == "cuadra"


def test_variantes_staking_kraken():
    from finanzas.patrimonio import activo_kraken
    assert activo_kraken("DOT28.S") == "DOT" and activo_kraken("KSM07.S") == "KSM" and activo_kraken("C98") == "C98"
    assert activo_kraken("XXBT") == "BTC" and activo_kraken("ADA.S") == "ADA"


def test_pp_desactualizado_y_plataforma_sin_pp():
    from finanzas.patrimonio import conciliar
    pp_pos = pd.DataFrame({"cuenta_pp": ["Indexa"], "activo": ["IE00BFPM9V94"], "cantidad": [41.78]})
    of = pd.DataFrame({"plataforma": ["Indexa Capital", "Revolut X"], "activo": ["IE00BFPM9V94", "BTC"], "cantidad": [43.06, 0.02], "valor": [1, 1]})
    ops = pd.DataFrame({"fecha": ["2025-03-01"], "plataforma": ["Revolut X"], "tipo": ["compra"], "activo": ["BTC"], "cantidad": ["0.02"]})
    c = conciliar(pp_pos, of, ops_oficiales=ops, pp_hasta={"Indexa Capital": "2026-04-30"}, pp_desactualizado=["Indexa Capital"]).set_index("activo")
    assert c.loc["IE00BFPM9V94", "estado"].startswith("PP desactualizado") and c.loc["BTC", "estado"] == "cuadra"


def test_diferencias_explicadas():
    from finanzas.patrimonio import conciliar
    pp_pos = pd.DataFrame({"cuenta_pp": ["Binance"], "activo": ["ZIL"], "cantidad": [999.0]})
    of = pd.DataFrame({"plataforma": ["Kraken"], "activo": ["ADA"], "cantidad": [1.0], "valor": [1]})
    c = conciliar(pp_pos, of, explicadas={"ZIL": "restos"}).set_index("activo")
    assert c.loc["ZIL", "estado"] == "explicada: restos" and c.loc["ADA", "estado"] == "solo oficial"


def test_detalle_y_categorias():
    from finanzas.patrimonio import detalle, por_categoria
    pos = [{"plataforma": "Indexa Capital", "producto": "Fondos indexados", "valor": 59500, "valor_fondos": 59000, "efectivo": 500, "aportado": 40000},
           {"plataforma": "MyInvestor", "producto": "DW", "activo": "IE00BD0NCM55", "cantidad": 10, "valor": 300, "coste_fiscal": 200, "aportado": 250},
           {"plataforma": "Kraken", "producto": "BTC", "activo": "BTC", "cantidad": 0.01, "valor": 900}]
    idx = [{"cuenta": "Fondos indexados", "activo": "IE00BFPM9V94", "nombre": "US 500", "cantidad": 40, "valor": 59000, "coste": 21000, "fecha": "2026-10-29"},
           {"cuenta": "Plan de pensiones", "activo": "N0001", "nombre": "PP", "cantidad": 190, "valor": 5000, "coste": 4250, "fecha": "2026-10-29"}]
    man = pd.DataFrame([{"categoria": "ETF y acciones Trade Republic", "plataforma": "Trade Republic", "isin": "IE00B5BMR087", "nombre": "S&P 500",
                         "cantidad": "2", "valor": "1.000", "coste_fiscal": "800", "fecha": "30/10/2026"}])
    det = detalle(pos, idx, man, lambda isin: {"vl": 600.0, "fecha": "2026-10-30"})
    assert set(det["categoria"]) == {"Fondos Indexa", "Plan de pensiones Indexa", "Fondos MyInvestor", "Cripto", "ETF y acciones Trade Republic"}
    assert det.loc[det["activo"] == "IE00B5BMR087", "valor"].item() == 1200          # 2 × 600 (precio público)
    hist = pd.DataFrame([{"fecha": "2026-10-30", "categoria": "Fondos Indexa", "aportado": "39500"}])
    cm = pd.DataFrame([{"categoria": "Fondos Indexa", "fecha": "30/10/2026", "coste_fiscal": "18.395,96", "nota": ""}])
    cat = por_categoria(det, cm, hist, {"Fondos Indexa": 40000}).set_index("categoria")
    assert abs(cat.loc["Fondos Indexa", "coste_fiscal"] - (18395.96 + 500 + 500)) < 0.01  # web + aportado después + efectivo
    assert cat.loc["Fondos Indexa", "valor"] == 59500                                  # fondos + efectivo


def test_trade_republic_csv():
    from finanzas.fuentes import trade_republic_csv as T
    from finanzas.patrimonio import trade_republic_cartera
    cab = '"datetime","date","account_type","category","type","asset_class","name","symbol","shares","price","amount","fee","tax","currency","original_amount","original_currency","fx_rate","description","transaction_id","counterparty_name","counterparty_iban","payment_reference","mcc_code"\n'
    filas = [
        '"t","2024-01-18","DEFAULT","TRADING","BUY","FUND","Core MSCI World","IE00B4L5Y983","3.0","82.2","-246.73","-1.00","","EUR","","","","","a1","","","",""',
        '"t","2025-06-11","DEFAULT","DELIVERY","MIGRATION","FUND","Core MSCI World","IE00B4L5Y983","-3.0","82.5","","","","EUR","","","","MIGRATION","a2","","","",""',
        '"t","2025-06-11","DEFAULT","DELIVERY","MIGRATION","FUND","Core MSCI World","IE00B4L5Y983","3.0","82.5","","","","EUR","","","","MIGRATION","a3","","","",""',
        '"t","2025-08-18","DEFAULT","TRADING","BUY","STOCK","Netflix","US64110L1061","0.0094","1069.2","-10.05","","","EUR","","","","","a4","","","",""',
        '"t","2025-11-17","DEFAULT","CORPORATE_ACTION","SPLIT","STOCK","Netflix","US64110L1061","0.0846","","","","","EUR","","","","SPLIT","a5","","","",""',
        '"t","2026-10-02","DEFAULT","CASH","CARD_TRANSACTION","","DES PETITS HAUTS","","","","-14.50","","","EUR","","","","DES PETITS HAUTS","a6","","","",""',
    ]
    b = (cab + "\n".join(filas) + "\n").encode()
    assert T.es_export(b)
    mv = T.movimientos(b)
    assert list(mv["importe"]) == [-247.73, -10.05, -14.5] and mv["concepto"].iloc[2] == "DES PETITS HAUTS"
    car = {r["activo"]: r for r in trade_republic_cartera(T.valores(b), pd.DataFrame(columns=["isin", "fecha", "vl", "fuente"]))}
    assert car["IE00B4L5Y983"]["cantidad"] == 3.0 and car["IE00B4L5Y983"]["coste_fiscal"] == 247.73
    assert car["US64110L1061"]["cantidad"] == 0.094 and car["US64110L1061"]["coste_fiscal"] == 10.05


def test_costes_fiscales_por_fondo():
    from finanzas.patrimonio import costes_por_activo, por_categoria
    det = pd.DataFrame([
        {"categoria": "Fondos Indexa", "plataforma": "Indexa Capital", "activo": "IE00BFPM9V94", "nombre": "US", "cantidad": 43, "valor": 26000.0, "coste": 21000.0, "fecha_valor": "", "fuente": ""},
        {"categoria": "Fondos Indexa", "plataforma": "Indexa Capital", "activo": "IE00BFPM9L96", "nombre": "EU", "cantidad": 54, "valor": 16000.0, "coste": 12000.0, "fecha_valor": "", "fuente": ""},
        {"categoria": "Fondos Indexa", "plataforma": "Indexa Capital", "activo": "EUR", "nombre": "Efectivo", "cantidad": 300, "valor": 300.0, "coste": 300.0, "fecha_valor": "", "fuente": ""}])
    cm = pd.DataFrame([{"categoria": "Fondos Indexa", "isin": "IE00BFPM9V94", "fecha": "30/10/2026", "coste_fiscal": "", "plusvalia_latente": "8.000", "valor": "", "nota": ""},
                       {"categoria": "Fondos Indexa", "isin": "IE00BFPM9L96", "fecha": "30/10/2026", "coste_fiscal": "10000", "plusvalia_latente": "", "valor": "", "nota": ""}])
    d1, base = costes_por_activo(det, cm, pd.DataFrame())
    assert d1.loc[0, "coste"] == 18000 and d1.loc[1, "coste"] == 10000 and len(base) == 2
    # un mes después Indexa compra 500 € más del fondo US: el coste fiscal sube lo mismo
    det2 = det.copy(); det2.loc[0, "coste"] = 21500.0
    d2, _ = costes_por_activo(det2, cm, base)
    assert d2.loc[0, "coste"] == 18500
    cat = por_categoria(d2, cm, pd.DataFrame(), {}).set_index("categoria")
    assert cat.loc["Fondos Indexa", "coste_fiscal"] == 18500 + 10000 + 300


def test_trade_republic_provisional():
    from finanzas.modelo import resolver_fuentes
    eb = _raw([("TR", "2026-11-03", "", -20.0, "", ""), ("TR", "2026-11-05", "", -7.5, "", "")]).assign(fuente="enable_banking")
    csv_ = _raw([("TR", "2026-11-03", "Glovo", -20.0, "", "")]).assign(fuente="trade_republic_csv")
    limpio, fuera = resolver_fuentes(pd.concat([eb, csv_], ignore_index=True))
    assert sorted(limpio["fuente"]) == ["enable_banking", "trade_republic_csv"] and len(fuera) == 1   # el 03 manda el CSV; el 05 queda provisional
    m = clasificar(limpio, REGLAS, pd.DataFrame(), CFG).set_index("fecha")
    assert m.loc["2026-11-05", "categoria"] == "Pendiente de detalle (Trade Republic)" and m.loc["2026-11-05", "flujo"] == "Gasto"


def test_coste_cripto_en_detalle():
    from finanzas.patrimonio import coste_cripto
    det = pd.DataFrame([{"categoria": "Cripto", "plataforma": "Kraken", "activo": "BTC", "cantidad": 0.01, "valor": 900, "coste": None},
                        {"categoria": "Cripto", "plataforma": "Revolut X", "activo": "BTC", "cantidad": 0.03, "valor": 2700, "coste": None},
                        {"categoria": "Cripto", "plataforma": "Kraken", "activo": "EUR", "cantidad": 5, "valor": 5, "coste": None}])
    lotes = pd.DataFrame({"activo": ["BTC", "BTC"], "fecha_compra": ["2025-01-01", ""], "cantidad": [0.03, 0.01], "coste": [2000, 0], "origen": ["compra", "sin registrar"]})
    d = coste_cripto(det, lotes)
    assert list(d["coste"]) == [500, 1500, 5]


def test_plan_pensiones_concilia_como_fondo():
    from finanzas.patrimonio import conciliar
    pp_pos = pd.DataFrame({"cuenta_pp": ["Indexa"], "activo": ["0P0000TEST.F"], "cantidad": [192.5]})
    of = pd.DataFrame({"plataforma": ["Indexa Capital"], "activo": ["N0001"], "cantidad": [192.5], "valor": [5300]})
    c = conciliar(pp_pos, of, alias={"0P0000TEST.F": "N0001"})
    assert list(c["plataforma"]) == ["Indexa Capital"] and list(c["estado"]) == ["cuadra"]


def test_provisionales_tr_con_fecha_contable_desplazada():
    from finanzas.modelo import resolver_fuentes
    csv_ = _raw([("TR", "2026-10-28", "Glovo", -20.0, "", ""), ("TR", "2026-10-30", "Bon Area", -7.64, "", "")]).assign(fuente="trade_republic_csv")
    eb = _raw([("TR", "2026-10-29", "", -20.0, "", ""),      # mismo pago, contabilizado un día después: dentro del CSV → fuera
               ("TR", "2026-11-01", "", -7.64, "", ""),      # contabilizado tras el fin del CSV, mismo importe → fuera
               ("TR", "2026-11-02", "", -55.0, "", "")]).assign(fuente="enable_banking")  # nuevo de verdad → se queda
    limpio, fuera = resolver_fuentes(pd.concat([csv_, eb], ignore_index=True))
    eb_queda = limpio[limpio["fuente"] == "enable_banking"]
    assert list(eb_queda["importe"]) == [-55.0] and len(fuera) == 2


def test_valor_de_compra_por_categoria():
    from finanzas.patrimonio import detalle, por_categoria, costes_por_activo
    pos = [{"plataforma": "MyInvestor", "producto": "DW", "activo": "IE00BD0NCM55", "cantidad": 10, "valor": 45000, "coste_fiscal": 30000, "aportado": 37000}]
    idx = [{"cuenta": "Fondos indexados", "activo": "IE00BFPM9V94", "nombre": "US", "cantidad": 40, "valor": 59000, "coste": 21000, "fecha": ""},
           {"cuenta": "Plan de pensiones", "activo": "N0001", "nombre": "PP", "cantidad": 190, "valor": 5300, "coste": 4250, "fecha": ""}]
    det = detalle(pos, idx, pd.DataFrame(), lambda i: None)
    cm = pd.DataFrame([{"categoria": "Fondos Indexa", "isin": "IE00BFPM9V94", "fecha": "30/10/2026", "coste_fiscal": "", "plusvalia_latente": "18340", "valor": "59000", "nota": ""}])
    det, _ = costes_por_activo(det, cm, pd.DataFrame())
    c = por_categoria(det, cm, pd.DataFrame(), {}).set_index("categoria")
    assert c.loc["Fondos Indexa", "valor_compra"] == 21000 and c.loc["Fondos Indexa", "coste_fiscal"] == 40660
    assert c.loc["Fondos MyInvestor", "valor_compra"] == 37000 and c.loc["Fondos MyInvestor", "coste_fiscal"] == 30000
    assert c.loc["Plan de pensiones Indexa", "valor_compra"] == 4250


def test_coverflex_formato_2025():
    from finanzas.fuentes import coverflex
    csv_ = ("id,date,type,status,merchant,amount,currency,is_debit,category,product,voucher_count,voucher_amount,rejection_reason\n"
            "a1,2025-02-03T13:10:00Z,purchase,completed,BARCELO GASTRO,12.50,EUR,true,restaurants,Meal,,,\n"
            "a2,2025-02-04,purchase,rejected,BAR X,9.00,EUR,true,restaurants,Meal,,,insufficient\n"
            "a3,2025-02-05,top_up,completed,Coverflex,160.00,EUR,false,,Meal,,,\n"
            "a4,2025-02-06,purchase,completed,TMB,11.35,EUR,true,transport,Public transport,,,\n")
    c = coverflex.leer(csv_)
    assert list(c["importe"]) == [-12.5, -11.35] and list(c["tipo_origen"]) == ["Comida", "Transporte"] and list(c["fecha"]) == ["2025-02-03", "2025-02-06"]


def test_regla_anclada_al_concepto():
    reg = pd.concat([REGLAS, pd.DataFrame([{"prioridad": 6, "patron": "^SSP$", "cuenta": "", "categoria": "Restauración y delivery",
                                            "subcategoria": "", "tipo_gasto": "Variable", "flujo": "Gasto", "nota": ""}])], ignore_index=True)
    m = clasificar(_raw([("REV_CONJ", "2025-09-15", "SSP", -15.15, "", "SSP")]), reg, pd.DataFrame(), CFG)
    assert m["categoria"].iloc[0] == "Restauración y delivery"


def test_desglose_de_un_pago_unico():
    from finanzas.modelo import aplicar_desgloses
    raw = _raw([("REV_CONJ", "2026-11-05", "To Casero", -100.0, "", "")])
    d = pd.DataFrame([
        {"grupo": "Facturas", "cuenta": "REV_CONJ", "importe_pago": "100", "fecha": "20/05/2025", "concepto": "Gas", "categoria": "Suministros y telecom", "subcategoria": "Luz y gas", "importe": "-70"},
        {"grupo": "Facturas", "cuenta": "REV_CONJ", "importe_pago": "100", "fecha": "09/06/2025", "concepto": "Agua", "categoria": "Suministros y telecom", "subcategoria": "Agua", "importe": "-35"},
        {"grupo": "Facturas", "cuenta": "REV_CONJ", "importe_pago": "100", "fecha": "21/07/2025", "concepto": "Devolución", "categoria": "Suministros y telecom", "subcategoria": "Luz y gas", "importe": "5"}])
    r, c, av = aplicar_desgloses(raw, d)
    assert len(r) == 3 and abs(r["importe"].sum() + 100) < 1e-9 and sorted(r["fecha"]) == ["2025-05-20", "2025-06-09", "2025-07-21"]
    assert set(c["subcategoria"]) == {"Luz y gas", "Agua"} and not av
    # sin el pago, no se añade nada (y avisa)
    r2, c2, av2 = aplicar_desgloses(_raw([("REV_CONJ", "2026-11-05", "Otro", -50.0, "", "")]), d)
    assert len(r2) == 1 and c2.empty and av2
    # idempotente: las líneas tienen ids estables
    r3, _, _ = aplicar_desgloses(raw, d)
    assert sorted(r3["tx_id"]) == sorted(r["tx_id"])


def test_paso_de_excel_a_api_sin_duplicar():
    from finanzas.modelo import resolver_fuentes
    excel = _raw([("BANCO_CORR", "2026-11-02", "COMPRA TARJ. GLOVO", -20.0, "", ""),
                  ("BANCO_CORR", "2026-10-28", "RECIBO FIBRA", -30.0, "", "")]).assign(fuente="sabadell_xls")
    api = _raw([("BANCO_CORR", "2026-11-01", "GLOVO", -20.0, "", ""),       # la API lo fecha un día antes
                ("BANCO_CORR", "2026-11-03", "NOMINA", 2700.0, "", "")]).assign(fuente="enable_banking")
    limpio, fuera = resolver_fuentes(pd.concat([excel, api], ignore_index=True))
    assert sorted(limpio["concepto"]) == ["GLOVO", "NOMINA", "RECIBO FIBRA"] and len(fuera) == 1   # el Excel solo cuenta antes del 01/11
