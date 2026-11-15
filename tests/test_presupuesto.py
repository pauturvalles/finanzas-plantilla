import pandas as pd
import pytest

from finanzas.presupuesto import meses_del_periodo, tabla_anual


def test_meses_del_periodo():
    hoy = pd.Timestamp("2026-11-15")
    assert meses_del_periodo(2026, hoy) == (10 + 15 / 30, 12.0, "2026-01", "2026-11")
    assert meses_del_periodo(2025, hoy, "2025-02") == (11.0, 11.0, "2025-02", "2025-12")  # clasificado desde febrero
    assert meses_del_periodo(2025, hoy)[:2] == (12.0, 12.0)


def test_un_gasto_de_golpe_no_sale_superado_si_cabe_en_el_ano():
    # 60 €/mes de ropa; en lo que va de 2026 (6,5 meses) se han gastado 300 € en un solo mes.
    hoy = pd.Timestamp("2026-07-16")
    gastos = pd.DataFrame({"mes": ["2026-03", "2025-12"], "categoria": ["Ropa", "Ropa"], "importe": [300.0, 999.0]})
    tab, info = tabla_anual(gastos, pd.Series({"Ropa": 60.0}), 2026, hoy, media_mensual=pd.Series({"Ropa": 50.0}))
    f = tab.iloc[0]
    assert info["meses_transcurridos"] == pytest.approx(6 + 16 / 31)
    assert f["Presupuesto del año"] == 720 and f["Gastado"] == 300  # el gasto de 2025 no cuenta
    assert f["Presupuesto a la fecha"] == pytest.approx(60 * (6 + 16 / 31))
    assert f["Proyección a fin de año"] == pytest.approx(300 + 50 * (12 - 6 - 16 / 31))
    assert f["Estado"] == "🟢 dentro"


def test_estados():
    hoy = pd.Timestamp("2026-06-30")
    gastos = pd.DataFrame({"mes": ["2026-01", "2026-02"], "categoria": ["A", "B"], "importe": [700.0, 500.0]})
    tab, info = tabla_anual(gastos, pd.Series({"A": 100.0, "B": 100.0, "C": 10.0}), 2026, hoy,
                            media_mensual=pd.Series({"B": 150.0}))
    est = dict(zip(tab["Categoría"], tab["Estado"]))
    assert est == {"A": "🔴 por encima", "B": "🟠 va a pasarse", "C": "🟢 dentro"}  # B: 500 + 150×6 = 1.400 > 1.200
    assert info["gastado_sin_presupuesto"] == 0
