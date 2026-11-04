import pandas as pd

from finanzas import fiscal


def test_cuota_ahorro():
    assert fiscal.cuota_ahorro(10_000) == 6_000 * 0.19 + 4_000 * 0.21
    assert fiscal.cuota_ahorro(0) == 0


def _pp(filas):
    return pd.DataFrame(filas, columns=["fecha", "cuenta_pp", "tipo", "activo", "cantidad", "importe", "clase", "pareja"])


def test_fifo_traspasos_permutas_y_staking():
    pp = _pp([
        ["2021-01-01", "Binance", "compra", "BTC", 1.0, 10_000, "", ""],
        ["2021-06-01", "Binance", "compra", "BTC", 1.0, 30_000, "", ""],
        ["2021-07-01", "Binance", "traspaso_salida", "BTC", -0.5, 15_000, "", "Ledger"],   # no es venta
        ["2021-07-01", "Ledger", "traspaso_entrada", "BTC", 0.5, 15_000, "", "Binance"],
        ["2022-01-01", "Binance", "venta", "BTC", -1.5, 60_000, "", ""],                   # FIFO: 1×10.000 + 0,5×30.000
        ["2022-01-01", "Binance", "compra", "ETH", 10, 60_000, "", ""],                   # permuta: venta BTC + compra ETH
        ["2022-02-01", "Binance", "salida", "ETH", -0.01, 50, "comision", ""],
    ])
    of = pd.DataFrame([["2026-01-01", "Kraken", "ingreso_en_especie", "ETH", "0.5", ""]],
                      columns=["fecha", "plataforma", "tipo", "activo", "cantidad", "importe"])
    ventas, lotes = fiscal.fifo_cripto(pp, of, {"Kraken": "2025-12-31"}, posiciones_hoy={"ETH": 10.6, "BTC": 0.5})
    v = ventas.iloc[0]
    assert v["coste_fifo"] == 25_000 and v["ganancia"] == 35_000 and v["ejercicio"] == 2022 and v["sin_lote"] == 0
    btc = lotes[lotes["activo"] == "BTC"]
    assert abs(btc["cantidad"].sum() - 0.5) < 1e-9 and btc["coste"].sum() == 15_000
    eth = lotes[lotes["activo"] == "ETH"]
    assert abs(eth["cantidad"].sum() - 10.6) < 1e-9          # 9,99 comprados + 0,5 staking + 0,11 sin registrar
    assert set(eth["origen"]) == {"compra", "staking (coste 0)", "sin registrar (coste 0)"}
    sim = fiscal.simular_venta(lotes, "BTC", 0.25, 80_000)
    assert sim["coste"] == 7_500 and sim["ganancia"] == 12_500
