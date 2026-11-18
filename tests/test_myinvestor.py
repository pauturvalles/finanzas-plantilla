"""Valoración de MyInvestor con traspasos entre clases (D → S), ventanas de fechas y compras confirmadas."""
import pandas as pd
import pytest

from finanzas import patrimonio as P

D_DEV, S_DEV, D_EM, S_EM, SC = "IE00BD0NCM55", "IE000ZYRH0Q7", "IE00BYWYCC39", "IE000QAZP7L2", "IE00B42W4L06"
PAT_DEV, PAT_EM = "DEVELOPED WORLD|DEVL WRLD|DEVELOPED WRD", "EMERGING MARKETS|EMRG MARKT|EMERGING MRK"


def cfg(con_traspaso=True, part_origen_dev=1554.18):
    c = {"myinvestor_fraccion_invertida": 1.0, "myinvestor_dias_ejecucion": 2,
         "myinvestor_fondos": [
             {"nombre_en_extracto": PAT_DEV, "isin": D_DEV, "peso": 0.8, "hasta": "2026-11-10"},
             {"nombre_en_extracto": PAT_DEV, "isin": S_DEV, "peso": 0.8, "desde": "2026-11-01"},
             {"nombre_en_extracto": PAT_EM, "isin": D_EM, "peso": 0.2, "hasta": "2026-11-10"},
             {"nombre_en_extracto": PAT_EM, "isin": S_EM, "peso": 0.2, "desde": "2026-11-01"}]}
    if con_traspaso:
        c["myinvestor_traspasos"] = [
            {"origen": D_DEV, "destino": S_DEV, "fecha_salida": "2026-11-12", "fecha_entrada": "2026-11-17",
             "participaciones_origen": part_origen_dev, "participaciones_destino": 3738.99, "importe": 45739.51,
             "vl_origen": 29.43, "vl_destino": 12.2331},
            {"origen": D_EM, "destino": S_EM, "fecha_salida": "2026-11-12", "fecha_entrada": "2026-11-17",
             "participaciones_origen": 271.51, "participaciones_destino": 403.53, "importe": 5150.27,
             "vl_origen": 18.9687, "vl_destino": 12.7628}]
        c["myinvestor_compras_confirmadas"] = [
            {"isin": S_DEV, "fecha": "2026-11-11", "importe": 425, "participaciones": 33.83},
            {"isin": S_EM, "fecha": "2026-11-11", "importe": 50, "participaciones": 3.76}]
    return c


FOTO = pd.DataFrame([
    {"isin": D_DEV, "fondo": "Dev D", "fecha": "2026-10-29", "participaciones": 1554.18, "invertido": 40000, "coste_fiscal": 30000},
    {"isin": D_EM, "fondo": "EM D", "fecha": "2026-10-29", "participaciones": 271.51, "invertido": 4000, "coste_fiscal": 3500}])
VLS = pd.DataFrame([{"isin": D_DEV, "fecha": "2026-10-29", "vl": 28.0}, {"isin": D_EM, "fecha": "2026-10-29", "vl": 18.0},
                    {"isin": S_DEV, "fecha": "2026-11-17", "vl": 12.2331}, {"isin": S_EM, "fecha": "2026-11-17", "vl": 12.7628}])
# Aportación de 500 € a MyInvestor el 09/11 (se invierte el 11/11, ya en la clase S).
MOV = pd.DataFrame([{"cuenta": "SAB_CORR", "fecha": "2026-11-09", "concepto": "TRANSFERENCIA", "importe": -500.0, "iban_contraparte": "ES00MYINV"}])


@pytest.fixture(autouse=True)
def iban(monkeypatch):
    monkeypatch.setenv("IBAN_MYINV", "ES00MYINV")


def posicion(c, foto=FOTO, mov=MOV, vls=VLS):
    return P.myinvestor(mov, c, foto, pd.DataFrame(), vls).set_index("referencia")


def test_traspaso_mueve_participaciones_a_la_clase_s():
    p = posicion(cfg())
    assert D_DEV not in p.index and D_EM not in p.index          # traspasadas enteras: sin fila
    assert p.loc[S_DEV, "cantidad"] == pytest.approx(3738.99 + 33.83)  # traspaso + compra ya hecha en la S
    assert p.loc[S_EM, "cantidad"] == pytest.approx(403.53 + 3.76)


