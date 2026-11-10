"""Clasificación de movimientos (DECISIONES.md §2–§9).

Función pura: misma entrada → misma salida. No lee ni escribe nada; el pipeline le
pasa los movimientos raw, las reglas, los ajustes manuales y la configuración.
"""
from __future__ import annotations

import os
import re

import pandas as pd

from .modelo import COLUMNAS_MOV

ESENCIAL = {
    "Vivienda", "Suministros y telecom", "Supermercado y alimentación", "Seguros",
    "Salud y cuidado personal", "Transporte", "Impuestos y trámites", "Comisiones bancarias",
    "Hogar: equipamiento y mantenimiento", "Efectivo",
}
# Categorías antiguas → (categoría, subcategoría). Se aplican a todo (reglas de la hoja, ajustes e histórico),
# así que no hace falta reescribir nada en la hoja ni en los ficheros revisados.
CATEGORIAS_RENOMBRADAS = {"Vivienda: alquiler": ("Vivienda", "Alquiler")}


def normalizar_categorias(df: pd.DataFrame) -> pd.DataFrame:
    """Pasa las categorías antiguas a la nueva forma; la subcategoría solo se rellena si estaba vacía."""
    if df.empty or "categoria" not in df.columns:
        return df
    df = df.copy()
    for vieja, (cat, sub) in CATEGORIAS_RENOMBRADAS.items():
        m = df["categoria"].astype(str).eq(vieja)
        if not m.any():
            continue
        df.loc[m, "categoria"] = cat
        if "subcategoria" in df.columns:
            vacia = m & df["subcategoria"].fillna("").astype(str).str.strip().isin(["", "nan"])
            df.loc[vacia, "subcategoria"] = sub
    return df


SIN_DETALLE = "Compartido: reposiciones (sin detalle)"  # valor por defecto; ver nombres()


def nombres(cfg: dict) -> tuple[str, str]:
    """Nombres cortos del titular y de la otra persona de la cuenta conjunta (config: nombre_titular / nombre_pareja)."""
    return cfg.get("nombre_titular", "Titular"), cfg.get("nombre_pareja", "Pareja")


def sin_detalle(cfg: dict) -> str:
    yo, otro = nombres(cfg)
    return f"Compartido: reposiciones {yo}/{otro} (sin detalle)"
SIN_CLASIFICAR = "Sin clasificar"


def _norm(s) -> str:
    s = str(s or "").upper()
    for a, b in zip("ÁÉÍÓÚÀÈÒÏÜ", "AEIOUAEOIU"):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def _ibans(cfg: dict, clave: str) -> set[str]:
    return {os.environ.get(v, "").replace(" ", "") for v in cfg.get(clave, []) if os.environ.get(v)}


