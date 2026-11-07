"""Genera los datos inventados de la demo del dashboard (demo/datos/*.csv).

Persona: Alex, 2.000 € netos al mes, comparte piso con Sam (cuenta conjunta al 50 %), aporta cada mes
a dos fondos indexados en MyInvestor y guarda un colchón en Trade Republic. Aficiones: gimnasio,
clases de guitarra y cine. Los movimientos pasan por el motor de reglas real (src/finanzas/reglas.py)
y el patrimonio por las mismas funciones que usa el pipeline, así que la demo enseña exactamente lo
que verá quien lo instale con sus datos.

    python demo/generar_demo.py            # regenera demo/datos/ hasta ayer (semilla fija)

El dashboard en modo demo lo vuelve a ejecutar solo cuando los datos tienen más de un día, así la demo
siempre llega hasta la fecha de hoy.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
from finanzas import patrimonio, reglas  # noqa: E402
from finanzas.modelo import COLUMNAS_RAW, asignar_ids, posibles_duplicados  # noqa: E402

DEMO = RAIZ / "demo"
INICIO, FIN = date(2024, 11, 1), date.today() - timedelta(days=1)
rng = np.random.default_rng(7)
SEMILLA_PRECIOS = int(os.environ.get("DEMO_SEMILLA", "3"))  # semilla de los precios de los fondos

FONDOS = {  # isin: (nombre, patrón del extracto, precio inicial, rentabilidad anual, volatilidad mensual)
    "IE00BD0NCM55": ("iShares Developed World Index Fund (IE) D Acc EUR", "ISHARES DEVELOPED WORLD", 16.20, 0.08, 0.035),
    "IE00BYWYCC39": ("iShares Emerging Markets Index Fund (IE) D Acc EUR", "ISHARES EMERGING MARKETS", 13.10, 0.05, 0.045),
}
PESOS = {"IE00BD0NCM55": 0.8, "IE00BYWYCC39": 0.2}

filas: list[dict] = []


def mov(cuenta, f: date, concepto, importe, contraparte="", fuente="enable_banking"):
    if f <= FIN:
        filas.append({"cuenta": cuenta, "fecha": f.isoformat(), "concepto": concepto, "contraparte": contraparte,
                      "iban_contraparte": "", "importe": round(float(importe), 2), "moneda": "EUR", "tipo_origen": "", "fuente": fuente})


def dia(anio, mes, d):
    """Día d del mes (o el último si no existe)."""
    ult = (date(anio + (mes == 12), mes % 12 + 1, 1) - timedelta(days=1)).day
    return date(anio, mes, min(d, ult))


def r(a, b):
    return round(float(rng.uniform(a, b)), 2)


def meses():
    a, m = INICIO.year, INICIO.month
    while date(a, m, 1) <= FIN:
        yield a, m
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)


# ------------------------------------------------------------------ precios de los fondos (uno por mes)
precios: dict[str, dict[tuple[int, int], float]] = {}
rng_p = np.random.default_rng(SEMILLA_PRECIOS)
for isin, (_, _, p0, rent, vol) in FONDOS.items():
    p, serie = p0, {}
    for am in meses():
        serie[am] = round(p, 4)
        p *= np.exp(rent / 12 - vol ** 2 / 2 + vol * rng_p.standard_normal())
    serie["hoy"] = round(p, 4)
    precios[isin] = serie

# ------------------------------------------------------------------ movimientos
supers = ["MERCADONA", "BONAREA", "LIDL", "CONSUM", "CARREFOUR EXPRESS"]
restaurantes = ["RESTAURANTE LA LLOTJA", "PIZZERIA NAPOLI", "SUSHI SHOP", "TABERNA EL PORRON", "BURGER LAB", "RAMEN KAGURA", "BAR CASA PACO"]
cafes = ["CAFETERIA EL MOLI", "COFFEE LAB", "CAFE DE LA PLAZA"]
cines = ["CINESA DIAGONAL", "YELMO CINES ICARIA", "CINES VERDI"]
participaciones = {i: [] for i in FONDOS}  # (fecha, títulos, importe)
precio_amz = None
for a, m in meses():
    hoy_m = date(a, m, 1)
    invierno = m in (11, 12, 1, 2)
    # Nómina (último día laborable aproximado) y paga extra en junio y diciembre.
    mov("BANCO_CORR", dia(a, m, 28), "NOMINA EMPRESA DEMO SL", 2000, "EMPRESA DEMO SL")
    # Reparto del sueldo: conjunta, inversión, colchón.
    mov("BANCO_CORR", dia(a, m, 1), "TRANSFERENCIA A ALEX GARCIA LOPEZ (CONJUNTA)", -875)
    mov("REV_CONJ", dia(a, m, 1), "Transferencia de ALEX GARCIA LOPEZ", 875, "ALEX GARCIA LOPEZ")
    mov("REV_CONJ", dia(a, m, 1), "Transferencia de SAM PEREZ RUIZ", 875, "SAM PEREZ RUIZ")
    aport = 250 if hoy_m < date(2025, 9, 1) else 300
    mov("BANCO_CORR", dia(a, m, 2), "TRANSFERENCIA A ALEX GARCIA LOPEZ MYINVESTOR", -aport)
    mov("MYINV", dia(a, m, 2), "TRANSFERENCIA RECIBIDA ALEX GARCIA LOPEZ", aport)
    for isin, (nombre, patron, *_ ) in FONDOS.items():
        imp = round(aport * PESOS[isin], 2)
        pr = precios[isin][(a, m)]
        f_ = dia(a, m, 5)
        mov("MYINV", f_, f"SUSCRIPCION {patron}", -imp)
        if f_ <= FIN:
            participaciones[isin].append((f_, imp / pr, imp))
    mov("BANCO_CORR", dia(a, m, 3), "TRASPASO A TRADE REPUBLIC", -100)
    mov("TR", dia(a, m, 3), "Transferencia ALEX GARCIA LOPEZ", 100, "ALEX GARCIA LOPEZ", fuente="trade_republic_csv")
    # Conjunta: alquiler, suministros, súper.
    mov("REV_CONJ", dia(a, m, 3), "ALQUILER PISO C/ MALLORCA", -1200, "INMOBILIARIA DEMO")
    mov("REV_CONJ", dia(a, m, 8), "ENDESA ENERGIA", -r(70, 95) if invierno else -r(45, 65))
    mov("REV_CONJ", dia(a, m, 10), "FIBRA DIGI", -30)
    mov("REV_CONJ", dia(a, m, 12), "AIGUES DE BARCELONA", -r(22, 30))
    for k in range(int(rng.integers(6, 9))):
        mov("REV_CONJ", dia(a, m, 2 + k * 4 + int(rng.integers(0, 3))), rng.choice(supers), -r(18, 75))
    if rng.random() < 0.7:
        mov("REV_CONJ", dia(a, m, int(rng.integers(5, 26))), "IKEA BADALONA" if rng.random() < 0.5 else "LEROY MERLIN", -r(15, 90))
    # Personales de Alex: aficiones.
    mov("BANCO_CORR", dia(a, m, 1), "BASIC-FIT SPAIN CUOTA", -29.99)
    mov("BANCO_CORR", dia(a, m, 5), "RECIBO ESCOLA DE MUSICA ACORDS - CLASES GUITARRA", -95)
    for _ in range(int(rng.integers(1, 4))):
        mov("BANCO_CORR", dia(a, m, int(rng.integers(1, 28))), rng.choice(cines), -r(7.5, 10.9))
    if rng.random() < 0.35:
        mov("BANCO_CORR", dia(a, m, int(rng.integers(1, 28))), "THOMANN MUSIKHAUS (CUERDAS Y ACCESORIOS)", -r(12, 45))
    if rng.random() < 0.25:
        mov("BANCO_CORR", dia(a, m, int(rng.integers(1, 28))), "DECATHLON", -r(15, 60))
    # Suscripciones y telecom.
    mov("BANCO_CORR", dia(a, m, 7), "SPOTIFY P3A8B7C", -11.99)
    mov("BANCO_CORR", dia(a, m, 15), "NETFLIX.COM", -13.99 if hoy_m >= date(2025, 6, 1) else -12.99)
    mov("BANCO_CORR", dia(a, m, 20), "DIGI MOBIL TARIFA MOVIL", -15)
    # Transporte, restaurantes, cafés, ropa, compras, salud.
    mov("BANCO_CORR", dia(a, m, 2), "TMB T-USUAL", -21.35)
    for _ in range(int(rng.integers(4, 8))):
        mov("BANCO_CORR", dia(a, m, int(rng.integers(1, 29))), rng.choice(restaurantes), -r(16, 52))
    for _ in range(int(rng.integers(4, 10))):
        mov("BANCO_CORR", dia(a, m, int(rng.integers(1, 29))), rng.choice(cafes), -r(1.8, 6.5))
    if rng.random() < 0.6:
        mov("BANCO_CORR", dia(a, m, int(rng.integers(1, 29))), "GLOVO PEDIDO", -r(15, 32))
    if rng.random() < 0.45:
        mov("BANCO_CORR", dia(a, m, int(rng.integers(1, 29))), rng.choice(["ZARA", "UNIQLO", "PRIMARK", "MANGO"]), -r(20, 85))
    if rng.random() < 0.6:
        imp = -r(12, 70)
        f_ = dia(a, m, int(rng.integers(1, 29)))
        mov("BANCO_CORR", f_, "AMAZON.ES COMPRA", imp)
    if rng.random() < 0.3:
        mov("BANCO_CORR", dia(a, m, int(rng.integers(1, 29))), "FARMACIA CENTRAL", -r(6, 25))
    if m % 2 == 0:
        mov("BANCO_CORR", dia(a, m, int(rng.integers(5, 25))), "PELUQUERIA ESTILO", -18)
    mov("BANCO_CORR", dia(a, m, 22), "SEGURO MAPFRE SALUD", -42.5)
    # Intereses de Trade Republic.
    mov("TR", dia(a, m, 1), "Interest payment", fuente="trade_republic_csv", importe= r(8, 12) + (date(a, m, 1) - INICIO).days * 0.004)
    # Viajes y regalos según la época.
    if m == 7:
        mov("BANCO_CORR", dia(a, m, 10), "RYANAIR VUELOS", -r(120, 190))
        mov("BANCO_CORR", dia(a, m, 12), "BOOKING.COM HOTEL", -r(250, 380))
    if m == 12:
        mov("BANCO_CORR", dia(a, m, 18), "RENFE VIAJEROS", -r(60, 95))
        mov("BANCO_CORR", dia(a, m, 20), "EL CORTE INGLES REGALOS", -r(80, 160))
    if m == 4:
        mov("BANCO_CORR", dia(a, m, 15), "AEAT DEVOLUCION IRPF", r(250, 420))
        filas[-1]["concepto"] = "TRANSFERENCIA DEVOLUCION IRPF AGENCIA TRIBUTARIA"

# Gasto extraordinario: guitarra nueva (marzo de 2026) y un par de cosas sin regla para «Calidad del dato».
mov("BANCO_CORR", date(2026, 3, 14), "MUSICOPOLIX - GUITARRA NUEVA FENDER", -749)
mov("BANCO_CORR", date(2026, 3, 14), "MUSICOPOLIX - GUITARRA NUEVA FENDER", -749)  # cobro repetido que se devolvió
mov("BANCO_CORR", date(2026, 3, 19), "DEVOLUCION MUSICOPOLIX - GUITARRA NUEVA FENDER", 749)
ult_mes = FIN.replace(day=1) - timedelta(days=1)
for f_, c_, i_ in ((FIN - timedelta(days=3), "COMPRA TARJ. LA TRASTIENDA DEL GRACIA", -23.40),
                   (ult_mes - timedelta(days=9), "COMPRA TARJ. PAPERERIA NOU", -8.95),
                   (ult_mes - timedelta(days=15), "BIZUM DE JORDI MARTIN - ENTRADAS CONCIERTO", 35.0),
                   (ult_mes - timedelta(days=20), "TRANSFERENCIA DE LUCIA SANZ", 60.0)):
    mov("BANCO_CORR", f_, c_, i_)
# Un cargo que llega por dos vías (banco y fichero) para enseñar «posibles duplicados».
mov("BANCO_CORR", FIN - timedelta(days=6), "AMAZON.ES COMPRA", -34.99)
mov("BANCO_CORR", FIN - timedelta(days=6), "AMAZON.ES COMPRA", -34.99, fuente="fichero")

raw = asignar_ids(pd.DataFrame(filas))[COLUMNAS_RAW].sort_values(["fecha", "cuenta"]).reset_index(drop=True)

# ------------------------------------------------------------------ clasificación (el motor real)
cfg = yaml.safe_load((DEMO / "config" / "cuentas.yaml").read_text(encoding="utf-8"))
reg = pd.read_csv(DEMO / "config" / "reglas_comercios.csv", keep_default_na=False, comment=None)
reg["prioridad"] = pd.to_numeric(reg["prioridad"], errors="coerce").fillna(999)
sin_dup = raw[raw["fuente"] != "fichero"]
movs = reglas.clasificar(sin_dup, reg, pd.DataFrame(), cfg)
movs = movs[movs["mes"] >= cfg["clasificar_desde"]]

# ------------------------------------------------------------------ saldos y patrimonio
saldo_ini = {"BANCO_CORR": 2400.0, "TR": 5000.0, "REV_CONJ": 900.0, "MYINV": 0.0}
saldos = pd.DataFrame([{"cuenta": c, "tipo": "CLBD", "fecha": FIN.isoformat(),
                        "saldo": round(s + sin_dup.loc[sin_dup["cuenta"] == c, "importe"].sum(), 2)} for c, s in saldo_ini.items()])
pct = {c["id"]: c["pct_titular"] for c in cfg["cuentas"]}
pos = []
for isin, (nombre, *_ ) in FONDOS.items():
    t = sum(x[1] for x in participaciones[isin])
    coste = sum(x[2] for x in participaciones[isin])
    pos.append({"plataforma": "MyInvestor", "producto": nombre, "referencia": isin, "activo": isin, "cantidad": round(t, 4),
                "valor": round(t * precios[isin]["hoy"], 2), "aportado": round(coste, 2), "coste_fiscal": round(coste, 2),
                "fecha_valor": FIN.isoformat(), "metodo": f"{t:.4f} participaciones × valor liquidativo"})
for _, s in saldos.iterrows():
    pos.append({"plataforma": "Cuentas", "producto": s["cuenta"], "referencia": "CLBD", "valor": round(s["saldo"] * pct[s["cuenta"]], 2),
                "aportado": None, "metodo": "api (tu parte)"})
det = patrimonio.detalle(pos, [], pd.DataFrame(), lambda _: None)
aportado_hoy = {"Fondos MyInvestor": sum(p["aportado"] for p in pos if p["plataforma"] == "MyInvestor")}
cats = patrimonio.por_categoria(det, pd.DataFrame(), pd.DataFrame(), aportado_hoy)
cats["aportado"] = cats["categoria"].map(aportado_hoy)
pat = pd.DataFrame(pos)
pat.insert(0, "fecha", FIN.isoformat())

# Evolución mensual (fin de cada mes): efectivo por saldos acumulados y fondos a su precio de ese mes.
rec = []
for a, m in meses():
    fin_m = min(dia(a, m, 31), FIN)
    if fin_m >= FIN.replace(day=1):
        break
    ef = sum((saldo_ini[c] + sin_dup.loc[(sin_dup["cuenta"] == c) & (sin_dup["fecha"] <= fin_m.isoformat()), "importe"].sum()) * pct[c] for c in saldo_ini)
    fo = sum(sum(x[1] for x in participaciones[i] if x[0] <= fin_m) * precios[i][(a, m)] for i in FONDOS)
    rec += [{"mes": fin_m.isoformat(), "categoria": "Efectivo en cuentas", "valor": round(ef, 2)},
            {"mes": fin_m.isoformat(), "categoria": "Fondos MyInvestor", "valor": round(fo, 2)}]
hist = pd.concat([cats.assign(fecha=(FIN - timedelta(days=d)).isoformat(),
                              valor=cats["valor"] * (1 - 0.002 * d)) for d in range(min(5, FIN.day - 1), -1, -1)], ignore_index=True)

# ------------------------------------------------------------------ pestañas manuales y de control
presupuestos = pd.DataFrame({"categoria": ["Supermercado y alimentación", "Restauración y delivery", "Ocio y cultura", "Ropa y calzado", "Música (guitarra)"],
                             "presupuesto_mensual": [160, 150, 35, 40, 110], "nota": ["Tu parte de la conjunta", "", "Cine", "", "Clases + material"]})
ahora = f"{FIN.isoformat()}T07:05:00+00:00"
registro = pd.DataFrame([{"fecha": ahora, "fuente": f, "estado": "ok", "filas": n, "detalle": d} for f, n, d in (
    ("BANCO_CORR", 42, "desde 2026-10-20"), ("TR", 3, "desde 2026-10-20"), ("REV_CONJ", 18, "desde 2026-10-20"),
    ("MYINV (CSV)", 3, "1 ficheros"), ("MyInvestor", 2, ""), ("Patrimonio por categoría", 2, ""))])

salida = {
    "raw": raw, "movimientos": movs, "pendientes": reglas.pendientes(movs),
    "posibles_duplicados": posibles_duplicados(raw[raw["fecha"] >= (FIN - timedelta(days=40)).isoformat()]),
    "saldos": saldos, "patrimonio": pat, "patrimonio_detalle": det, "patrimonio_categorias": cats,
    "patrimonio_categorias_historico": hist, "patrimonio_historico_reconstruido": pd.DataFrame(rec),
    "presupuestos": presupuestos, "registro": registro,
}
(DEMO / "datos").mkdir(exist_ok=True)
for nombre, df in salida.items():
    df.to_csv(DEMO / "datos" / f"{nombre}.csv", index=False)
print(f"Demo: {len(movs)} movimientos de {movs['mes'].min()} a {movs['mes'].max()}, {len(salida['pendientes'])} pendientes, "
      f"patrimonio {det['valor'].sum():,.0f} €")
