import numpy as np
import pandas as pd
import pytest

from finanzas import rentabilidad as R


def _precios():
    # A sube un 10 % en febrero y baja un 10 % en marzo; B sube un 1 % cada mes. Varios precios en un mes: manda el último.
    return pd.DataFrame({"activo": ["A", "A", "A", "A", "B", "B", "B"],
                         "fecha": ["2026-01-15", "2026-01-31", "2026-02-28", "2026-03-31", "2026-01-31", "2026-02-28", "2026-03-31"],
                         "precio": [99, 100, 110, 99, 50, 50.5, 51.005]})


def test_rentabilidad_de_cada_activo():
    r = R.rent_activos(_precios())
    assert r.loc["2026-02-28", "A"] == pytest.approx(0.10)
    assert r.loc["2026-03-31", "A"] == pytest.approx(-0.10)
    assert r.loc["2026-03-31", "B"] == pytest.approx(0.01)
    assert r.loc["2026-01-31"].isna().all()  # el primer mes no tiene rentabilidad


def test_grupo_ponderado_por_valor_del_mes_anterior_e_ignora_aportaciones():
    r = R.rent_activos(_precios())
    # Enero: 3.000 € en A y 1.000 € en B. En febrero se aportan 10.000 € a B: no debe cambiar la rentabilidad de febrero.
    val = pd.DataFrame({"mes": ["2026-01-31", "2026-01-31", "2026-02-28", "2026-02-28"],
                        "activo": ["A", "B", "A", "B"], "valor": [3000, 1000, 3300, 11010]})
    g = R.rent_grupo(val, r)
    assert g.loc["2026-02-28"] == pytest.approx(0.75 * 0.10 + 0.25 * 0.01)
    # Marzo: pesos de final de febrero (3.300 y 11.010).
    assert g.loc["2026-03-31"] == pytest.approx((3300 * -0.10 + 11010 * 0.01) / 14310)
    solo_b = R.rent_grupo(val, r, ["B"])
    assert solo_b.loc["2026-02-28"] == pytest.approx(0.01)


def test_mes_sin_foto_usa_la_anterior_y_activo_vendido_no_cuenta():
    r = R.rent_activos(_precios())
    # Sin foto de febrero (hueco entre reconstrucción y pipeline): marzo se pondera con la de enero.
    val = pd.DataFrame({"mes": ["2026-01-31", "2026-01-31"], "activo": ["A", "B"], "valor": [1000, 1000]})
    g = R.rent_grupo(val, r)
    assert g.loc["2026-03-31"] == pytest.approx((-0.10 + 0.01) / 2)
    # Con foto de febrero en la que A ya no está (vendido): marzo solo cuenta B.
    val2 = pd.concat([val, pd.DataFrame({"mes": ["2026-02-28"], "activo": ["B"], "valor": [1010]})])
    assert R.rent_grupo(val2, r).loc["2026-03-31"] == pytest.approx(0.01)


def test_acumulada_metricas_y_tabla():
    r = pd.Series([0.10, -0.10, 0.05], index=pd.to_datetime(["2026-02-28", "2026-03-31", "2026-04-30"]))
    ac = R.acumulada(r)
    assert ac.iloc[0] == 0 and ac.index[0] == pd.Timestamp("2026-01-31")
    assert ac.iloc[-1] == pytest.approx(1.1 * 0.9 * 1.05 - 1)
    m = R.metricas(r)
    assert m["Rentabilidad"] == pytest.approx(1.1 * 0.9 * 1.05 - 1)
    assert m["Peor caída"] == pytest.approx(-0.10)
    assert np.isnan(m["Anualizada"])  # menos de 12 meses: no se anualiza
    t = R.tabla_mes_a_mes(r)
    assert t.loc[2026, "Feb"] == pytest.approx(0.10) and np.isnan(t.loc[2026, "Ene"])
    assert t.loc[2026, "Año"] == pytest.approx(ac.iloc[-1])


def test_actualizar_mes_sustituye_la_foto_del_mismo_mes():
    e = pd.DataFrame({"activo": ["A", "A", "B"], "fecha": ["2026-10-31", "2026-11-03", "2026-11-03"], "precio": [1, 2, 3]})
    n = pd.DataFrame({"activo": ["A"], "fecha": ["2026-11-10"], "precio": [2.5]})
    out = R.actualizar_mes(e, n, "fecha", ["activo"])
    assert sorted(out["precio"].tolist()) == [1, 2.5, 3]


def test_foto_mensual_y_sustituir_mes():
    det = pd.DataFrame({"categoria": ["Fondos Indexa", "Fondos MyInvestor", "Cripto", "Efectivo en cuentas", "Cripto"],
                        "activo": ["IE1", "IE1", "BTC", "REV", "EUR"], "cantidad": [10, 5, 0.5, 1, 100],
                        "valor": [1000, 500, 30000, 2000, 100]})
    p, v = R.foto_mensual(det, "2026-11-10")
    assert dict(zip(p["activo"], p["precio"])) == {"BTC": 60000, "IE1": 100}
    assert len(v) == 3 and set(v["categoria"]) == {"Fondos Indexa", "Fondos MyInvestor", "Cripto"}
    viejo = pd.DataFrame({"mes": ["2026-10-31", "2026-11-03"], "categoria": ["Cripto", "Cripto"], "activo": ["ETH", "ETH"], "cantidad": [1, 1], "valor": [3000, 3100]})
    out = R.sustituir_mes(viejo, v, "mes")
    assert len(out) == 4 and not ((out["activo"] == "ETH") & (out["mes"] == "2026-11-03")).any()  # ETH vendido en noviembre


def test_indice_oficial_sustituye_a_los_fondos_de_su_categoria():
    precios = pd.DataFrame({"activo": ["F1", "F1", "F1", "M", "M", "M"],
                            "fecha": ["2026-01-31", "2026-02-28", "2026-03-31"] * 2, "precio": [10, 11, 12, 100, 101, 102.01]})
    valores = pd.DataFrame({"mes": ["2026-01-31", "2026-02-28"] * 2, "categoria": ["Fondos Indexa"] * 2 + ["Fondos MyInvestor"] * 2,
                            "activo": ["F1", "F1", "M", "M"], "valor": [1000, 1100, 1000, 1010]})
    indices = pd.DataFrame({"categoria": ["Fondos Indexa"] * 3, "fecha": ["2026-01-30", "2026-02-27", "2026-03-31"], "indice": [1.0, 1.05, 1.071]})
    p2, v2 = R.con_indice_oficial(precios, valores, indices)
    r = R.rent_activos(p2)
    g = R.rent_grupo(v2[v2["categoria"] == "Fondos Indexa"], r)
    assert g.loc["2026-02-28"] == pytest.approx(0.05)  # el índice de Indexa, no el +10 % del fondo
    assert g.loc["2026-03-31"] == pytest.approx(0.02)
    assert r.loc["2026-02-28", "F1"] == pytest.approx(0.10)  # el fondo suelto sigue disponible
    total = R.rent_grupo(v2, r)
    assert total.loc["2026-03-31"] == pytest.approx((1100 * 0.02 + 1010 * 0.01) / 2110)