def clasificar(raw: pd.DataFrame, reglas: pd.DataFrame, ajustes: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    df = raw.copy().reset_index(drop=True)
    df["importe"] = df["importe"].astype(float)
    df["fecha_dt"] = pd.to_datetime(df["fecha"])
    df["txt"] = (df["concepto"].map(_norm) + " | " + df["contraparte"].map(_norm))
    df["iban_c"] = df["iban_contraparte"].fillna("").str.replace(" ", "")
    for c in ("flujo", "categoria", "subcategoria", "tipo_gasto", "regla", "nota"):
        df[c] = ""
    pct = {c["id"]: float(c.get("pct_titular", 1.0)) for c in cfg["cuentas"]}
    yo, otro = nombres(cfg)
    SIN_DET = sin_detalle(cfg)
    df["pct_imputado"] = df["cuenta"].map(pct).fillna(1.0)

    al_titular = "|".join(re.escape(_norm(a)) for a in cfg.get("alias_titular", [cfg["titular"]]))
    al_pareja = "|".join(re.escape(_norm(a)) for a in cfg.get("alias_pareja", [cfg["pareja"]]))
    propios = _ibans(cfg, "ibans_env")
    inversion = _ibans(cfg, "ibans_inversion_env")
    conj = df["cuenta"].eq("REV_CONJ")
    bizum = df["txt"].str.contains("BIZUM")
    libre = lambda: df["flujo"].eq("")  # noqa: E731

    def poner(m, flujo, cat="", sub="", tipo="", regla="", nota=""):
        m = m & libre()
        df.loc[m, ["flujo", "categoria", "subcategoria", "tipo_gasto", "regla"]] = [flujo, cat, sub, tipo, regla]
        if nota:
            df.loc[m, "nota"] = nota

    # 1. Mes imputado: la nómina de los días 1–5 cuenta para el mes anterior.
    df["mes"] = df["fecha_dt"].dt.to_period("M").astype(str)
    nom = df["txt"].str.contains("NOMINA") & (df["fecha_dt"].dt.day <= 5)
    df.loc[nom, "mes"] = (df.loc[nom, "fecha_dt"] - pd.Timedelta(days=6)).dt.to_period("M").astype(str)

    # 1b. Trade Republic por Enable Banking: llega sin comercio. Provisional hasta que el CSV cubra ese día.
    tr_prov = df["cuenta"].eq("TR") & df["fuente"].eq("enable_banking")
    poner(tr_prov & (df["importe"] < 0), "Gasto", "Pendiente de detalle (Trade Republic)", tipo="Variable", regla="tr_provisional")
    poner(tr_prov & (df["importe"] >= 0), "Pendiente de detalle", "Pendiente de detalle (Trade Republic)", regla="tr_provisional")

    # 2. Traspasos e inversión por IBAN de la contraparte.
    poner(df["iban_c"].isin(inversion) & df["iban_c"].ne(""), "Ahorro / inversión", "Inversión", regla="iban_inversion")
    a_titular = conj & (df["importe"] < 0) & (df["txt"].str.contains(al_titular) | df["iban_c"].isin(propios - {os.environ.get("IBAN_REV_CONJ", "")}))
    a_pareja = conj & (df["importe"] < 0) & df["txt"].str.contains(al_pareja) & ~bizum
    reposiciones = df.index[(a_titular | a_pareja) & libre()]
    poner(a_titular, "Gasto", SIN_DET, tipo="Variable", regla="reposicion_titular")
    poner(a_pareja, "Gasto", SIN_DET, tipo="Variable", regla="reposicion_pareja")
    poner(conj & (df["importe"] > 0) & df["txt"].str.contains(al_titular),
          "Traspaso interno", f"Aportación {yo} a conjunta", regla="aportacion_conjunta")
    poner(conj & (df["importe"] > 0) & df["txt"].str.contains(al_pareja),
          "Traspaso interno", f"Aportación {otro} a conjunta", regla="aportacion_conjunta")
    poner(df["iban_c"].isin(propios) & df["iban_c"].ne(""), "Traspaso interno", "Traspaso entre cuentas propias", regla="iban_propio")
    poner(~conj & ~bizum & df["txt"].str.contains("TRASPASO"), "Traspaso interno", "Traspaso entre cuentas propias", regla="traspaso")
    poner(~conj & ~bizum & df["txt"].str.contains(al_titular) & df["txt"].str.contains("TRANSFER"),
          "Traspaso interno", "Traspaso entre cuentas propias", regla="transferencia_propia")

    # 3. Reglas por comercio (prioridad ascendente; la primera que encaja gana).
    for _, r in reglas.sort_values("prioridad").iterrows():
        patron = re.sub(r"\((?!\?)", "(?:", str(r["patron"]))  # grupos sin captura: evita avisos de pandas
        # Se prueba contra «concepto | contraparte» y contra el concepto solo, para que ^…$ funcione como se espera.
        m = df["txt"].str.contains(patron, regex=True, na=False) | df["txt"].str.split(" | ", regex=False).str[0].str.contains(patron, regex=True, na=False)
        if isinstance(r.get("cuenta"), str) and r["cuenta"]:
            m &= df["cuenta"].eq(r["cuenta"])
        m &= ~bizum  # los Bizum se resuelven en los pasos 6–8
        poner(m, r["flujo"], r["categoria"], r.get("subcategoria") or "", r.get("tipo_gasto") or "",
              regla=f"regla:{r['patron'][:40]}", nota=r.get("nota") or "")

    # 4. Coverflex sin regla específica: por tipo de tarjeta.
    cov = df["cuenta"].eq("COVERFLEX")
    poner(cov & df["tipo_origen"].eq("Comida"), "Gasto", "Restauración y delivery", "Restaurantes y cenas", "Variable", "coverflex")
    poner(cov & df["tipo_origen"].eq("Transporte"), "Gasto", "Transporte", "", "Variable", "coverflex")

    # 5. Reposiciones: buscar el pago original del titular (DECISIONES §3).
    usados = set()
    for i in reposiciones:
        imp, f = -df.at[i, "importe"], df.at[i, "fecha_dt"]
        dias = (f - df["fecha_dt"]).dt.days
        cand = df[~conj & (df["importe"].sub(-imp).abs() < 0.011) & dias.between(-3, 7)
                  & df["flujo"].isin(["", "Gasto"]) & ~df.index.isin(usados) & ~bizum]
        if len(cand):
            j = (f - cand["fecha_dt"]).abs().idxmin()
            usados.add(j)
            if df.at[j, "flujo"] == "":
                poner(df.index == j, "Gasto", SIN_CLASIFICAR, tipo="Variable", regla="pendiente")
            df.at[j, "pct_imputado"] = 0.0
            df.at[j, "nota"] = f"Pagado por {yo}, repuesto desde la conjunta (50 % vía conjunta)"
            df.loc[i, ["categoria", "subcategoria"]] = df.loc[j, ["categoria", "subcategoria"]].values
            df.at[i, "regla"] = "reposicion_emparejada"
            df.at[i, "nota"] = f"Reposición de {df.at[j, 'concepto']}"
        else:
            # Sin pago original localizable: Revolut incluye la nota de la transferencia
            # («Bon preu To <pareja>…»), así que se clasifica por esa nota con las mismas reglas.
            nota = re.split(r"\bTO\b", df.at[i, "txt"].split(" | ")[0])[0].strip()
            for _, r in reglas[reglas["flujo"].eq("Gasto")].sort_values("prioridad").iterrows():
                if nota and (re.search(str(r["patron"]), nota) or re.search(str(r["patron"]), nota.replace(" ", ""))):
                    df.loc[i, ["categoria", "subcategoria", "tipo_gasto"]] = [r["categoria"], r.get("subcategoria") or "", r.get("tipo_gasto") or "Variable"]
                    df.at[i, "regla"] = "reposicion_por_nota"
                    df.at[i, "nota"] = f"Reposición: {nota.title()}"
                    break

    # 6. Compras de Amazon compensadas por las personas de `compensan_compras` (±3 días, ±5 %).
    quien = "|".join(map(re.escape, cfg.get("compensan_compras", [])))
    biz_in = df[bizum & (df["importe"] > 0) & libre() & df["txt"].str.contains(quien)] if quien else df.iloc[0:0]
    amz = df[~conj & df["txt"].str.contains("AMAZON|AMZN") & (df["importe"] < 0) & (df["pct_imputado"] > 0)]
    pares = []
    for i, c in amz.iterrows():
        for k, b in biz_in.iterrows():
            dif, dd = abs(b["importe"] + c["importe"]), abs((b["fecha_dt"] - c["fecha_dt"]).days)
            if dd <= 3 and dif <= max(0.5, 0.05 * -c["importe"]):
                pares.append((dif + dd / 100, i, k))
    hecho_c, hecho_b = set(), set()
    for _, i, k in sorted(pares):
        if i in hecho_c or k in hecho_b:
            continue
        hecho_c.add(i); hecho_b.add(k)
        df.at[i, "pct_imputado"] = 0.0
        df.at[i, "nota"] = f"Compensada por {df.at[k, 'concepto']}"
        poner(df.index == k, "Compensación de compra", "Compensación de compra (pedido para otro)", regla="compensacion_compra")

    # 7. Comidas pagadas por todos: Bizums de amigos entre −1 y +4 días restan del gasto.
    fam = "|".join(map(re.escape, cfg.get("familia", []))) or "^$"
    comidas = df[df["categoria"].eq("Restauración y delivery") & (df["importe"] <= -20) & (df["pct_imputado"] > 0)]
    for i, c in comidas.sort_values("fecha_dt").iterrows():
        dd = (df["fecha_dt"] - c["fecha_dt"]).dt.days
        b = df[bizum & (df["importe"] > 0) & libre() & dd.between(-1, 4) & ~df["txt"].str.contains(fam)].sort_values("fecha_dt")
        total = 0.0
        for k, r in b.iterrows():
            if total + r["importe"] > -c["importe"] + 0.01:
                continue
            total += r["importe"]
            poner(df.index == k, "Gasto", "Restauración y delivery", c["subcategoria"], "Variable", "compensacion_comida",
                  f"Compensa su parte de {c['concepto']} ({-c['importe']:.2f} €)")
        if total:
            df.at[i, "nota"] = f"Pagado por todos; compensado {total:.2f} € por Bizum"

    # 8. Resto de Bizums y lo que no encaja en ninguna regla.
    poner(bizum, "Bizum personas", "Bizum con personas", regla="bizum")
    poner(df["importe"] > 0, "Ingreso", SIN_CLASIFICAR, regla="pendiente")
    poner(df["importe"] <= 0, "Gasto", SIN_CLASIFICAR, tipo="Variable", regla="pendiente")
    df.loc[df["flujo"].eq("Gasto") & df["tipo_gasto"].eq(""), "tipo_gasto"] = "Variable"

    # 9. Ajustes manuales por tx_id: siempre mandan (DECISIONES §10).
    if ajustes is not None and not ajustes.empty:
        for _, a in ajustes.iterrows():
            m = df["tx_id"].eq(a["tx_id"])
            for col in ("flujo", "categoria", "subcategoria", "tipo_gasto", "nota"):
                if isinstance(a.get(col), str) and a[col]:
                    df.loc[m, col] = a[col]
            if pd.notna(a.get("pct_imputado")) and str(a.get("pct_imputado")) != "":
                df.loc[m, "pct_imputado"] = float(str(a["pct_imputado"]).replace(",", "."))
            df.loc[m, "regla"] = "ajuste_manual"

    df = normalizar_categorias(df)
    df["naturaleza"] = ""
    g = df["flujo"].eq("Gasto")
    df.loc[g, "naturaleza"] = df.loc[g, "categoria"].map(lambda c: "Esencial" if c in ESENCIAL else "Discrecional")
    df.loc[g & df["tipo_gasto"].eq("Extraordinario"), "naturaleza"] = "Extraordinario"
    df["importe_imputado"] = (df["importe"] * df["pct_imputado"]).round(2)
    df["comercio"] = [(c if str(c).strip() else t).strip().title()[:40] for c, t in zip(df["contraparte"].fillna(""), df["concepto"].fillna(""))]
    return df[COLUMNAS_MOV].sort_values(["fecha", "cuenta", "tx_id"]).reset_index(drop=True)


def pendientes(mov: pd.DataFrame) -> pd.DataFrame:
    """Lo que el motor no ha sabido clasificar: para revisar a mano."""
    # Las reposiciones de la conjunta sin nota ya tienen categoría (gasto común al 50 %): no son pendientes.
    return mov[mov["categoria"].eq(SIN_CLASIFICAR) & ~mov["regla"].eq("tr_provisional")][
        ["tx_id", "fecha", "cuenta", "concepto", "contraparte", "importe", "categoria", "nota"]]