def test_el_destino_hereda_el_coste_fiscal_y_lo_invertido_arranca_en_el_valor_del_traspaso():
    p = posicion(cfg())
    assert p.loc[S_DEV, "aportado"] == pytest.approx(45739.51 + 425)   # como la web: valor con que llega + compras
    assert p.loc[S_EM, "aportado"] == pytest.approx(5150.27 + 50)
    assert p.loc[S_DEV, "coste_fiscal"] == pytest.approx(30000 + 425)
    assert p.loc[S_EM, "coste_fiscal"] == pytest.approx(3500 + 50)


def test_la_compra_estimada_del_mes_no_se_cuenta_dos_veces():
    # La aportación de 500 € (deducida el 11/11) se sustituye por las compras confirmadas de ese día.
    p = posicion(cfg())
    assert p.loc[S_DEV, "aportado"] == pytest.approx(45739.51 + 425)
    assert "órdenes confirmadas" in p.loc[S_DEV, "metodo"] and "traspaso de IE00BD0NCM55" in p.loc[S_DEV, "metodo"]


def test_valora_con_el_vl_de_la_orden_si_no_hay_otro():
    p = posicion(cfg())
    assert p.loc[S_DEV, "valor"] == pytest.approx((3738.99 + 33.83) * 12.2331, abs=0.01)
    assert p.loc[S_DEV, "fecha_valor"] == "2026-11-17"


def test_sin_traspaso_configurado_la_clase_nueva_pide_foto():
    p = posicion(cfg(con_traspaso=False))
    assert "falta la foto inicial" in p.loc[S_DEV, "metodo"]


def test_residuo_si_la_foto_no_coincide_con_lo_traspasado():
    foto = FOTO.assign(participaciones=[1600.0, 271.51])  # la foto tiene 45,82 participaciones más de las que se traspasaron
    p = posicion(cfg(), foto=foto)
    assert p.loc[D_DEV, "cantidad"] == pytest.approx(1600 - 1554.18)
    assert "residuo" in p.loc[D_DEV, "metodo"]
    # Solo se lleva el destino la parte proporcional del coste fiscal (1554,18 de 1600).
    assert p.loc[S_DEV, "coste_fiscal"] == pytest.approx(30000 * 1554.18 / 1600 + 425)


def test_las_compras_de_otra_clase_no_cuentan_fuera_de_su_ventana():
    # Sin traspasos ni confirmadas, la aportación del 09/11 solo va a las clases cuya ventana la incluye (la S), no a la D.
    c = cfg(con_traspaso=False)
    foto = pd.concat([FOTO, pd.DataFrame([{"isin": S_DEV, "fondo": "Dev S", "fecha": "2026-10-29", "participaciones": 10.0, "invertido": 100, "coste_fiscal": 100},
                                          {"isin": S_EM, "fondo": "EM S", "fecha": "2026-10-29", "participaciones": 5.0, "invertido": 50, "coste_fiscal": 50}])])
    p = posicion(c, foto=foto, vls=pd.concat([VLS, pd.DataFrame([{"isin": S_DEV, "fecha": "2026-11-09", "vl": 12.5}, {"isin": S_EM, "fecha": "2026-11-09", "vl": 13.0}])]))
    assert p.loc[D_DEV, "cantidad"] == pytest.approx(1554.18)
    assert p.loc[S_DEV, "cantidad"] == pytest.approx(10 + 500 * 0.8 / 12.5)


def test_vl_conocidos_de_las_ordenes():
    v = P.vl_conocidos(cfg())
    assert set(v["isin"]) == {D_DEV, S_DEV, D_EM, S_EM}
    assert v[(v["isin"] == S_DEV)]["vl"].iloc[0] == pytest.approx(12.2331)


def test_los_vl_con_fecha_iso_no_se_leen_con_mes_y_dia_cambiados():
    # «2026-11-12» (día ≤ 12) se leía como 12 de diciembre: quedaba en el futuro y se valoraba con un VL más viejo.
    vls = pd.concat([VLS, pd.DataFrame([{"isin": SC, "fecha": "2026-11-05", "vl": 400.0}, {"isin": SC, "fecha": "2026-11-12", "vl": 430.0}])])
    foto = pd.concat([FOTO, pd.DataFrame([{"isin": SC, "fondo": "SC", "fecha": "2026-10-29", "participaciones": 10.0, "invertido": 1, "coste_fiscal": 1}])])
    c = cfg()
    c["myinvestor_fondos"].append({"nombre_en_extracto": "GLOB SMALL CAP", "isin": SC, "peso": 0.0})
    p = posicion(c, foto=foto, vls=vls)
    assert p.loc[SC, "valor"] == pytest.approx(4300.0) and p.loc[SC, "fecha_valor"] == "2026-11-12"
