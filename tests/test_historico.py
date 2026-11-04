import pandas as pd

from finanzas import historico as H


def test_participaciones_y_extension():
    ops = pd.DataFrame({"fecha": ["2021-01-10", "2021-03-05", "2021-03-20"], "cuenta_pp": ["Indexa", "Indexa", "Binance"],
                        "tipo": ["compra", "compra", "compra"], "activo": ["IE00BFPM9V94", "IE00BFPM9V94", "BTC"],
                        "cantidad": [10.0, 5.0, 0.1], "importe": [1000, 520, 4000]})
    meses = pd.date_range("2021-01-31", "2021-06-30", freq="ME")
    p = H.participaciones_mensuales(ops, meses, {})
    us = p[p["activo"] == "IE00BFPM9V94"].set_index("mes")["cantidad"]
    assert us["2021-01-31"] == 10 and us["2021-03-31"] == 15 and p[(p["activo"] == "BTC")].set_index("mes").loc["2021-02-28", "cantidad"] == 0
    of = pd.DataFrame({"categoria": ["Fondos Indexa"], "activo": ["IE00BFPM9V94"], "cantidad": [21.0]})
    e = H.extender_hasta_hoy(p, of, pd.Timestamp("2021-03-31"), meses).set_index(["mes", "activo"])["cantidad"]
    assert e[(pd.Timestamp("2021-06-30"), "IE00BFPM9V94")] == 21 and 15 < e[(pd.Timestamp("2021-05-31"), "IE00BFPM9V94")] < 21


def test_saldo_trade_republic_hacia_atras():
    csv = ('"datetime","date","amount","fee","tax"\n'
           '"x","2024-01-01","100","",""\n"x","2024-02-10","-30","-1",""\n"x","2024-03-05","50","",""\n').encode()
    s = H.saldos_trade_republic(csv, 119.0)
    assert s[pd.Timestamp("2024-03-05")] == 119 and s[pd.Timestamp("2024-02-10")] == 69 and s[pd.Timestamp("2024-01-01")] == 100


def test_saldos_revolut_y_nombre_desconocido():
    csv = ("Tipo,Producto,Fecha de inicio,Fecha de finalización,Descripción,Importe,Comisión,Divisa,State,Saldo\n"
           "Recargas,Actual,2020-01-03 14:30:17,2020-01-03 14:30:20,Recarga,50.00,0.00,EUR,COMPLETADO,50.00\n"
           "Transferir,Ahorros,2020-01-04 10:00:00,2020-01-04 10:00:00,Hucha,10.00,0.00,EUR,COMPLETADO,10.00\n"
           "Pago con tarjeta,Actual,2020-01-05 19:24:04,2020-01-05 18:23:43,Farmacia,-20.00,0.00,EUR,COMPLETADO,30.00\n").encode()
    s = H.saldos_revolut(csv, 0.5)
    assert s[pd.Timestamp("2020-01-05")] == (30 + 10) * 0.5
    precios = {"NULL": pd.Series([10.0, 11.0, 12.0], index=pd.to_datetime(["2021-01-01", "2021-02-01", "2021-03-01"]))}
    ops = pd.DataFrame({"fecha": ["2021-01-02", "2021-02-02", "2021-03-02"], "tipo": "compra", "activo": "0P0000TEST.F",
                        "cantidad": [1.0, 1.0, 1.0], "importe": [10.0, 11.0, 12.0]})
    assert H.identificar_sin_nombre(precios, ops, ["NULL"])["NULL"][0] == "0P0000TEST.F"


def test_interpola_hasta_hoy():
    s = pd.Series([82.0], index=pd.to_datetime(["2024-01-18"]))
    c = H.completar_con_precio_actual(s, 130.0, pd.Timestamp("2026-01-18"))
    assert c[pd.Timestamp("2024-01-18")] == 82 and c[pd.Timestamp("2026-01-18")] == 130 and 100 < c[pd.Timestamp("2025-01-18")] < 110


def test_plan_de_pensiones_con_alias_es_fondo():
    assert H.categoria_pp("Indexa", "0P0000TEST.F", {"0P0000TEST.F": "N0001"}) == "Plan de pensiones Indexa"
    assert H.categoria_pp("Binance", "BTC", {}) == "Cripto"
