"""Exports CSV de «Transacciones de cartera» de Portfolio Performance (uno por cuenta de valores).

Es un registro mantenido a mano: sirve para rellenar huecos (compras antiguas, exchanges
ya cerrados), pero **mandan las fuentes oficiales** (DECISIONES §7). Por eso aquí solo se
normaliza; la conciliación con Indexa, MyInvestor y los exchanges se hace aparte.

Cabecera en español (PP en castellano):
Fecha;Tipo;Valor;Divisa de la transacción;Valor bruto;Divisa del importe bruto;Tipo de cambio;
Comisiones;Impuestos;Cantidad;ISIN;WKN;Símbolo del ticker;Nombre del valor;Nota
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import pandas as pd

# Tipos de PP (castellano e inglés) → tipo normalizado y signo de la cantidad.
TIPOS = {
    "compra": ("compra", 1), "buy": ("compra", 1),
    "venta": ("venta", -1), "sell": ("venta", -1),
    "entrega (entrante)": ("entrada", 1), "entrega entrante": ("entrada", 1), "delivery (inbound)": ("entrada", 1),
    "entrega (saliente)": ("salida", -1), "entrega saliente": ("salida", -1), "delivery (outbound)": ("salida", -1),
    "traspaso (entrada)": ("traspaso_entrada", 1), "traspaso (salida)": ("traspaso_salida", -1),
    "traslado (entrante)": ("traspaso_entrada", 1), "transferencia (entrante)": ("traspaso_entrada", 1), "transfer (inbound)": ("traspaso_entrada", 1),
    "traslado (saliente)": ("traspaso_salida", -1), "transferencia (saliente)": ("traspaso_salida", -1), "transfer (outbound)": ("traspaso_salida", -1),
}
COLUMNAS = {  # castellano / inglés → nombre interno
    "fecha": "fecha", "date": "fecha", "tipo": "tipo", "type": "tipo", "valor": "importe", "value": "importe",
    "comisiones": "comisiones", "fees": "comisiones", "impuestos": "impuestos", "taxes": "impuestos",
    "cantidad": "cantidad", "shares": "cantidad", "isin": "isin", "símbolo del ticker": "ticker", "ticker symbol": "ticker",
    "nombre del valor": "nombre", "security name": "nombre", "nota": "nota", "note": "nota",
    "divisa de la transacción": "divisa", "transaction currency": "divisa",
}


def _num(s: pd.Series) -> pd.Series:
    t = s.fillna("").astype(str).str.strip().str.replace("\u00a0", "").str.replace(" ", "")
    t = t.where(~t.str.contains(","), t.str.replace(".", "", regex=False).str.replace(",", ".", regex=False))
    return pd.to_numeric(t.replace("", None), errors="coerce")


# Nombres de exchange o de PP → código común (para cruzar cuentas y fuentes oficiales).
ALIAS = {"XXBTZ": "BTC", "XXBT": "BTC", "XBT": "BTC", "XETHZ": "ETH", "XETH": "ETH", "XXRPZ": "XRP",
         "XXLMZ": "XLM", "XLTCZ": "LTC", "XDGZ": "DOGE"}
POR_NOMBRE = {"TETHER": "USDT", "BITCOIN": "BTC", "ETHEREUM": "ETH", "USD COIN": "USDC"}
FIAT = {"EUR", "USD", "USDT", "GBP"}


def _activo(ticker: str, isin: str, nombre: str) -> str:
    """Identificador del activo: ISIN si lo hay; si no, el ticker sin la divisa (DOTEUR → DOT,
    XXBTZEUR → BTC). Si quitando la divisa no queda nada útil, se usa el nombre (Tether → USDT)."""
    if isinstance(isin, str) and isin.strip():
        return isin.strip().upper()
    t = (ticker or "").strip().upper()
    base = re.sub(r"[-/]?(EUR|USD|USDT)$", "", t)
    base = ALIAS.get(base, base)
    nom = (nombre or "").strip().upper()
    if not base or base in FIAT:
        return POR_NOMBRE.get(nom, nom or t)
    return base


def leer(contenido: bytes | str, cuenta: str) -> pd.DataFrame:
    """Normaliza un CSV de PP. `cuenta` = nombre de la cuenta de valores (sale del nombre del fichero)."""
    texto = contenido.decode("utf-8-sig") if isinstance(contenido, bytes) else contenido
    delim = csv.Sniffer().sniff(texto.splitlines()[0], delimiters=";,\t").delimiter
    df = pd.read_csv(io.StringIO(texto), sep=delim, dtype=str, keep_default_na=False)
    df.columns = [COLUMNAS.get(c.strip().lower(), c.strip().lower()) for c in df.columns]
    tipo = df["tipo"].str.strip().str.lower().map(lambda t: TIPOS.get(t, ("desconocido:" + t, 0)))
    cant = _num(df["cantidad"])
    out = pd.DataFrame({
        "fecha": pd.to_datetime(df["fecha"].str[:10], errors="coerce").dt.strftime("%Y-%m-%d"),
        "cuenta_pp": cuenta,
        "tipo": tipo.map(lambda x: x[0]),
        "activo": [_activo(t, i, n) for t, i, n in zip(df.get("ticker", ""), df.get("isin", ""), df.get("nombre", ""))],
        "isin": df.get("isin", "").str.strip(),
        "ticker": df.get("ticker", "").str.strip(),
        "nombre": df.get("nombre", "").str.strip(),
        "cantidad": cant * tipo.map(lambda x: x[1]),
        "importe": _num(df["importe"]),
        "comisiones": _num(df.get("comisiones", pd.Series("", index=df.index))).fillna(0.0),
        "impuestos": _num(df.get("impuestos", pd.Series("", index=df.index))).fillna(0.0),
        "divisa": df.get("divisa", "EUR"),
        "nota": df.get("nota", ""),
        "fuente": "portfolio_performance",
    })
    return out


def leer_carpeta(carpeta: str | Path) -> pd.DataFrame:
    """Lee todos los CSV de una carpeta; el nombre del fichero (sin extensión) es la cuenta."""
    partes = [leer(f.read_bytes(), f.stem) for f in sorted(Path(carpeta).glob("*.csv"))]
    return pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()


def posiciones(ops: pd.DataFrame) -> pd.DataFrame:
    """Cantidad actual y coste de compra acumulado por cuenta y activo según PP."""
    compras = ops[ops["tipo"].isin(["compra", "entrada", "traspaso_entrada"])]
    return ops.groupby(["cuenta_pp", "activo", "nombre"], as_index=False).agg(
        cantidad=("cantidad", "sum"), operaciones=("tipo", "size")).merge(
        compras.groupby(["cuenta_pp", "activo"], as_index=False).agg(coste_compras=("importe", "sum")),
        on=["cuenta_pp", "activo"], how="left")


ENTRADAS = ("traspaso_entrada", "entrada")
SALIDAS = ("traspaso_salida", "salida")


def emparejar_traspasos(ops: pd.DataFrame, dias: int = 3, tolerancia: float = 0.02) -> pd.DataFrame:
    """Empareja cada salida de un activo de una cuenta con su entrada en otra cuenta:
    mismo activo, fechas a ≤ `dias` y cantidades que difieren ≤ `tolerancia` (las comisiones
    de retirada hacen que llegue algo menos). Añade `pareja` (cuenta del otro lado) y
    `id_traspaso`; las que no encuentran pareja quedan con `pareja` vacía."""
    ops = ops.copy()
    ops["pareja"], ops["id_traspaso"] = "", ""
    f = pd.to_datetime(ops["fecha"])
    sal = ops[ops["tipo"].isin(SALIDAS)]
    ent = ops[ops["tipo"].isin(ENTRADAS)]
    candidatos = []
    for i, s_ in sal.iterrows():
        q = abs(s_["cantidad"])
        if not q:
            continue
        for j, e in ent[(ent["activo"] == s_["activo"]) & (ent["cuenta_pp"] != s_["cuenta_pp"])].iterrows():
            dd = abs((f[j] - f[i]).days)
            dif = abs(abs(e["cantidad"]) - q) / q
            if dd <= dias and dif <= tolerancia:
                candidatos.append((dif + dd / 100, i, j))
    usados = set()
    for n, (_, i, j) in enumerate(sorted(candidatos)):
        if i in usados or j in usados:
            continue
        usados |= {i, j}
        ident = f"T{n:04d}"
        ops.loc[i, ["pareja", "id_traspaso"]] = [ops.at[j, "cuenta_pp"], ident]
        ops.loc[j, ["pareja", "id_traspaso"]] = [ops.at[i, "cuenta_pp"], ident]
    return ops


# Motivo de las entradas/salidas sin pareja, según la nota (castellano, catalán o inglés).
CLASES = [
    ("comision", r"fee|comisi|commiss"),
    ("ingreso_en_especie", r"interest|inter[eé]s|staking|airdrop|reward|recompens"),
    ("perdida", r"delist|bancarrota|fallida|p[eé]rdida|hack"),
    ("ajuste_manual", r"ficticia|fict[ií]cia|ajust"),
    ("polvo", r"residu|remanent|dust|resto"),
]


def clasificar_sueltos(ops: pd.DataFrame, umbral_comision: float = 1.0) -> pd.DataFrame:
    """Añade `clase` a las entradas/salidas sin pareja: comisión, ingreso en especie, pérdida,
    ajuste manual, polvo o sin_explicar. Las salidas sin nota de menos de `umbral_comision` €
    se dan por comisiones de red o retirada."""
    ops = ops.copy()
    ops["clase"] = ""
    m = ops["tipo"].isin(ENTRADAS + SALIDAS) & (ops.get("pareja", "") == "")
    for i in ops.index[m]:
        nota = str(ops.at[i, "nota"]).lower()
        clase = next((c for c, pat in CLASES if re.search(pat, nota)), "")
        if not clase:
            pequeno = abs(ops.at[i, "importe"] or 0) < umbral_comision
            clase = "comision" if (ops.at[i, "tipo"] in SALIDAS and pequeno) else "sin_explicar"
        ops.at[i, "clase"] = clase
    return ops
