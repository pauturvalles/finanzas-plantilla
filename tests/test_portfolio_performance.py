from finanzas.fuentes import portfolio_performance as pp

CSV = (
    "Fecha;Tipo;Valor;Divisa de la transacción;Valor bruto;Divisa del importe bruto;Tipo de cambio;Comisiones;Impuestos;"
    "Cantidad;ISIN;WKN;Símbolo del ticker;Nombre del valor;Nota\n"
    "2021-02-07T00:00;Compra;14,44;EUR;;;;;;0,9;;;DOTEUR;Polkadot;\n"
    "2021-02-07T00:00;Compra;14,81;EUR;;;;;;28;;;ADAEUR;Cardano;\n"
    "2022-03-01T00:00;Venta;1.234,50;EUR;;;;1,5;;10;;;ADAEUR;Cardano;\n"
    "2020-05-04T00:00;Compra;500;EUR;;;;;;21,3;IE00BD0NCM55;;;iShares Developed World;\n"
)


def test_lee_formato_pp():
    ops = pp.leer(CSV.encode("utf-8"), "Binance")
    assert list(ops["activo"]) == ["DOT", "ADA", "ADA", "IE00BD0NCM55"]
    assert ops.loc[2, "cantidad"] == -10 and ops.loc[2, "importe"] == 1234.5 and ops.loc[2, "comisiones"] == 1.5
    assert ops.loc[0, "fecha"] == "2021-02-07"
    pos = pp.posiciones(ops).set_index("activo")
    assert pos.loc["ADA", "cantidad"] == 18


def test_tabuladores():
    ops = pp.leer(CSV.replace(";", "\t"), "Binance")
    assert len(ops) == 4


def test_activos_normalizados():
    assert pp._activo("XXBTZEUR", "", "Bitcoin") == "BTC"
    assert pp._activo("EURUSDT", "", "Tether") == "USDT"
    assert pp._activo("XETHZEUR", "", "Ethereum") == "ETH"


def test_empareja_traspasos():
    cab = "Fecha;Tipo;Valor;Divisa de la transacción;Comisiones;Impuestos;Cantidad;ISIN;Símbolo del ticker;Nombre del valor;Nota\n"
    a = pp.leer(cab + "2021-03-31T00:00;Traspaso (salida);481,75;EUR;;;1,0;;KSMEUR;Kusama;\n", "Binance")
    b = pp.leer(cab + "2021-04-01T00:00;Traspaso (entrada);481,75;EUR;;;0,995;;KSMEUR;Kusama;\n", "Kraken")
    c = pp.leer(cab + "2021-05-01T00:00;Entrega (saliente);10;EUR;;;5;;ADAEUR;Cardano;\n", "Binance")
    import pandas as pd
    ops = pp.emparejar_traspasos(pd.concat([a, b, c], ignore_index=True))
    assert list(ops["pareja"]) == ["Kraken", "Binance", ""]
    assert ops.loc[0, "cantidad"] == -1.0 and ops.loc[1, "cantidad"] == 0.995


def test_clasifica_sueltos():
    import pandas as pd
    cab = "Fecha;Tipo;Valor;Divisa de la transacción;Comisiones;Impuestos;Cantidad;ISIN;Símbolo del ticker;Nombre del valor;Nota\n"
    filas = ("2021-07-31T00:00;Entrega (entrante);1,82;EUR;;;0,00005;;BTCEUR;Bitcoin;Generated interest\n"
             "2021-03-18T00:00;Entrega (saliente);0,42;EUR;;;0,7;;FETEUR;Fetch.ai;\n"
             "2022-12-15T00:00;Entrega (saliente);0;EUR;;;500;;MEDAEUR;Meda;Bancarrota\n"
             "2021-04-06T00:00;Entrega (entrante);58,86;EUR;;;0,0012;;BTCEUR;Bitcoin;\n")
    ops = pp.clasificar_sueltos(pp.emparejar_traspasos(pp.leer(cab + filas, "Binance")))
    assert list(ops["clase"]) == ["ingreso_en_especie", "comision", "perdida", "sin_explicar"]
