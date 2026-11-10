"""Dashboard de finanzas personales (Streamlit).

Lee la hoja de Google Sheets que escribe el pipeline, con la misma cuenta de servicio.
Secretos (Streamlit Cloud → Settings → Secrets, o .streamlit/secrets.toml en local):
    GOOGLE_SERVICE_ACCOUNT_JSON = '''{ ... }'''
    SHEET_ID = "..."
Ejecutar en local:  streamlit run dashboard/app.py

Modo demo (datos inventados de demo/datos, sin hoja ni secretos): MODO_DEMO = true en los secretos,
la variable de entorno FINANZAS_DEMO=1, o simplemente no tener SHEET_ID configurado.
"""
from __future__ import annotations

import json
import os
import re

import gspread
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from google.oauth2.service_account import Credentials

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
# Streamlit solo recarga el código de la carpeta del dashboard: si cambia algo de src/finanzas (p. ej. tras un push),
# el proceso seguiría usando la versión vieja ya importada. Si cambió algún fichero, se vuelven a importar.
_firma = tuple(sorted((str(f), f.stat().st_mtime) for f in (Path(__file__).resolve().parents[1] / "src" / "finanzas").rglob("*.py")))
if getattr(sys, "_finanzas_firma", None) != _firma:
    for _m in [m for m in sys.modules if m == "finanzas" or m.startswith("finanzas.")]:
        del sys.modules[_m]
    sys._finanzas_firma = _firma
from finanzas.fiscal import cuota_ahorro, simular_venta  # noqa: E402
from finanzas.reglas import normalizar_categorias  # noqa: E402
from finanzas import rentabilidad as RENT  # noqa: E402

st.set_page_config(page_title="Finanzas personales", page_icon="💶", layout="wide")


def _secreto(clave: str, defecto=None):
    try:
        return st.secrets.get(clave, defecto)
    except Exception:  # sin fichero de secretos (p. ej. la demo en local)
        return defecto


RAIZ = Path(__file__).resolve().parents[1]
DEMO = str(os.environ.get("FINANZAS_DEMO", "")).lower() in ("1", "true", "si", "sí") \
    or str(_secreto("MODO_DEMO", "")).lower() in ("1", "true", "si", "sí") or not _secreto("SHEET_ID")
DIR_CONFIG = RAIZ / ("demo/config" if DEMO else "config")

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
# Paleta apagada (menos saturada) que funciona en modo claro y oscuro.
C = {"azul": "#6C8EBF", "ambar": "#D4A85A", "verde": "#7FAE8E", "rojo": "#C27C88", "violeta": "#9C8FC4",
     "cian": "#6FAAB5", "arena": "#B8A27A", "gris": "#8C95A3"}
PALETA = list(C.values())
# Subcategorías de una misma categoría: tonos de un solo color (de oscuro a claro).
TONOS_VERDE = ["#4F7D63", "#7FAE8E", "#A9CBB3", "#D2E5D7"]
OSCURO = (st.context.theme.type or "dark") == "dark"
TXT, REJILLA, LINEA, TOOLTIP_BG = (("#C9CED6", "#2A2F37", "#3A404A", "#1C2027") if OSCURO else ("#374151", "#EEF0F3", "#E5E7EB", "#FFFFFF"))
DIAS_AVISO = 35  # un fichero manual sin datos nuevos en más de estos días genera aviso

st.markdown("""
<style>
.block-container {padding-top: 2rem;}
div[data-testid="stMetricLabel"] p {font-size: 0.85rem; opacity: 0.75;}
h1 {font-weight: 700; letter-spacing: -0.02em;}
h3 {font-weight: 600; margin-top: 0.5rem;}
</style>""", unsafe_allow_html=True)


# ------------------------------------------------------------------ formato
def eur(v, dec: int = 0) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    s = f"{abs(v):,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'−' if v < 0 else ''}{s} €"


def eur_delta(v, dec: int = 0) -> str:
    """Para el «delta» de los KPI: Streamlit decide verde/rojo por el signo ASCII inicial."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    s = f"{abs(v):,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if v < 0 else '+'}{s} €"


def pct(v, dec: int = 1) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return f"{v * 100:.{dec}f} %".replace(".", ",")


def mes_lab(m: str) -> str:
    return f"{MESES[int(m[5:7]) - 1]} {m[2:4]}"


def num(s: pd.Series) -> pd.Series:
    def c(v):
        if isinstance(v, (int, float)):
            return float(v)
        t = str(v).strip().replace("€", "").replace(" ", "")
        if not t:
            return np.nan
        if "," in t:
            t = t.replace(".", "").replace(",", ".")
        try:
            return float(t)
        except ValueError:
            return np.nan
    return s.map(c)


def estilo(fig: go.Figure, alto: int = 360, euros_en: str = "y") -> go.Figure:
    """Estilo común: formato español, tooltips limpios, sin rejilla innecesaria."""
    _ = fig.update_layout(
        height=alto, margin=dict(t=36, b=10, l=10, r=10), separators=",.",
        font=dict(family="Inter, sans-serif", size=13, color=TXT),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0, title=None),  # arriba: no se solapa con el eje
        hoverlabel=dict(bgcolor=TOOLTIP_BG, bordercolor=LINEA, font=dict(family="Inter, sans-serif", size=13, color=TXT)),
        hovermode="x unified" if euros_en == "y" else "y unified",
    )
    eje = dict(ticksuffix=" €", tickformat=",.0f", gridcolor=REJILLA, zeroline=False)
    if euros_en == "y":
        _ = fig.update_yaxes(**eje)
        _ = fig.update_xaxes(showgrid=False, linecolor=LINEA)
    else:
        _ = fig.update_xaxes(**eje)
        _ = fig.update_yaxes(showgrid=False)
    return fig


EURO_COL = st.column_config.NumberColumn(format="localized")


def tabla(df: pd.DataFrame, euros: list[str] = (), pcts: list[str] = (), **kw):
    cfg = {c: st.column_config.NumberColumn(f"{c} (€)", format="localized") for c in euros}
    cfg |= {c: st.column_config.NumberColumn(c, format="percent") for c in pcts}
    st.dataframe(df, column_config=cfg, hide_index=kw.pop("hide_index", True), width="stretch", **kw)


def xirr(flujos: list[tuple[pd.Timestamp, float]]) -> float:
    """TIR anual de una serie de flujos (negativo = dinero que metes, positivo = lo que sacas o vale hoy)."""
    flujos = [(pd.Timestamp(f), float(v)) for f, v in flujos if pd.notna(f) and v]
    if len(flujos) < 2 or not (any(v < 0 for _, v in flujos) and any(v > 0 for _, v in flujos)):
        return np.nan
    t0 = min(f for f, _ in flujos)
    anos = np.array([(f - t0).days / 365.25 for f, _ in flujos])
    v = np.array([x for _, x in flujos])
    vpn = lambda r: float(np.sum(v / (1 + r) ** anos))  # noqa: E731
    lo, hi = -0.99, 10.0
    if vpn(lo) * vpn(hi) > 0:
        return np.nan
    for _ in range(200):
        mid = (lo + hi) / 2
        if vpn(lo) * vpn(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def sankey(ingresos: dict, gastos: pd.Series, invertido: float, alto: int = 460, max_cat: int = 10) -> tuple[go.Figure, list]:
    """Flujo de caja: fuentes de ingresos → Ingresos → esencial / discrecional → categorías, inversión y lo que queda."""
    ingresos = {k: v for k, v in ingresos.items() if v > 0.5}
    total_in = sum(ingresos.values())
    g = gastos[gastos > 0.5].sort_values(ascending=False)
    if len(g) > max_cat:
        g = pd.concat([g.iloc[:max_cat], pd.Series({"Otras categorías": g.iloc[max_cat:].sum()})])
    ess = {"Vivienda", "Suministros y telecom", "Supermercado y alimentación", "Seguros", "Salud y cuidado personal",
           "Transporte", "Impuestos y trámites", "Comisiones bancarias", "Hogar: equipamiento y mantenimiento", "Efectivo"}
    gasto_ess = sum(v for k, v in g.items() if k in ess)
    gasto_dis = g.sum() - gasto_ess
    resto = total_in - g.sum() - max(invertido, 0)
    nodos, src, dst, val, col_n = [], [], [], [], []

    def nodo(n, c):
        if n not in nodos:
            nodos.append(n); col_n.append(c)
        return nodos.index(n)

    def enlace(a, b, v):
        if v > 0.5:
            src.append(a); dst.append(b); val.append(v)

    centro = nodo("Ingresos", C["verde"])
    for k, v in ingresos.items():
        enlace(nodo(k, C["verde"]), centro, v)
    if resto < 0:  # gastas más de lo que entra: sale de los ahorros
        enlace(nodo("De tus ahorros", C["rojo"]), centro, -resto)
    n_ess, n_dis = nodo("Gasto esencial", C["azul"]), nodo("Gasto discrecional", C["arena"])
    enlace(centro, n_ess, gasto_ess)
    enlace(centro, n_dis, gasto_dis)
    for k, v in g.items():
        enlace(n_ess if k in ess else n_dis, nodo(k, C["azul"] if k in ess else C["arena"]), v)
    enlace(centro, nodo("Inversión", C["violeta"]), max(invertido, 0))
    if resto > 0:
        enlace(centro, nodo("Queda en cuentas", C["cian"]), resto)
    fig = go.Figure(go.Sankey(
        arrangement="snap", valueformat=",.0f", valuesuffix=" €",
        node=dict(label=[f"{n}" for n in nodos], color=col_n, pad=14, thickness=14, line=dict(width=0)),
        link=dict(source=src, target=dst, value=val, color="rgba(140,149,163,0.25)",
                  hovertemplate="%{source.label} → %{target.label}: %{value:,.0f} €<extra></extra>")))
    fig.update_layout(height=alto, margin=dict(t=10, b=10, l=10, r=10), separators=",.",
                      font=dict(family="Inter, sans-serif", size=12, color=TXT), paper_bgcolor="rgba(0,0,0,0)")
    enlaces = [(nodos[a], nodos[b], v) for a, b, v in zip(src, dst, val)]
    fig._nodos, fig._colores = nodos, col_n  # para la versión interactiva
    return fig, enlaces


def movimientos_flujo(mov: pd.DataFrame, origen: str, destino: str, otras: list[str]) -> pd.DataFrame:
    """Movimientos que forman un enlace del Sankey (mov = movimientos del periodo o del mes)."""
    g = mov[(mov["flujo"] == "Gasto") & (mov["tipo_gasto"] != "Extraordinario")]
    if destino == "Ingresos":
        if origen == "Nómina":
            return mov[(mov["flujo"] == "Ingreso") & (mov["categoria"] == "Nómina")]
        if origen == "Coverflex":
            return mov[mov["cuenta"] == "COVERFLEX"]
        if origen == "Otros ingresos":
            return mov[(mov["flujo"] == "Ingreso") & (mov["categoria"] != "Nómina") & (mov["tipo_gasto"] != "Extraordinario")]
        return mov.iloc[0:0]
    if origen == "Ingresos":
        if destino == "Gasto esencial":
            return g[g["naturaleza"] == "Esencial"]
        if destino == "Gasto discrecional":
            return g[g["naturaleza"] == "Discrecional"]
        if destino == "Inversión":
            return mov[mov["flujo"] == "Ahorro / inversión"]
        return mov.iloc[0:0]
    if destino == "Otras categorías":
        return g[g["categoria"].isin(otras)]
    return g[g["categoria"] == destino]


HTML_SANKEY = """
<div id="wrap" style="font-family:Inter,system-ui,sans-serif;color:__TXT__">
  <svg id="sk" width="100%" height="__ALTO__"></svg>
  <div id="tip" style="position:fixed;pointer-events:none;display:none;background:__BG__;border:1px solid __LINEA__;border-radius:8px;
       padding:8px 10px;font-size:12px;max-width:420px;box-shadow:0 4px 16px rgba(0,0,0,.35);z-index:10"></div>
  <div id="cab" style="font-size:13px;margin:6px 2px;opacity:.8">Pasa el ratón por un tramo para un resumen; pincha para ver todos sus movimientos aquí debajo.</div>
  <div id="tabla" style="max-height:300px;overflow:auto;border:1px solid __LINEA__;border-radius:8px;display:none"></div>
</div>
<script>__D3__</script>
<script>__D3SANKEY__</script>
<script>
function dibujar() {
try {
const D = __DATOS__;
const fmt = new Intl.NumberFormat('es-ES', {maximumFractionDigits: 0});
const fmt2 = new Intl.NumberFormat('es-ES', {minimumFractionDigits: 2, maximumFractionDigits: 2});
const svg = d3.select('#sk'), H = __ALTO__;
svg.selectAll('*').remove();
const W = Math.max(document.getElementById('wrap').clientWidth || 0, document.documentElement.clientWidth || 0, window.innerWidth || 0, 600);
svg.attr('viewBox', [0, 0, W, H]);
const sk = d3.sankey().nodeWidth(12).nodePadding(12).nodeAlign(d3.sankeyJustify).extent([[4, 8], [W - 4, H - 8]]);
const g = sk({nodes: D.nodos.map(d => Object.assign({}, d)), links: D.enlaces.map(d => Object.assign({}, d))});
const tip = d3.select('#tip');
function filas(l) { return D.movs[l.k] || []; }
function resumen(titulo, valor, ms) {
  let h = `<b>${titulo}</b><br>${fmt.format(valor)} €${D.media ? ' al mes' : ''} · ${ms.length} movimientos`;
  if (ms.length) {
    h += '<table style="margin-top:6px;border-collapse:collapse">';
    ms.slice().sort((a, b) => b.o.localeCompare(a.o)).slice(0, 6).forEach(m =>
      h += `<tr><td style="padding:1px 8px 1px 0;opacity:.7">${m.f}</td><td style="padding:1px 8px 1px 0">${esc(m.c).slice(0, 38)}</td><td style="text-align:right">${fmt2.format(m.i)} €</td></tr>`);
    h += '</table>' + (ms.length > 6 ? `<div style="opacity:.7;margin-top:4px">Los 6 más recientes; ${ms.length - 6} más al pinchar</div>` : '');
  }
  return h;
}
function esc(t) { const d = document.createElement('div'); d.textContent = t || ''; return d.innerHTML; }
function mostrar(titulo, ms) {
  const cab = document.getElementById('cab'), tb = document.getElementById('tabla');
  const total = ms.reduce((a, m) => a + m.i, 0);
  cab.innerHTML = `<b>${esc(titulo)}</b> · ${ms.length} movimientos · ${fmt2.format(total)} € en total`;
  if (!ms.length) { tb.style.display = 'none'; return; }
  let h = '<table style="width:100%;border-collapse:collapse;font-size:12px"><thead><tr>' +
    ['Fecha', 'Cuenta', 'Concepto', 'Categoría', 'Subcategoría', 'Importe (€)'].map((c, i) =>
      `<th style="position:sticky;top:0;background:__BG__;text-align:${i == 5 ? 'right' : 'left'};padding:6px 8px;border-bottom:1px solid __LINEA__">${c}</th>`).join('') + '</tr></thead><tbody>';
  ms.slice().sort((a, b) => b.o.localeCompare(a.o)).forEach(m =>
    h += `<tr><td style="padding:4px 8px;white-space:nowrap">${m.f}</td><td style="padding:4px 8px">${esc(m.a)}</td><td style="padding:4px 8px">${esc(m.c)}</td>` +
         `<td style="padding:4px 8px">${esc(m.g)}</td><td style="padding:4px 8px">${esc(m.s)}</td><td style="padding:4px 8px;text-align:right">${fmt2.format(m.i)}</td></tr>`);
  tb.innerHTML = h + '</tbody></table>';
  tb.style.display = 'block';
}
const mover = e => tip.style('left', Math.min(e.clientX + 14, window.innerWidth - 430) + 'px').style('top', (e.clientY + 14) + 'px');
svg.append('g').attr('fill', 'none').selectAll('path').data(g.links).join('path')
  .attr('d', d3.sankeyLinkHorizontal()).attr('stroke', '__ENLACE__').attr('stroke-width', d => Math.max(1, d.width))
  .style('cursor', 'pointer')
  .on('mouseover', function (e, l) { d3.select(this).attr('stroke', '__ENLACE_ON__'); tip.style('display', 'block').html(resumen(esc(`${l.source.name} → ${l.target.name}`), l.value, filas(l))); mover(e); })
  .on('mousemove', mover)
  .on('mouseout', function () { d3.select(this).attr('stroke', '__ENLACE__'); tip.style('display', 'none'); })
  .on('click', (e, l) => mostrar(`${l.source.name} → ${l.target.name}`, filas(l)));
const nd = svg.append('g').selectAll('g').data(g.nodes).join('g').style('cursor', 'pointer');
nd.append('rect').attr('x', d => d.x0).attr('y', d => d.y0).attr('height', d => Math.max(1, d.y1 - d.y0)).attr('width', d => d.x1 - d.x0).attr('fill', d => d.color);
nd.append('text').attr('x', d => d.x0 < W / 2 ? d.x1 + 6 : d.x0 - 6).attr('y', d => (d.y1 + d.y0) / 2).attr('dy', '0.35em')
  .attr('text-anchor', d => d.x0 < W / 2 ? 'start' : 'end').attr('fill', '__TXT__').style('font-size', '12px').text(d => d.name);
const delNodo = n => { const ls = n.targetLinks.length ? n.targetLinks : n.sourceLinks; return ls.flatMap(filas); };
nd.on('mouseover', (e, n) => { tip.style('display', 'block').html(resumen(esc(n.name), n.value, delNodo(n))); mover(e); })
  .on('mousemove', mover).on('mouseout', () => tip.style('display', 'none'))
  .on('click', (e, n) => mostrar(n.name, delNodo(n)));
} catch (err) { document.getElementById('cab').textContent = 'No se ha podido dibujar el diagrama: ' + err; }
}
if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', dibujar); } else { dibujar(); }
let _t; window.addEventListener('resize', () => { clearTimeout(_t); _t = setTimeout(dibujar, 200); });
</script>
"""


@st.cache_resource
def _libreria(nombre: str) -> str:
    """D3 y d3-sankey van incluidos en el repo (dashboard/vendor) y dentro del HTML: no dependen de ningún CDN."""
    return (Path(__file__).resolve().parent / "vendor" / nombre).read_text(encoding="utf-8")


def sankey_interactivo(fig: go.Figure, enlaces: list, mov: pd.DataFrame, gastos: pd.Series, media: bool, alto: int = 460):
    """Sankey en D3 con tooltip (resumen y principales movimientos) y, al pinchar un tramo o un nodo,
    la tabla con todos sus movimientos debajo. Los importes de la tabla son tu parte."""
    import json
    top = gastos[gastos > 0.5].sort_values(ascending=False)
    otras = list(top.index[10:])
    nodos = [{"name": n, "color": c} for n, c in zip(fig._nodos, fig._colores)]
    idx = {n: i for i, n in enumerate(fig._nodos)}
    datos_enl, movs = [], {}
    for k, (a, b, v) in enumerate(enlaces):
        datos_enl.append({"source": idx[a], "target": idx[b], "value": round(float(v), 2), "k": str(k)})
        t = movimientos_flujo(mov, a, b, otras) if not (b == "Queda en cuentas" or a == "De tus ahorros") else mov.iloc[0:0]
        imp = t["importe"].abs() if b == "Ingresos" else (-t["importe_imputado"] if b != "Inversión" else -t["importe"])
        movs[str(k)] = [{"f": pd.to_datetime(f).strftime("%d/%m/%Y"), "o": str(f), "a": cu, "c": co, "g": ca, "s": su, "i": round(float(i), 2)}
                        for f, cu, co, ca, su, i in zip(t["fecha"], t["cuenta"], t["concepto"], t["categoria"], t["subcategoria"], imp)]
    # «</» escapado para que ningún concepto pueda cerrar la etiqueta <script> (los textos se insertan escapados).
    datos = json.dumps({"nodos": nodos, "enlaces": datos_enl, "movs": movs, "media": media}, ensure_ascii=False).replace("</", "<\\/")
    html = (HTML_SANKEY.replace("__D3__", _libreria("d3.min.js")).replace("__D3SANKEY__", _libreria("d3-sankey.min.js"))
            .replace("__DATOS__", datos).replace("__ALTO__", str(alto)).replace("__TXT__", TXT)
            .replace("__BG__", TOOLTIP_BG).replace("__LINEA__", LINEA)
            .replace("__ENLACE__", "rgba(140,149,163,0.25)").replace("__ENLACE_ON__", "rgba(140,149,163,0.55)"))
    st.iframe(html, height=alto + 360)


_dataframe_original = st.dataframe


def _dataframe_2dec(data=None, *args, column_config=None, **kwargs):
    """Todas las tablas: importes con 2 decimales como máximo y porcentajes como «45,03» (sin decimales raros).
    Las columnas de cantidades (formato %.6f) no se tocan."""
    if isinstance(data, pd.DataFrame):
        data = data.copy()
        cfg = dict(column_config or {})
        for col in data.columns:
            c = cfg.get(col)
            fmt = c.get("type_config", {}).get("format") if isinstance(c, dict) else None
            tipo = c.get("type_config", {}).get("type") if isinstance(c, dict) else None
            if not pd.api.types.is_numeric_dtype(data[col]) or tipo == "progress" or (fmt and fmt.startswith("%.6")):
                continue
            if fmt == "percent":
                data[col] = (data[col] * 100).round(2)
                etiqueta = c.get("label") or col
                cfg[col] = st.column_config.NumberColumn(f"{etiqueta} (%)", format="localized", help=c.get("help"))
            else:
                data[col] = data[col].round(2)
        column_config = cfg
    return _dataframe_original(data, *args, column_config=column_config, **kwargs)


st.dataframe = _dataframe_2dec
_plotly_chart_original = st.plotly_chart


def _plotly_chart_sin_barra(fig, *args, config=None, **kwargs):
    """Sin la barra de herramientas de Plotly: en el móvil se queda fija encima de la leyenda.
    Streamlit ya pone su propio botón de pantalla completa en cada gráfico."""
    return _plotly_chart_original(fig, *args, config={"displayModeBar": False, **(config or {})}, **kwargs)


st.plotly_chart = _plotly_chart_sin_barra


# ------------------------------------------------------------------ datos
@st.cache_resource
def _hoja():
    info = st.secrets["GOOGLE_SERVICE_ACCOUNT_JSON"]
    info = json.loads(info) if isinstance(info, str) else dict(info)
    # Escritura solo para lo que se edita desde el dashboard (p. ej. el objetivo de reparto).
    cred = Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return gspread.authorize(cred).open_by_key(st.secrets["SHEET_ID"])


def pestana(nombre: str) -> pd.DataFrame:
    """Una pestaña de la hoja como texto (o, en la demo, su CSV; con lo editado en esta sesión si lo hay)."""
    if DEMO:
        editadas = st.session_state.get("_demo_editadas", {})
        if nombre in editadas:
            return editadas[nombre].copy()
        return _pestana_demo(nombre, _demo_al_dia()).copy()
    return _pestana_hoja(nombre)


@st.cache_resource(ttl=6 * 3600, show_spinner="Preparando los datos de la demo…")
def _demo_al_dia() -> str:
    """Regenera los datos inventados si no llegan hasta ayer, para que la demo siempre esté al día."""
    saldos = RAIZ / "demo" / "datos" / "saldos.csv"
    ayer = (pd.Timestamp.today().normalize() - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    if not saldos.exists() or pd.read_csv(saldos)["fecha"].max() < ayer:
        import runpy
        runpy.run_path(str(RAIZ / "demo" / "generar_demo.py"), run_name="__main__")
    return ayer


@st.cache_data
def _pestana_demo(nombre: str, al_dia: str = "") -> pd.DataFrame:
    f = RAIZ / "demo" / "datos" / f"{nombre}.csv"
    return pd.read_csv(f, dtype=str, keep_default_na=False) if f.exists() else pd.DataFrame()


@st.cache_data(ttl=1800, show_spinner="Leyendo la hoja…")
def _pestana_hoja(nombre: str) -> pd.DataFrame:
    try:
        filas = _hoja().worksheet(nombre).get_all_values(value_render_option="UNFORMATTED_VALUE",
                                                         date_time_render_option="FORMATTED_STRING")
    except gspread.WorksheetNotFound:
        return pd.DataFrame()
    if not filas:
        return pd.DataFrame()
    return pd.DataFrame(filas[1:], columns=[str(c).strip() for c in filas[0]])


@st.cache_data
def _costes() -> dict:
    import yaml
    return yaml.safe_load((DIR_CONFIG / "costes.yaml").read_text(encoding="utf-8"))


def comercio_norm(c: str) -> str:
    """«Glovo 16Jan Bluwfhy1» → «Glovo»: quita referencias con cifras y deja como mucho tres palabras."""
    palabras = [w for w in re.split(r"[\s*]+", str(c)) if w and not re.search(r"\d", w)]
    return " ".join(palabras[:3]).strip(" -.,").title() or str(c)[:30]


def valor_futuro(mensual: float, anos: int, rent: float) -> float:
    """Valor de invertir `mensual` € al mes durante `anos` a una rentabilidad anual `rent`."""
    i = (1 + rent) ** (1 / 12) - 1
    n = anos * 12
    return mensual * n if i == 0 else mensual * ((1 + i) ** n - 1) / i


def guardar_pestana(nombre: str, df: pd.DataFrame):
    """Sobrescribe una pestaña pequeña de la hoja con lo editado en el dashboard (en la demo, solo en esta sesión)."""
    if DEMO:
        st.session_state.setdefault("_demo_editadas", {})[nombre] = df.astype(str).reset_index(drop=True)
        st.toast("Demo: el cambio se guarda solo mientras tengas abierta esta página.", icon="ℹ️")
        return
    try:
        ws = _hoja().worksheet(nombre)
    except gspread.WorksheetNotFound:
        ws = _hoja().add_worksheet(nombre, rows=50, cols=max(len(df.columns), 2))
    ws.clear()
    ws.update([list(df.columns)] + df.astype(object).where(df.notna(), "").values.tolist(), value_input_option="RAW")
    st.cache_data.clear()


def unificar_comercios(serie: pd.Series) -> pd.Series:
    """Une variantes del mismo comercio por su primera palabra («Spotify P47D867217», «Spotifyes» → «Spotify»).
    Las palabras genéricas («To», «Transferencia», «Compra»…) usan las tres primeras para no mezclar destinatarios."""
    genericas = {"to", "transferencia", "adeudo", "recibo", "compra", "pago", "bizum", "trf", "transfer", "la", "el", "de"}
    limpio = serie.fillna("").map(comercio_norm)

    def clave(c):
        w = str(c).lower().split()
        return " ".join(w[:3]) if w and w[0] in genericas else (w[0][:7] if w else "—")
    k = limpio.map(clave)
    nombre = limpio.groupby(k).agg(lambda x: min(x.value_counts().index[:1].tolist() + [x.iloc[0]], key=len))
    return k.map(nombre).rename("comercio")


def serie_patrimonio() -> pd.Series:
    """Patrimonio total por fecha: reconstrucción mensual hasta su último mes y, después, las fotos diarias."""
    rec = pestana("patrimonio_historico_reconstruido")
    h = pestana("patrimonio_categorias_historico")
    partes = []
    if not rec.empty:
        partes.append(rec.rename(columns={"mes": "fecha"})[["fecha", "valor"]])
    if not h.empty:
        h_ = h[["fecha", "valor"]]
        if partes:
            h_ = h_[pd.to_datetime(h_["fecha"]) > pd.to_datetime(partes[0]["fecha"]).max()]
        partes.append(h_)
    if not partes:
        return pd.Series(dtype=float)
    t = pd.concat(partes)
    t["valor"], t["fecha"] = num(t["valor"]), pd.to_datetime(t["fecha"], errors="coerce")
    return t.groupby("fecha")["valor"].sum().sort_index()


@st.cache_data
def _cuentas_cfg() -> dict:
    import yaml
    return yaml.safe_load((DIR_CONFIG / "cuentas.yaml").read_text(encoding="utf-8"))


def rentabilidad_por_activo(det: pd.DataFrame, pp: pd.DataFrame, rx: pd.DataFrame, trv: pd.DataFrame) -> pd.DataFrame:
    """Valor, coste y TIR de cada fondo, ETF o cripto. Flujos: compras y ventas de PP (en fondos, los traspasos de entrada
    y salida cuentan como compra y venta a su valor), después las oficiales (suscripciones de MyInvestor, operaciones de
    Kraken y Revolut X) y, en Trade Republic, su CSV."""
    cfg_ = _cuentas_cfg()
    alias = cfg_.get("inversion", {}).get("alias_activos") or {}
    fondos_mi = cfg_.get("inversion", {}).get("myinvestor_fondos", [])
    pp = pp.copy()
    if not pp.empty:
        pp["activo"] = pp["activo"].replace(alias)
        pp["importe"], pp["fecha"] = num(pp["importe"]).abs(), pd.to_datetime(pp["fecha"], errors="coerce")
    ult_pp = pp["fecha"].max() if not pp.empty else pd.Timestamp("1900-01-01")
    d = det[(det["activo"] != "EUR") & (det["valor"].fillna(0) > 0.5)].copy()
    agr = d.groupby(["activo", "categoria"], as_index=False).agg(nombre=("nombre", "first"), valor=("valor", "sum"),
                                                                coste=("coste", lambda x: x.sum(min_count=1)),
                                                                compra=("coste_plataforma", lambda x: num(x).sum(min_count=1)) if "coste_plataforma" in d.columns
                                                                else ("coste", lambda x: x.sum(min_count=1)))
    filas = []
    for _, r in agr.iterrows():
        a, cat = str(r["activo"]), r["categoria"]
        f_ = []
        if cat == "ETF y acciones Trade Republic" and not trv.empty:
            t_ = trv[(trv["isin"] == a) & trv["tipo"].isin(["BUY", "SELL"])]
            f_ = list(zip(pd.to_datetime(t_["fecha"]), np.where(num(t_["cantidad"]) > 0, -num(t_["importe"]), num(t_["importe"]))))
        elif not pp.empty:
            g = pp[pp["activo"] == a]
            if cat == "Cripto":
                g = g[g["tipo"].isin(["compra", "venta"])]
                sig = g["tipo"].map({"compra": -1, "venta": 1})
            else:
                g = g[g["tipo"].isin(["compra", "venta", "traspaso_entrada", "traspaso_salida"])]
                sig = g["tipo"].map({"compra": -1, "traspaso_entrada": -1, "venta": 1, "traspaso_salida": 1})
            f_ = list(zip(g["fecha"], sig * g["importe"]))
            if cat == "Cripto" and not rx.empty:
                o_ = rx[(rx["activo"] == a) & rx["tipo"].isin(["compra", "venta"])].assign(fecha=lambda x: pd.to_datetime(x["fecha"]), importe=lambda x: num(x["importe"]))
                o_ = o_[(o_["fecha"] > ult_pp) & o_["importe"].notna()]
                f_ += list(zip(o_["fecha"], np.where(o_["tipo"] == "compra", -o_["importe"], o_["importe"])))
            if cat == "Fondos MyInvestor":
                patron = next((f["nombre_en_extracto"] for f in fondos_mi if f["isin"] == a), None)
                if patron:
                    sub = m_all[(m_all["cuenta"] == "MYINV") & (m_all["flujo"] == "Ahorro / inversión") & (pd.to_datetime(m_all["fecha"]) > ult_pp)
                                & m_all["concepto"].str.upper().str.contains(patron, regex=True, na=False)]
                    f_ += list(zip(pd.to_datetime(sub["fecha"]), sub["importe"]))
        filas.append({"Inversión": r["nombre"] or a, "Categoría": cat, "Valor actual": r["valor"], "Valor de compra": r["compra"],
                      "Aportación real": r["coste"], "TIR anual": xirr(f_ + [(hoy, r["valor"])]) if f_ else np.nan})
    return pd.DataFrame(filas)


def movimientos() -> pd.DataFrame:
    m = pestana("movimientos").copy()
    if m.empty:
        return m
    for c in ("importe", "importe_imputado", "pct_imputado"):
        m[c] = num(m[c]).fillna(0.0)
    for c in ("mes", "fecha", "flujo", "categoria", "subcategoria", "tipo_gasto", "naturaleza", "comercio", "cuenta", "concepto", "nota", "regla"):
        m[c] = m[c].astype(str).replace("nan", "") if c in m.columns else ""
    m["mes"] = m["mes"].str[:7]
    return normalizar_categorias(m)  # categorías antiguas («Vivienda: alquiler») a la forma nueva


# ------------------------------------------------------------------ cálculos
def es_comp_comida(m: pd.DataFrame) -> pd.Series:
    """Bizums de amigos que devuelven su parte de una comida (restan del gasto de restauración)."""
    return (m["flujo"] == "Gasto") & (m["importe"] > 0) & (m["nota"].str.startswith("Compensa su parte") | (m["regla"] == "compensacion_comida"))


def ingresos_mes(m: pd.DataFrame, coverflex_bruto: bool) -> pd.DataFrame:
    """Ingresos del trabajo por mes: nómina + retribución flexible (Coverflex)."""
    nomina = m[(m["flujo"] == "Ingreso") & (m["categoria"] == "Nómina")].groupby("mes")["importe"].sum()
    cov = m[m["cuenta"] == "COVERFLEX"]
    if coverflex_bruto:
        flex = -cov.groupby("mes")["importe"].sum()
    else:
        flex = (-cov.groupby("mes")["importe_imputado"].sum()).sub(m[es_comp_comida(m)].groupby("mes")["importe"].sum(), fill_value=0)
    otros = m[(m["flujo"] == "Ingreso") & (m["categoria"] != "Nómina") & (m["tipo_gasto"] != "Extraordinario")].groupby("mes")["importe"].sum()
    return pd.DataFrame({"nomina": nomina, "flex": flex, "otros": otros}).fillna(0.0)


def mensual(m: pd.DataFrame, coverflex_bruto: bool) -> pd.DataFrame:
    gasto = m[m["flujo"] == "Gasto"]
    t = gasto.pivot_table(index="mes", columns="tipo_gasto", values="importe_imputado", aggfunc="sum", fill_value=0) * -1
    for c in ("Fijo", "Variable", "Extraordinario"):
        t[c] = t[c] if c in t.columns else 0.0
    t = t.join(ingresos_mes(m, coverflex_bruto), how="outer").fillna(0.0)
    t["ingresos"] = t["nomina"] + t["flex"]
    t["gasto_normal"] = t["Fijo"] + t["Variable"]
    t["margen"] = t["ingresos"] - t["gasto_normal"]
    return t.reset_index().rename(columns={"index": "mes"}).sort_values("mes")


# ------------------------------------------------------------------ carga y periodo
m_all = movimientos()
if m_all.empty:
    st.error("No hay datos en la pestaña «movimientos». ¿Se ha ejecutado el pipeline?")
    st.stop()

hoy = pd.Timestamp.today()
completos_todos = sorted(x for x in m_all["mes"].unique() if x < hoy.strftime("%Y-%m"))


def meses_incompletos(m: pd.DataFrame, meses: list[str]) -> dict[str, list[str]]:
    """Meses en los que falta una cuenta principal porque aún no teníamos sus datos (p. ej. la conjunta
    de la que aún no tenías datos porque estaba en otro banco). Cuenta principal: ≥ 20 movimientos al mes de media.
    Devuelve {mes: [cuentas que faltan]}."""
    por = m[m["mes"].isin(meses)].groupby(["cuenta", "mes"]).size().unstack(fill_value=0)
    principales = por.index[por.mean(axis=1) >= 20]
    falta = {}
    for c_ in principales:
        primero = por.columns[por.loc[c_] > 0].min()
        for mes_ in meses:
            if mes_ < primero:
                falta.setdefault(mes_, []).append(c_)
    return falta


INCOMPLETOS = meses_incompletos(m_all, completos_todos)
completos = [x for x in completos_todos if x not in INCOMPLETOS]  # solo meses con todas las cuentas principales

with st.sidebar:
    st.markdown("### Periodo")
    n_meses = st.slider("Meses completos", 3, max(3, len(completos)), min(12, len(completos)),
                        help="Últimos meses completos que se usan para calcular todas las medias.")
    periodo = completos[-n_meses:]
    n_rec = st.slider("Meses recientes", 1, max(1, n_meses - 1), min(4, n_meses - 1),
                      help="Los últimos meses del periodo, para comparar la tendencia reciente con la media.")
    recientes = periodo[-n_rec:]
    st.caption(f"Periodo: {mes_lab(periodo[0])} – {mes_lab(periodo[-1])}  \nRecientes: desde {mes_lab(recientes[0])}")
    if INCOMPLETOS:
        nombres = {"REV_CONJ": "la cuenta conjunta", "SAB_CORR": "el Sabadell", "TR": "Trade Republic", "COVERFLEX": "Coverflex"}
        faltan = sorted({c_ for v_ in INCOMPLETOS.values() for c_ in v_})
        st.caption(f"Fuera del análisis: {mes_lab(min(INCOMPLETOS))} – {mes_lab(max(INCOMPLETOS))}, porque falta "
                   f"{', '.join(nombres.get(c_, c_) for c_ in faltan)}.")
    st.markdown("### Filtros")
    cats_gasto_todas = sorted(m_all.loc[m_all["flujo"] == "Gasto", "categoria"].unique())
    cats_sel = st.multiselect("Categorías de gasto", cats_gasto_todas, default=[], placeholder="Todas",
                              help="Vacío = todas. Filtra «Este mes», «Gastos» y «Desglose». El Resumen (ingresos, margen) usa siempre todo el gasto.")
    cats_pat = st.multiselect("Tipos de activo", ["Fondos Indexa", "Plan de pensiones Indexa", "Fondos MyInvestor", "ETF y acciones Trade Republic",
                                                  "Cripto", "Efectivo en cuentas"], default=[], placeholder="Todos",
                              help="Filtra la pestaña Patrimonio. Vacío = todos.")
    st.divider()
    if st.button("Recargar datos de la hoja", width="stretch"):
        st.cache_data.clear()
        st.rerun()

flex_bruto = True  # Coverflex en bruto: acordado como el criterio más realista (DECISIONES §11)
m = m_all[m_all["mes"].isin(periodo)]
gasto = m[m["flujo"] == "Gasto"]
normal = gasto[gasto["tipo_gasto"] != "Extraordinario"]
cats_sel = cats_sel or cats_gasto_todas
normal_f = normal[normal["categoria"].isin(cats_sel)]  # para las pestañas filtrables
men = mensual(m, flex_bruto)
ing_mes, gas_mes = men["ingresos"].mean(), men["gasto_normal"].mean()
margen = (ing_mes - gas_mes) / ing_mes if ing_mes else np.nan

_cfg_nombres = _cuentas_cfg()
YO, OTRO = _cfg_nombres.get("nombre_titular", "Titular"), _cfg_nombres.get("nombre_pareja", "Pareja")
st.title(f"Finanzas de {YO}")
if DEMO:
    st.info("**Demo con datos inventados.** Alex cobra 2.000 € netos al mes, comparte piso con Sam (cuenta conjunta al 50 %), "
            "invierte cada mes en dos fondos indexados y le gustan el gimnasio, la guitarra y el cine. Puedes tocarlo todo: "
            "lo que edites solo dura mientras tengas la página abierta.", icon="🧪")
st.caption(f"{mes_lab(periodo[0])} – {mes_lab(periodo[-1])} · ingresas {eur(ing_mes)} al mes, gastas {eur(gas_mes)} y ahorras el {pct(margen)}.")

# Avisos de datos
avisos = []
ult = m_all.groupby("cuenta")["fecha"].max()
for cuenta, nombre in (("TR", "extracto de Trade Republic"), ("COVERFLEX", "export de Coverflex"), ("MYINV", "CSV de MyInvestor"),
                       ("SAB_CORR", "Excel del Sabadell corriente"), ("SAB_AHO", "Excel del Sabadell ahorro")):
    if cuenta in ult.index and (hoy - pd.to_datetime(ult[cuenta])).days > DIAS_AVISO:
        avisos.append(f"{nombre}: último movimiento el {pd.to_datetime(ult[cuenta]):%d/%m/%Y}")
pend = pestana("pendientes")
if len(pend):
    avisos.append(f"{len(pend)} movimientos pendientes de clasificar")
if avisos:
    st.warning("**Revisa:** " + " · ".join(avisos), icon="⚠️")

tabs = st.tabs(["Resumen", "Este mes", "Ingresos", "Gastos", "Desglose", "Dónde ahorrar", "Conjunta", "Patrimonio", "Inversiones", "Fiscal", "Vivienda", "Calidad del dato"])
T = dict(zip(["resumen", "mes", "ingresos", "gastos", "desglose", "ahorrar", "conjunta", "patrimonio", "inversiones", "fiscal", "vivienda", "estado"], tabs))

# ------------------------------------------------------------------ Resumen
with T["resumen"]:
    inv = -m[m["flujo"] == "Ahorro / inversión"]["importe"].sum() / n_meses
    otros_ing = men["otros"].mean()
    k = st.columns(5)
    k[0].metric("Ingresos / mes", eur(ing_mes), border=True,
                help="Media mensual de nómina neta + todo lo pagado con Coverflex (retribución flexible que se descuenta del bruto). "
                     + ("" if flex_bruto else "Ahora mismo: solo el Coverflex que consumes tú. ")
                     + "La nómina de los días 1–5 cuenta para el mes anterior. No incluye ventas, intereses, regalos ni otros ingresos "
                     f"(aparte: {eur(otros_ing)}/mes).")
    k[1].metric("Gasto normal / mes", eur(gas_mes), border=True,
                help="Gasto fijo + variable, tu parte (la cuenta conjunta al 50 %). Excluye extraordinarios (p. ej. una boda, un viaje grande, el IRPF), "
                     "traspasos entre cuentas, inversión y Bizums sin gasto asociado.")
    k[2].metric("Gasto fijo / mes", eur(men["Fijo"].mean()), border=True,
                help="Cargos recurrentes: alquiler, fibra, seguros, suscripciones, licencias y donaciones periódicas, tu parte.")
    k[3].metric("Margen de ahorro", pct(margen), border=True,
                help="(Ingresos − gasto normal) ÷ ingresos. Es lo que te queda de lo que ganas trabajando, antes de invertir.")
    k[4].metric("Invertido / mes", eur(inv), border=True,
                help="Aportaciones a Indexa, MyInvestor, plan de pensiones, Kraken y planes de Trade Republic, media mensual.")

    st.subheader("Ingresos frente a gasto")
    x = men["mes"].map(mes_lab)
    fig = go.Figure()
    for col, color in (("Fijo", C["azul"]), ("Variable", C["arena"]), ("Extraordinario", C["gris"])):
        fig.add_bar(x=x, y=men[col], name=col, marker_color=color, hovertemplate="%{y:,.0f} €")
    fig.add_scatter(x=x, y=men["ingresos"], name="Ingresos", mode="lines+markers", line=dict(color=C["verde"], width=3),
                    hovertemplate="%{y:,.0f} €")
    _ = fig.update_layout(barmode="stack", bargap=0.35)
    st.plotly_chart(estilo(fig, 380), width="stretch", key="g1")

    a, b = st.columns(2)
    with a:
        st.subheader("Margen de ahorro mensual", help="(Ingresos − gasto normal) ÷ ingresos de cada mes. La línea discontinua es la media del periodo.")
        t = men.assign(tasa=np.where(men["ingresos"] > 0, men["margen"] / men["ingresos"], np.nan))
        fig = go.Figure(go.Bar(x=x, y=t["tasa"], marker_color=[C["verde"] if v >= margen else C["arena"] for v in t["tasa"].fillna(0)],
                               hovertemplate="%{y:.1%}<extra></extra>"))
        _ = fig.add_hline(y=margen, line_dash="dash", line_color=C["gris"])
        _ = estilo(fig, 260)
        _ = fig.update_yaxes(tickformat=".0%", ticksuffix="")
        st.plotly_chart(fig, width="stretch", key="g2")
    with b:
        st.subheader("Esencial y discrecional", help="Esencial: vivienda, suministros, supermercado, transporte, salud, seguros, hogar, impuestos. Discrecional: el resto.")
        nat = normal.groupby("naturaleza")["importe_imputado"].sum().mul(-1) / n_meses
        fig = go.Figure(go.Pie(labels=nat.index, values=nat.values, hole=0.6, marker_colors=[C["azul"], C["arena"]],
                               hovertemplate="%{label}: %{value:,.0f} €/mes (%{percent})<extra></extra>", textinfo="label+percent"))
        st.plotly_chart(estilo(fig, 260), width="stretch", key="g3")





# ------------------------------------------------------------------ Este mes y presupuestos
def _presupuestos() -> pd.Series:
    p = pestana("presupuestos")
    if p.empty or "categoria" not in p.columns:
        return pd.Series(dtype=float)
    p = p[p["categoria"].astype(str).str.strip() != ""]
    return pd.Series(num(p["presupuesto_mensual"]).values, index=p["categoria"].astype(str).str.strip()).dropna()


with T["mes"]:
    meses_sel = sorted(set(m_all["mes"]) | {hoy.strftime("%Y-%m")}, reverse=True)
    mes_act = st.selectbox("Mes", meses_sel, index=0, format_func=lambda x: mes_lab(x) + (" (en curso)" if x == hoy.strftime("%Y-%m") else ""),
                           key="mes_vista", help="Por defecto, el mes en curso. Elige uno anterior para verlo cerrado (p. ej. el día 1).")
    en_curso_m = mes_act == hoy.strftime("%Y-%m")
    dias_mes = pd.Period(mes_act).days_in_month
    fraccion = min(1.0, hoy.day / dias_mes) if en_curso_m else 1.0
    act = m_all[(m_all["mes"] == mes_act) & (m_all["flujo"] == "Gasto") & (m_all["tipo_gasto"] != "Extraordinario") & m_all["categoria"].isin(cats_sel)]
    por_cat_act = act.groupby("categoria")["importe_imputado"].sum().mul(-1)
    media_cat = normal_f.groupby("categoria")["importe_imputado"].sum().mul(-1) / n_meses
    # Proyección: lo gastado + lo que se suele gastar en los días que quedan (a ritmo medio).
    proy_cat = por_cat_act.reindex(media_cat.index.union(por_cat_act.index), fill_value=0) + media_cat.reindex(
        media_cat.index.union(por_cat_act.index), fill_value=0) * (1 - fraccion)
    pres = _presupuestos()
    gastado, proy = por_cat_act.sum(), proy_cat.sum()

    if en_curso_m:
        st.caption(f"{MESES[hoy.month - 1].capitalize()} {hoy.year} · día {hoy.day} de {dias_mes} ({pct(fraccion, 0)} del mes). "
                   "Las fuentes manuales (Trade Republic, Coverflex, Sabadell…) solo cuentan hasta el último fichero que hayas subido.")
    else:
        st.caption(f"{mes_lab(mes_act).capitalize()}: mes cerrado.")
    k = st.columns(4)
    k[0].metric("Gastado en el mes", eur(gastado), border=True, help="Gasto normal (sin extraordinarios), tu parte, desde el día 1 hasta el último dato disponible.")
    k[1].metric("A estas alturas sueles llevar" if en_curso_m else "Tu media mensual", eur(gas_mes * fraccion), border=True,
                help="Media mensual de gasto normal del periodo × fracción del mes transcurrida. Orientativo: los fijos (alquiler) se cargan a principio de mes.")
    k[2].metric("Proyección a fin de mes" if en_curso_m else "Total del mes", eur(proy), f"{eur_delta(proy - gas_mes)} vs media", delta_color="inverse", border=True,
                help="Lo gastado + la media de cada categoría para los días que quedan.")
    if len(pres):
        con_pres = por_cat_act.reindex(pres.index, fill_value=0).sum()
        k[3].metric("Presupuesto restante", eur(pres.sum() - con_pres), f"de {eur(pres.sum())}", delta_color="off", border=True,
                    help="Suma de los presupuestos mensuales − lo gastado este mes en esas mismas categorías.")
    else:
        k[3].metric("Presupuesto restante", "—", border=True, help="Aún no has definido presupuestos (pestaña «presupuestos» de la hoja).")

    st.subheader(f"Flujo de caja de {mes_lab(mes_act)}", help="De dónde ha entrado el dinero este mes y a dónde ha ido, hasta el último dato disponible. "
                 "Si la nómina aún no ha llegado, verás que el gasto sale «de tus ahorros».")
    m_act = m_all[m_all["mes"] == mes_act]
    ing_act = ingresos_mes(m_act, flex_bruto).sum() if not m_act.empty else pd.Series(dtype=float)
    inv_act = -m_act[m_act["flujo"] == "Ahorro / inversión"]["importe"].sum()
    fig_s, enl_s = sankey({"Nómina": ing_act.get("nomina", 0), "Coverflex": ing_act.get("flex", 0), "Otros ingresos": ing_act.get("otros", 0)},
                          por_cat_act, inv_act)
    sankey_interactivo(fig_s, enl_s, m_act[m_act["categoria"].isin(cats_sel) | (m_act["flujo"] != "Gasto")], por_cat_act, media=False)

    tabla_mes = pd.DataFrame({"Este mes": por_cat_act, "Proyección": proy_cat, "Media": media_cat, "Presupuesto": pres}).fillna(0)
    tabla_mes = tabla_mes[(tabla_mes[["Este mes", "Media", "Presupuesto"]] > 1).any(axis=1)].sort_values("Media")
    st.subheader("Por categoría", help="Barra: gastado este mes. Línea fina: proyección a fin de mes. Rombo: tu media mensual. Raya: presupuesto (si lo hay).")
    fig = go.Figure()
    fig.add_bar(y=tabla_mes.index, x=tabla_mes["Este mes"], name="Este mes", orientation="h", marker_color=C["azul"], hovertemplate="%{x:,.0f} €")
    fig.add_bar(y=tabla_mes.index, x=(tabla_mes["Proyección"] - tabla_mes["Este mes"]).clip(lower=0), name="Proyección (resto del mes)",
                orientation="h", marker_color="rgba(108,142,191,0.30)", hovertemplate="+%{x:,.0f} €")
    fig.add_scatter(y=tabla_mes.index, x=tabla_mes["Media"], name="Media mensual", mode="markers",
                    marker=dict(symbol="diamond", size=9, color=C["arena"]), hovertemplate="%{x:,.0f} €")
    if len(pres):
        con_p = tabla_mes[tabla_mes["Presupuesto"] > 0]
        fig.add_scatter(y=con_p.index, x=con_p["Presupuesto"], name="Presupuesto", mode="markers",
                        marker=dict(symbol="line-ns", size=18, line=dict(width=3, color=C["rojo"])), hovertemplate="%{x:,.0f} €")
    fig.update_layout(barmode="stack", bargap=0.35)
    st.plotly_chart(estilo(fig, 80 + 34 * len(tabla_mes), euros_en="x"), width="stretch", key="mes_cat")

    st.subheader("Presupuestos", help="Se definen en la pestaña «presupuestos» de la hoja: categoria, presupuesto_mensual, nota.")
    if not len(pres):
        st.info("Todavía no hay presupuestos. Como punto de partida, aquí tienes tu media mensual de cada categoría redondeada: "
                "copia el bloque y pégalo en la celda A1 de la pestaña «presupuestos», y ajusta los importes a tu objetivo.")
        sug = (media_cat[media_cat > 5].sort_values(ascending=False) / 10).round().mul(10).astype(int)
        st.code("categoria\tpresupuesto_mensual\tnota\n" + "\n".join(f"{c}\t{v}\t" for c, v in sug.items()), language=None)
    else:
        hist = normal_f.pivot_table(index="mes", columns="categoria", values="importe_imputado", aggfunc="sum", fill_value=0) * -1
        filas = []
        for cat, tope in pres.items():
            actual, pr = por_cat_act.get(cat, 0.0), proy_cat.get(cat, 0.0)
            meses_pasados = int((hist.get(cat, pd.Series(dtype=float)) > tope).sum())
            estado = "🔴 superado" if actual > tope else ("🟠 va a pasarse" if pr > tope else "🟢 dentro")
            filas.append({"Categoría": cat, "Presupuesto": tope, "Este mes": actual, "Usado": actual / tope if tope else np.nan,
                          "Proyección": pr, "Estado": estado, f"Meses pasados (de {n_meses})": meses_pasados})
        st.dataframe(pd.DataFrame(filas), hide_index=True, width="stretch", column_config={
            "Presupuesto": st.column_config.NumberColumn("Presupuesto (€)", format="localized"),
            "Este mes": st.column_config.NumberColumn("Este mes (€)", format="localized"),
            "Usado": st.column_config.ProgressColumn("Usado", format="percent", min_value=0, max_value=1),
            "Proyección": st.column_config.NumberColumn("Proyección (€)", format="localized"),
            f"Meses pasados (de {n_meses})": st.column_config.NumberColumn(help="Meses del periodo en que el gasto de la categoría superó el presupuesto.")})


    # ---------- Cierre de mes
    st.subheader("Cierre del mes anterior", help="Un mes completo comparado con la media de los 6 meses anteriores: qué ha subido, qué ha bajado, "
                 "comercios nuevos y cargos fuera de lo normal.")
    if len(completos) >= 2 and (pd.Period(mes_act) - 1).strftime("%Y-%m") in completos_todos:
        mes_c = (pd.Period(mes_act) - 1).strftime("%Y-%m")  # el mes anterior al que se está viendo: ya cerrado
        st.caption(f"Cierre de {mes_lab(mes_c)} frente a la media de los 6 meses anteriores.")
        previos = [x for x in completos if x < mes_c][-6:]
        gn_all = m_all[(m_all["flujo"] == "Gasto") & (m_all["tipo_gasto"] != "Extraordinario")]
        este = gn_all[gn_all["mes"] == mes_c]
        antes = gn_all[gn_all["mes"].isin(previos)]
        cat_este = este.groupby("categoria")["importe_imputado"].sum().mul(-1)
        cat_media = antes.groupby("categoria")["importe_imputado"].sum().mul(-1) / max(len(previos), 1)
        ing_c = ingresos_mes(m_all[m_all["mes"] == mes_c], True).sum()
        ing_c = ing_c.get("nomina", 0) + ing_c.get("flex", 0)
        ing_ant = ingresos_mes(m_all[m_all["mes"].isin(previos)], True).sum()
        ing_ant = (ing_ant.get("nomina", 0) + ing_ant.get("flex", 0)) / max(len(previos), 1)
        k = st.columns(4)
        k[0].metric("Gasto normal", eur(cat_este.sum()), f"{eur_delta(cat_este.sum() - cat_media.sum())} vs media", delta_color="inverse", border=True)
        k[1].metric("Ingresos del trabajo", eur(ing_c), f"{eur_delta(ing_c - ing_ant)} vs media", border=True)
        k[2].metric("Margen del mes", pct((ing_c - cat_este.sum()) / ing_c) if ing_c else "—", border=True)
        k[3].metric("Meses de referencia", f"{len(previos)}", border=True, help=f"Media de {', '.join(mes_lab(x) for x in previos)}.")
        dif = pd.DataFrame({"Este mes": cat_este, "Media": cat_media}).fillna(0)
        dif["Diferencia"] = dif["Este mes"] - dif["Media"]
        a_, b_ = st.columns(2)
        with a_:
            st.markdown("**Lo que más ha subido**")
            tabla(dif[dif["Diferencia"] > 5].sort_values("Diferencia", ascending=False).head(6).reset_index().rename(columns={"categoria": "Categoría"}),
                  euros=["Este mes", "Media", "Diferencia"])
        with b_:
            st.markdown("**Lo que más ha bajado**")
            tabla(dif[dif["Diferencia"] < -5].sort_values("Diferencia").head(6).reset_index().rename(columns={"categoria": "Categoría"}),
                  euros=["Este mes", "Media", "Diferencia"])
        # Comercios nuevos y cargos inusuales
        com = lambda d: d["comercio"].where(d["comercio"] != "", d["concepto"]).map(comercio_norm)  # noqa: E731
        este = este.assign(cn=com(este), cargo=-este["importe_imputado"])
        hist = gn_all[gn_all["mes"] < mes_c].assign(cn=lambda d: com(d), cargo=lambda d: -d["importe_imputado"])
        nuevos = este[~este["cn"].isin(set(hist["cn"]))].groupby("cn")["cargo"].agg(["size", "sum"]).sort_values("sum", ascending=False)
        ref_cat = hist[hist["mes"] >= (pd.Period(mes_c) - 12).strftime("%Y-%m")].query("cargo > 0").groupby("categoria")["cargo"]
        med, p95 = ref_cat.median(), ref_cat.quantile(0.95)
        raro = este[(este["cargo"] > 30) & (este["cargo"] > np.maximum(2 * este["categoria"].map(med).fillna(np.inf), este["categoria"].map(p95).fillna(np.inf)))]
        e_ = este.sort_values("fecha")
        e_["f"] = pd.to_datetime(e_["fecha"])
        dup = []
        for (cn_, imp_), g_ in e_[e_["cargo"] > 0].groupby(["cn", "cargo"]):
            if len(g_) > 1 and (g_["f"].diff().dt.days.dropna() <= 3).any():
                dup.append(g_)
        dup = pd.concat(dup) if dup else e_.iloc[0:0]
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(f"**Comercios nuevos** ({len(nuevos)})", help="Primera vez que aparecen en tus datos.")
            st.dataframe(nuevos.reset_index().rename(columns={"cn": "Comercio", "size": "Cargos", "sum": "Total"}), hide_index=True, width="stretch", height=240,
                         column_config={"Total": st.column_config.NumberColumn("Total (€)", format="localized")})
        with c2:
            st.markdown(f"**Cargos inusuales** ({len(raro)})", help="Más de 30 € y por encima del doble de lo normal en su categoría y del 95 % de sus cargos del último año.")
            st.dataframe(raro[["fecha", "cn", "categoria", "cargo"]].rename(columns={"fecha": "Fecha", "cn": "Comercio", "categoria": "Categoría", "cargo": "Importe"}),
                         hide_index=True, width="stretch", height=240, column_config={"Importe": st.column_config.NumberColumn("Importe (€)", format="localized")})
        with c3:
            st.markdown(f"**Posibles duplicados** ({len(dup)})", help="Mismo comercio y mismo importe con 3 días o menos de diferencia. Pueden ser compras reales repetidas.")
            st.dataframe(dup[["fecha", "cn", "cargo"]].rename(columns={"fecha": "Fecha", "cn": "Comercio", "cargo": "Importe"}), hide_index=True, width="stretch",
                         height=240, column_config={"Importe": st.column_config.NumberColumn("Importe (€)", format="localized")})

# ------------------------------------------------------------------ Ingresos
with T["ingresos"]:
    ing_rows = m[(m["flujo"] == "Ingreso")].assign(concepto_ing=lambda x: x["categoria"], valor_ing=lambda x: x["importe"])
    cov_rows = m[m["cuenta"] == "COVERFLEX"].assign(concepto_ing="Retribución flexible (Coverflex)", valor_ing=lambda x: -x["importe"])
    todo_ing = pd.concat([ing_rows, cov_rows], ignore_index=True)
    todo_ing["origen"] = todo_ing["comercio"].where(todo_ing["comercio"] != "", todo_ing["concepto"].str.slice(0, 40))
    extra_ing = todo_ing[todo_ing["tipo_gasto"] == "Extraordinario"]
    norm_ing = todo_ing[todo_ing["tipo_gasto"] != "Extraordinario"]
    trabajo = norm_ing[norm_ing["concepto_ing"].isin(["Nómina", "Retribución flexible (Coverflex)"])]["valor_ing"].sum() / n_meses
    otros_m = norm_ing[~norm_ing["concepto_ing"].isin(["Nómina", "Retribución flexible (Coverflex)"])]["valor_ing"].sum() / n_meses
    k = st.columns(4)
    k[0].metric("Ingresos del trabajo / mes", eur(trabajo), border=True, help="Nómina neta + todo lo pagado con Coverflex (retribución flexible). Es la base del margen de ahorro.")
    k[1].metric("Otros ingresos / mes", eur(otros_m), border=True, help="Ventas de segunda mano, intereses, dividendos, cashback, saveback, regalos de familia… (sin extraordinarios).")
    k[2].metric("Peso de nómina + Coverflex", pct(norm_ing.loc[norm_ing["concepto_ing"].isin(["Nómina", "Retribución flexible (Coverflex)"]), "valor_ing"].sum()
                                                  / norm_ing["valor_ing"].sum()) if norm_ing["valor_ing"].sum() else "—", border=True,
                help="(Nómina + Coverflex) ÷ todos los ingresos normales. Coverflex también sale de tu sueldo, aunque no llegue en la nómina.")
    k[3].metric("Extraordinarios del periodo", eur(extra_ing["valor_ing"].sum()), border=True,
                help="Ingresos puntuales que no cuentan en las medias (p. ej. regalos de una boda).")

    porc = pd.DataFrame({
        "Media del periodo": norm_ing.groupby("concepto_ing")["valor_ing"].sum() / n_meses,
        "Media reciente": norm_ing[norm_ing["mes"].isin(recientes)].groupby("concepto_ing")["valor_ing"].sum() / n_rec,
    }).fillna(0).sort_values("Media del periodo")
    a_, b_ = st.columns([3, 2])
    with a_:
        st.subheader("Por concepto", help="Media mensual. «Reciente»: los últimos meses del periodo (barra lateral).")
        fig = go.Figure()
        for col, color in zip(porc.columns, (C["verde"], C["arena"])):
            fig.add_bar(y=porc.index, x=porc[col], name=col, orientation="h", marker_color=color, hovertemplate="%{x:,.0f} €/mes")
        _ = fig.update_layout(barmode="group")
        st.plotly_chart(estilo(fig, 80 + 40 * len(porc), euros_en="x"), width="stretch", key="ing_conc")
    with b_:
        st.subheader("Otros ingresos: de dónde vienen", help="Total del periodo de todo lo que no es nómina ni Coverflex.")
        o_ = norm_ing[~norm_ing["concepto_ing"].isin(["Nómina", "Retribución flexible (Coverflex)"])]
        top = o_.groupby(["concepto_ing", "origen"], as_index=False).agg(Veces=("valor_ing", "size"), Total=("valor_ing", "sum")).sort_values("Total", ascending=False).head(12)
        tabla(top.rename(columns={"concepto_ing": "Concepto", "origen": "Origen"}), euros=["Total"])

    st.subheader("Mes a mes")
    im = norm_ing.pivot_table(index="mes", columns="concepto_ing", values="valor_ing", aggfunc="sum", fill_value=0).reindex(periodo).fillna(0)
    fig = go.Figure()
    for col, color in zip(im.sum().sort_values(ascending=False).index, [C["verde"], C["azul"], C["arena"], C["violeta"], C["cian"], C["rojo"], C["gris"]] * 2):
        fig.add_bar(x=[mes_lab(i) for i in im.index], y=im[col], name=col, marker_color=color, hovertemplate="%{y:,.0f} €")
    _ = fig.update_layout(barmode="stack", bargap=0.35)
    st.plotly_chart(estilo(fig, 340), width="stretch", key="ing_mes")

    st.subheader("Movimientos de ingreso")
    conc_sel = st.multiselect("Conceptos", sorted(norm_ing["concepto_ing"].unique()), default=[], placeholder="Todos", key="ing_conc_sel")
    t_ing = norm_ing[norm_ing["concepto_ing"].isin(conc_sel)] if conc_sel else norm_ing
    st.dataframe(t_ing.sort_values("fecha", ascending=False)[["fecha", "cuenta", "concepto_ing", "concepto", "valor_ing", "nota"]].assign(
        fecha=lambda x: pd.to_datetime(x["fecha"], errors="coerce")), hide_index=True, width="stretch", height=360, column_config={
        "fecha": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY"), "cuenta": "Cuenta", "concepto_ing": "Concepto de ingreso",
        "concepto": "Descripción", "valor_ing": st.column_config.NumberColumn("Importe (€)", format="localized"), "nota": "Nota"})
    if len(extra_ing):
        with st.expander("Ingresos extraordinarios del periodo"):
            st.dataframe(extra_ing.sort_values("fecha")[["fecha", "cuenta", "concepto", "categoria", "importe", "nota"]], hide_index=True, width="stretch",
                         column_config={"importe": st.column_config.NumberColumn("Importe (€)", format="localized")})


# ------------------------------------------------------------------ Dónde ahorrar
with T["ahorrar"]:
    base = normal_f.assign(cargo=-normal_f["importe_imputado"], comercio_n=normal_f["comercio"].where(normal_f["comercio"] != "", normal_f["concepto"]).map(comercio_norm))
    base = base[base["cargo"] > 0]
    # Clave de agrupación: primera palabra (7 letras) para unir variantes («Spotify P457…», «SpotifyES»).
    genericas = {"to", "transferencia", "adeudo", "recibo", "compra", "pago", "bizum", "trf", "transfer"}
    def _clave(c):
        w = str(c).lower().split()
        return " ".join(w[:3]) if w and w[0] in genericas else (w[0][:7] if w else "")
    base["clave_c"] = base["comercio_n"].map(_clave)
    nombre_c = base.groupby("clave_c")["comercio_n"].agg(lambda x: x.value_counts().index[0])
    base["comercio_n"] = base["clave_c"].map(nombre_c)
    # Categorías cuyo cargo se devuelve (p. ej. comisión de mantenimiento bonificada): no son gasto real.
    neto_cat = normal_f.groupby("categoria")["importe_imputado"].sum().mul(-1)
    bruto_cat = base.groupby("categoria")["cargo"].sum()
    devueltas = [c for c in bruto_cat.index if bruto_cat[c] > 0 and neto_cat.get(c, 0) < 0.1 * bruto_cat[c]]

    # ---------- 1. Gasto hormiga
    st.subheader("Gasto hormiga", help="Compras pequeñas y frecuentes que, sumadas, pesan más de lo que parece. "
                 "Se agrupan por comercio y se anualizan con la media del periodo.")
    c1, c2 = st.columns([1, 1])
    umbral = c1.slider("Importe máximo por compra (€)", 3, 40, 15, 1, help="Tu parte de cada cargo. Por debajo de esto cuenta como compra pequeña.")
    solo_disc = c2.toggle("Solo gasto discrecional", value=True, help="Excluye supermercado, transporte, farmacia y demás gasto esencial.")
    h_ = base[(base["cargo"] <= umbral) & ~base["categoria"].str.startswith(("Compartido", "Regalos y donaciones"))]
    if solo_disc:
        h_ = h_[h_["naturaleza"] == "Discrecional"]
    hor = h_.groupby("comercio_n").agg(Compras=("cargo", "size"), Total=("cargo", "sum"), Categoría=("categoria", "first")).reset_index()
    hor["Compras / mes"] = hor["Compras"] / n_meses
    hor["€ / mes"] = hor["Total"] / n_meses
    hor["€ / año"] = hor["€ / mes"] * 12
    hor["Ticket medio"] = hor["Total"] / hor["Compras"]
    hor = hor[hor["Compras"] >= max(3, n_meses // 2)].sort_values("€ / año", ascending=False)  # lo frecuente
    k = st.columns(4)
    k[0].metric("Gasto hormiga / mes", eur(hor["€ / mes"].sum()), border=True, help="Suma de los comercios frecuentes por debajo del umbral.")
    k[1].metric("Al año", eur(hor["€ / año"].sum()), border=True)
    k[2].metric("Compras pequeñas / mes", f"{hor['Compras / mes'].sum():.0f}", border=True)
    k[3].metric("Peso en tu gasto normal", pct(hor["€ / mes"].sum() / gas_mes) if gas_mes else "—", border=True)
    a_, b_ = st.columns([2, 3])
    with a_:
        top_h = hor.head(10).iloc[::-1]
        fig = go.Figure(go.Bar(y=top_h["comercio_n"], x=top_h["€ / año"], orientation="h", marker_color=C["arena"], name="€ / año",
                               customdata=np.stack([top_h["Compras / mes"], top_h["Ticket medio"]], axis=-1),
                               hovertemplate="%{y}: %{x:,.0f} €/año<br>%{customdata[0]:.1f} compras/mes · ticket %{customdata[1]:.2f} €<extra></extra>"))
        st.plotly_chart(estilo(fig, 60 + 32 * len(top_h), euros_en="x"), width="stretch", key="hormiga")
    with b_:
        st.dataframe(hor[["comercio_n", "Categoría", "Compras / mes", "Ticket medio", "€ / mes", "€ / año"]].rename(columns={"comercio_n": "Comercio"}),
                     hide_index=True, width="stretch", height=60 + 32 * min(len(top_h), 10), column_config={
                         "Compras / mes": st.column_config.NumberColumn(format="%.1f"),
                         "Ticket medio": st.column_config.NumberColumn("Ticket medio (€)", format="localized"),
                         "€ / mes": st.column_config.NumberColumn(format="localized"), "€ / año": st.column_config.NumberColumn(format="localized")})

    # ---------- 2. Suscripciones y cargos recurrentes
    st.subheader("Suscripciones y cargos recurrentes", help="Gasto fijo (alquiler, fibra, seguros, suscripciones…) y cualquier comercio con un único cargo "
                 "de importe casi idéntico en al menos 3 meses distintos. Aviso si el último cargo se aparta más de un 5 % del habitual.")
    meses_por = base.groupby("comercio_n")["mes"].nunique()
    anuales = base[(base["categoria"] == "Suscripciones") & (base["tipo_gasto"] == "Fijo")]["comercio_n"].unique()  # p. ej. Amazon Prime
    candidatos = set(meses_por[meses_por >= 3].index) | set(anuales)
    rec_filas = []
    for com, g in base[base["comercio_n"].isin(candidatos) & ~base["categoria"].isin(devueltas)].groupby("comercio_n"):
        if (g["tipo_gasto"] == "Fijo").any():  # p. ej. «Amazon»: solo los cargos fijos (Prime), no las compras
            g = g[g["tipo_gasto"] == "Fijo"]
        por_mes = g.groupby("mes")["cargo"].sum()
        med = por_mes.median()
        fijo = (g["tipo_gasto"] == "Fijo").mean() > 0.5
        if len(por_mes) < 3 and not fijo:
            continue
        estable = (por_mes.std(ddof=0) / med) <= 0.05 and g.groupby("mes").size().max() == 1
        if med <= 0 or not (fijo or estable):
            continue  # importes variables o varias compras al mes: es una tienda habitual, no una suscripción
        ult = g.sort_values("fecha").iloc[-1]
        cambio = (ult["cargo"] - med) / med
        # Frecuencia: hueco típico entre cargos (1 = mensual, 3 = trimestral…); un solo cargo fijo = anual.
        idx_m = sorted(pd.PeriodIndex(por_mes.index, freq="M").astype(int))
        hueco = float(np.median(np.diff(idx_m))) if len(idx_m) > 1 else 12.0
        rec_filas.append({"Comercio": com, "Categoría": g["categoria"].iloc[-1], "Meses con cargo": len(por_mes),
                          "Cada": {1: "mes", 2: "2 meses", 3: "trimestre", 6: "semestre", 12: "año"}.get(round(hueco), f"{hueco:.0f} meses"),
                          "Importe habitual": med, "€ / año": med * 12 / max(hueco, 1),
                          "Último cargo": pd.to_datetime(ult["fecha"]), "Último importe": ult["cargo"],
                          "Precio": "⬆️ ha subido" if cambio > 0.05 else ("⬇️ ha bajado" if cambio < -0.05 else "")})
    rec = pd.DataFrame(rec_filas)
    if rec.empty:
        st.info("No se han detectado cargos recurrentes en el periodo.")
    else:
        rec = rec.sort_values("€ / año", ascending=False)
        k = st.columns(3)
        k[0].metric("Recurrentes / año", eur(rec["€ / año"].sum()), border=True, help="Suma anualizada de todos los cargos recurrentes detectados.")
        k[1].metric("Cuántos", f"{len(rec)}", border=True)
        k[2].metric("Con subida de precio", f"{(rec['Precio'] == '⬆️ ha subido').sum()}", border=True)
        st.dataframe(rec, hide_index=True, width="stretch", column_config={
            "Importe habitual": st.column_config.NumberColumn("Importe habitual (€)", format="localized"),
            "€ / año": st.column_config.NumberColumn(format="localized"),
            "Último cargo": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Último importe": st.column_config.NumberColumn("Último importe (€)", format="localized")})
        st.caption("Revisa de vez en cuando si las sigues usando todas. Incluye también recibos fijos (alquiler, fibra, seguros): no todo es recortable.")

    # ---------- 3. ¿Y si…?
    st.subheader("¿Y si…?", help="Cuánto ahorrarías recortando un gasto concreto y cuánto valdría ese ahorro invertido.")
    c1, c2, c3 = st.columns([1, 2, 1])
    nivel = c1.selectbox("Recortar", ["Categoría", "Subcategoría", "Comercio"])
    col_n = {"Categoría": "categoria", "Subcategoría": "subcategoria", "Comercio": "comercio_n"}[nivel]
    gasto_por = base[base[col_n] != ""].groupby(col_n)["cargo"].sum().div(n_meses).sort_values(ascending=False)
    elegido = c2.selectbox(nivel, gasto_por.index.tolist(), format_func=lambda x: f"{x} · {eur(gasto_por[x])}/mes")
    recorte = c3.slider("Recorte (%)", 0, 100, 25, 5) / 100
    c4, c5 = st.columns(2)
    anos = c4.select_slider("Horizonte (años)", [1, 3, 5, 10, 15, 20, 25, 30], value=10)
    rent = c5.number_input("Rentabilidad anual si lo inviertes (%)", 0.0, 12.0, 5.0, 0.5,
                           help="Supuesto, no una previsión. Como referencia, tu TIR histórica en fondos indexados aparece en Patrimonio → Rentabilidad.") / 100
    if elegido:
        ahorro_m = gasto_por[elegido] * recorte
        k = st.columns(4)
        k[0].metric("Ahorro al mes", eur(ahorro_m), border=True)
        k[1].metric("Ahorro al año", eur(ahorro_m * 12), border=True)
        k[2].metric(f"Invertido, en {anos} años", eur(valor_futuro(ahorro_m, anos, rent)), f"{eur_delta(valor_futuro(ahorro_m, anos, rent) - ahorro_m * 12 * anos)} de rentabilidad",
                    delta_color="off", border=True, help="Aportando el ahorro cada mes a la rentabilidad indicada, sin impuestos ni inflación.")
        k[3].metric("Margen de ahorro", pct((ing_mes - gas_mes + ahorro_m) / ing_mes) if ing_mes else "—", f"hoy {pct(margen)}", delta_color="off", border=True)
        st.caption("Para tener una idea de conjunto: recortar un 20 % tus cinco categorías discrecionales más grandes supondría "
                   f"{eur(base[base['naturaleza'] == 'Discrecional'].groupby('categoria')['cargo'].sum().div(n_meses).nlargest(5).sum() * 0.2)} al mes.")

    # ---------- Lo que te devuelve la tarjeta
    st.subheader("Lo que te devuelve la tarjeta de Trade Republic", help="Saveback cobrado en los últimos 12 meses frente a lo que has pagado con la tarjeta.")
    doce = completos[-12:]
    sb = m_all[(m_all["cuenta"] == "TR") & m_all["concepto"].str.contains("Saveback", case=False, na=False) & m_all["mes"].isin(doce)]["importe"].sum()
    gasto_tr = -m_all[(m_all["cuenta"] == "TR") & (m_all["flujo"] == "Gasto") & (m_all["importe"] < 0) & m_all["mes"].isin(doce)]["importe"].sum()
    k = st.columns(3)
    k[0].metric("Saveback en 12 meses", eur(sb, 2), border=True, help="Lo que Trade Republic te ha devuelto y ha invertido en tu plan.")
    k[1].metric("Gasto con la tarjeta", eur(gasto_tr), border=True)
    k[2].metric("Devolución efectiva", pct(sb / gasto_tr, 2) if gasto_tr else "—", border=True,
                help="Saveback ÷ gasto con la tarjeta. Sirve para compararlo con lo que te daría otra tarjeta con el mismo gasto.")


# ------------------------------------------------------------------ Cuenta conjunta
with T["conjunta"]:
    cj = m_all[m_all["cuenta"] == "REV_CONJ"].copy()
    if cj.empty:
        st.info("No hay movimientos de la cuenta conjunta.")
    else:
        cj["ap_titular"] = np.where(cj["categoria"] == f"Aportación {YO} a conjunta", cj["importe"], 0.0)
        cj["ap_pareja"] = np.where(cj["categoria"] == f"Aportación {OTRO} a conjunta", cj["importe"], 0.0)
        cj["gasto"] = np.where(cj["flujo"] != "Traspaso interno", -cj["importe"], 0.0)
        bm = cj.groupby("mes")[["ap_titular", "ap_pareja", "gasto"]].sum()
        bm = bm[bm.index.isin(periodo)]
        bm["balance_titular"] = bm["ap_titular"] - bm["gasto"] / 2
        bm["acumulado"] = bm["balance_titular"].cumsum()
        tot = bm.sum()
        k = st.columns(4)
        k[0].metric("Has aportado", eur(tot["ap_titular"]), border=True, help="Tus transferencias a la conjunta en el periodo (aportación mensual y extras).")
        k[1].metric(f"Ha aportado {OTRO}", eur(tot["ap_pareja"]), border=True, help=f"Transferencias de {OTRO} a la conjunta en el periodo.")
        k[2].metric("Gasto común", eur(tot["gasto"]), border=True,
                    help=f"Todo lo que sale de la conjunta salvo traspasos: compras, alquiler, reposiciones a ti o a {OTRO}, comisiones… "
                         "A cada uno le corresponde la mitad.")
        bal = tot["ap_titular"] - tot["gasto"] / 2
        k[3].metric("Tu balance", eur(bal), "a tu favor" if bal >= 0 else f"a favor de {OTRO}", delta_color="off", border=True,
                    help="Lo que has aportado − la mitad del gasto común. Positivo: has puesto más de tu parte. "
                         "No incluye el saldo que queda en la cuenta, que es de los dos.")
        fig = go.Figure()
        fig.add_bar(x=[mes_lab(i) for i in bm.index], y=bm["balance_titular"], name="Balance del mes",
                    marker_color=[C["verde"] if v >= 0 else C["rojo"] for v in bm["balance_titular"]], hovertemplate="%{y:,.0f} €")
        fig.add_scatter(x=[mes_lab(i) for i in bm.index], y=bm["acumulado"], name="Acumulado", mode="lines+markers",
                        line=dict(color=C["azul"], width=3), hovertemplate="%{y:,.0f} €")
        st.subheader("Tu balance con la conjunta, mes a mes", help="Barras: lo que aportaste ese mes − la mitad del gasto común del mes. Línea: acumulado del periodo.")
        st.plotly_chart(estilo(fig, 340), width="stretch", key="conj_bal")
        st.subheader("En qué se va el gasto común")
        cj_p = cj[cj["mes"].isin(periodo)]  # solo los meses del periodo, como el resto de medias
        gc = cj_p[cj_p["flujo"] != "Traspaso interno"].groupby("categoria")["importe"].sum().mul(-1) / n_meses
        gc = gc[gc > 1].sort_values()
        fig = go.Figure(go.Bar(y=gc.index, x=gc.values, orientation="h", marker_color=C["azul"], name="Gasto común",
                               hovertemplate="%{y}: %{x:,.0f} €/mes<extra></extra>"))
        st.plotly_chart(estilo(fig, 60 + 30 * len(gc), euros_en="x"), width="stretch", key="conj_cat")
        st.caption("Importes totales de la cuenta (de los dos), media mensual del periodo.")

# ------------------------------------------------------------------ Gastos
with T["gastos"]:
    st.subheader("Flujo de caja medio", help="Media mensual del periodo: ingresos (nómina, Coverflex y otros) y cómo se reparten entre gasto esencial, "
                 "discrecional, inversión y lo que queda en las cuentas. Sin extraordinarios.")
    ing_med = ingresos_mes(m, flex_bruto).sum() / n_meses
    gastos_med = normal_f.groupby("categoria")["importe_imputado"].sum().mul(-1) / n_meses
    fig_s, enl_s = sankey({"Nómina": ing_med.get("nomina", 0), "Coverflex": ing_med.get("flex", 0), "Otros ingresos": ing_med.get("otros", 0)},
                          gastos_med, -m[m["flujo"] == "Ahorro / inversión"]["importe"].sum() / n_meses)
    st.caption("El diagrama muestra la media mensual; la tabla, todos los movimientos del periodo.")
    sankey_interactivo(fig_s, enl_s, m[m["categoria"].isin(cats_sel) | (m["flujo"] != "Gasto")], gastos_med, media=True)

    cat = pd.DataFrame({
        "Media del periodo": normal_f.groupby("categoria")["importe_imputado"].sum().mul(-1) / n_meses,
        "Media reciente": normal_f[normal_f["mes"].isin(recientes)].groupby("categoria")["importe_imputado"].sum().mul(-1) / n_rec,
    }).fillna(0)
    cat = cat[(cat > 1).any(axis=1)].sort_values("Media del periodo")
    st.subheader("Gasto normal por categoría", help="Media mensual, tu parte. «Reciente» son los últimos meses del periodo (barra lateral).")
    fig = go.Figure()
    for col, color in zip(cat.columns, (C["azul"], C["arena"])):
        fig.add_bar(y=cat.index, x=cat[col], name=col, orientation="h", marker_color=color, hovertemplate="%{x:,.0f} €/mes")
    _ = fig.update_layout(barmode="group", bargap=0.25)
    st.plotly_chart(estilo(fig, 60 + 36 * len(cat), euros_en="x"), width="stretch", key="g4")

    # ---------- Año contra año
    st.subheader("Año contra año", help="Los mismos meses de dos años seguidos, para ver si un gasto ha subido de verdad o es impresión. "
                 "Solo meses completos y con todas las cuentas principales en los dos años (en igualdad de condiciones).")
    anos_d = sorted({int(x[:4]) for x in completos}, reverse=True)
    if len(anos_d) >= 2:
        c1, c2 = st.columns([1, 3])
        a_act = c1.selectbox("Año", anos_d[:-1], key="yoy_ano", help="Se compara con el año anterior.")
        meses_act = [x for x in completos if x.startswith(str(a_act))]
        meses_ant = [x for x in completos if x.startswith(str(a_act - 1))]
        comunes = sorted({x[5:] for x in meses_act} & {x[5:] for x in meses_ant})
        if not comunes:
            st.info("No hay meses en común entre los dos años.")
        else:
            c2.caption(f"Meses comparados: {', '.join(MESES[int(x) - 1] for x in comunes)} de {a_act - 1} y {a_act}.")
            gn_y = m_all[(m_all["flujo"] == "Gasto") & (m_all["tipo_gasto"] != "Extraordinario") & m_all["categoria"].isin(cats_sel)]
            sel_ant = gn_y[gn_y["mes"].isin([f"{a_act - 1}-{x}" for x in comunes])]
            sel_act = gn_y[gn_y["mes"].isin([f"{a_act}-{x}" for x in comunes])]
            t_ant, t_act = -sel_ant["importe_imputado"].sum(), -sel_act["importe_imputado"].sum()
            ing_y = lambda a: ingresos_mes(m_all[m_all["mes"].isin([f"{a}-{x}" for x in comunes])], True).sum()  # noqa: E731
            i_ant, i_act = ing_y(a_act - 1), ing_y(a_act)
            i_ant, i_act = i_ant.get("nomina", 0) + i_ant.get("flex", 0), i_act.get("nomina", 0) + i_act.get("flex", 0)
            k = st.columns(4)
            k[0].metric(f"Gasto normal {a_act}", eur(t_act), f"{eur_delta(t_act - t_ant)} ({pct((t_act - t_ant) / t_ant) if t_ant else '—'})",
                        delta_color="inverse", border=True, help=f"Suma de los meses comparados. {a_act - 1}: {eur(t_ant)}.")
            k[1].metric(f"Ingresos del trabajo {a_act}", eur(i_act), f"{eur_delta(i_act - i_ant)}", border=True, help=f"{a_act - 1}: {eur(i_ant)}.")
            k[2].metric(f"Margen {a_act}", pct((i_act - t_act) / i_act) if i_act else "—",
                        f"{a_act - 1}: {pct((i_ant - t_ant) / i_ant) if i_ant else '—'}", delta_color="off", border=True)
            k[3].metric("Gasto medio al mes", eur(t_act / len(comunes)), f"{eur_delta((t_act - t_ant) / len(comunes))} al mes", delta_color="inverse", border=True)
            yoy = pd.DataFrame({str(a_act - 1): sel_ant.groupby("categoria")["importe_imputado"].sum().mul(-1),
                                str(a_act): sel_act.groupby("categoria")["importe_imputado"].sum().mul(-1)}).fillna(0)
            yoy = yoy[(yoy > 5).any(axis=1)]
            yoy["Diferencia"] = yoy[str(a_act)] - yoy[str(a_act - 1)]
            yoy["Variación"] = np.where(yoy[str(a_act - 1)] > 0, yoy["Diferencia"] / yoy[str(a_act - 1)], np.nan)
            a_, b_ = st.columns([3, 2])
            with a_:
                o_ = yoy.sort_values(str(a_act))
                fig = go.Figure()
                fig.add_bar(y=o_.index, x=o_[str(a_act - 1)], name=str(a_act - 1), orientation="h", marker_color=C["gris"], hovertemplate="%{x:,.0f} €")
                fig.add_bar(y=o_.index, x=o_[str(a_act)], name=str(a_act), orientation="h", marker_color=C["azul"], hovertemplate="%{x:,.0f} €")
                _ = fig.update_layout(barmode="group", bargap=0.25)
                st.plotly_chart(estilo(fig, 60 + 38 * len(o_), euros_en="x"), width="stretch", key="yoy_cat")
            with b_:
                st.dataframe(yoy.sort_values("Diferencia", ascending=False).reset_index().rename(columns={"categoria": "Categoría"}), hide_index=True,
                             width="stretch", height=60 + 38 * min(len(yoy), 14), column_config={
                                 str(a_act - 1): st.column_config.NumberColumn(f"{a_act - 1} (€)", format="localized"),
                                 str(a_act): st.column_config.NumberColumn(f"{a_act} (€)", format="localized"),
                                 "Diferencia": st.column_config.NumberColumn("Diferencia (€)", format="localized"),
                                 "Variación": st.column_config.NumberColumn(format="percent")})
            cat_y = st.selectbox("Mes a mes de", ["Todo el gasto normal"] + yoy.sort_values(str(a_act), ascending=False).index.tolist(), key="yoy_cat_sel")
            fig = go.Figure()
            for a_y, color, ancho in ((a_act - 1, C["gris"], 2), (a_act, C["azul"], 3)):
                d_y = gn_y[gn_y["mes"].isin([f"{a_y}-{x}" for x in comunes])]
                if cat_y != "Todo el gasto normal":
                    d_y = d_y[d_y["categoria"] == cat_y]
                serie_y = d_y.groupby(d_y["mes"].str[5:])["importe_imputado"].sum().mul(-1).reindex(comunes).fillna(0)
                fig.add_scatter(x=[MESES[int(x) - 1] for x in serie_y.index], y=serie_y.values, name=str(a_y), mode="lines+markers",
                                line=dict(color=color, width=ancho), hovertemplate="%{y:,.0f} €")
            st.plotly_chart(estilo(fig, 300), width="stretch", key="yoy_mes")
    else:
        st.info("Hace falta tener al menos dos años con datos para comparar.")

    st.subheader("Estacionalidad", help="Gasto normal de cada categoría en cada mes. Cuanto más oscuro, más gasto.")
    hm = normal_f.pivot_table(index="categoria", columns="mes", values="importe_imputado", aggfunc="sum", fill_value=0) * -1
    hm = hm.reindex(cat.index[::-1]).fillna(0) if len(cat) else hm
    fig = go.Figure(go.Heatmap(z=hm.clip(lower=0).values, x=[mes_lab(c) for c in hm.columns], y=hm.index, colorscale=[[0, "rgba(108,142,191,0.06)"], [1, C["azul"]]],
                               hovertemplate="%{y} · %{x}: %{z:,.0f} €<extra></extra>", showscale=False, xgap=2, ygap=2))
    _ = fig.update_layout(height=40 + 28 * len(hm), margin=dict(t=10, b=10, l=10, r=10), separators=",.",
                      font=dict(family="Inter, sans-serif", size=12, color=TXT), plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, width="stretch", key="g5")

    st.subheader("Movimientos")
    f1, f2 = st.columns([2, 1])
    q = f1.text_input("Buscar", placeholder="Comercio, categoría, nota…", label_visibility="collapsed")
    solo_gasto = f2.toggle("Solo gastos", value=False)
    t = m_all.copy()
    if solo_gasto:
        t = t[t["flujo"] == "Gasto"]
    if q:
        t = t[t[["concepto", "categoria", "subcategoria", "comercio", "nota", "cuenta"]].apply(lambda r: q.lower() in " ".join(r).lower(), axis=1)]
    t = t.sort_values("fecha", ascending=False)
    t["fecha"] = pd.to_datetime(t["fecha"], errors="coerce")
    st.dataframe(t[["fecha", "cuenta", "concepto", "categoria", "subcategoria", "importe", "importe_imputado", "nota"]], hide_index=True,
                 width="stretch", height=440, column_config={
                     "fecha": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY"),
                     "cuenta": "Cuenta", "concepto": "Concepto", "categoria": "Categoría", "subcategoria": "Subcategoría",
                     "importe": st.column_config.NumberColumn("Importe (€)", format="localized"),
                     "importe_imputado": st.column_config.NumberColumn("Tu parte (€)", format="localized",
                                                                        help="Lo que te cuenta como gasto: la conjunta al 50 %, las compras repuestas al 0 %."),
                     "nota": "Nota"})

    st.subheader("Extraordinarios del periodo", help="Gastos puntuales que no cuentan en el gasto normal.")
    ex = gasto[gasto["tipo_gasto"] == "Extraordinario"].groupby("categoria", as_index=False)["importe_imputado"].sum()
    tabla(ex.assign(Total=-ex["importe_imputado"]).rename(columns={"categoria": "Categoría"})[["Categoría", "Total"]], euros=["Total"])

# ------------------------------------------------------------------ Desglose
with T["desglose"]:
    principales = ["Restauración y delivery", "Videojuegos y ocio digital", "Ropa y calzado", "Supermercado y alimentación", "Compras y tecnología"]
    todas_cat = (normal_f.groupby("categoria")["importe_imputado"].sum().mul(-1).sort_values(ascending=False))
    opciones = [c for c in principales if c in todas_cat.index] + [c for c in todas_cat.index if c not in principales and todas_cat[c] > 0]
    elegida = st.selectbox("Categoría", opciones, index=0 if opciones else None, help="Cualquier categoría de gasto; las más habituales, primero.")
    d = normal_f[normal_f["categoria"] == elegida]
    media_cat_p = -d["importe_imputado"].sum() / n_meses
    media_cat_r = -d[d["mes"].isin(recientes)]["importe_imputado"].sum() / n_rec
    k = st.columns(4)
    k[0].metric(f"{elegida} / mes", eur(media_cat_p), border=True, help="Media mensual del periodo, tu parte.")
    k[1].metric("Peso en tu gasto normal", pct(media_cat_p / gas_mes) if gas_mes else "—", border=True, help="Media de la categoría ÷ gasto normal mensual.")
    k[2].metric("Últimos meses", eur(media_cat_r), f"{eur_delta(media_cat_r - media_cat_p)} vs media", delta_color="inverse", border=True,
                help="Media de los meses recientes (barra lateral) frente a la del periodo.")
    k[3].metric("Cargos / mes", f"{(d['importe_imputado'] < 0).sum() / n_meses:.1f}".replace(".", ","), border=True,
                help="Número medio de cargos al mes en la categoría.")
    clave = d["subcategoria"].replace("", "Sin subcategoría")
    sub = pd.DataFrame({
        "Media del periodo": d.groupby(clave)["importe_imputado"].sum().mul(-1) / n_meses,
        "Media reciente": d[d["mes"].isin(recientes)].groupby(clave[d["mes"].isin(recientes)])["importe_imputado"].sum().mul(-1) / n_rec,
    }).fillna(0).sort_values("Media del periodo")
    a_, b_ = st.columns([3, 2])
    with a_:
        st.subheader("En qué se reparte")
        fig = go.Figure()
        for col, color in zip(sub.columns, (C["azul"], C["arena"])):
            fig.add_bar(y=sub.index, x=sub[col], name=col, orientation="h", marker_color=color, hovertemplate="%{x:,.0f} €/mes")
        _ = fig.update_layout(barmode="group")
        st.plotly_chart(estilo(fig, 100 + 44 * len(sub), euros_en="x"), width="stretch", key="des_sub")
    with b_:
        st.subheader("Dónde gastas más", help="Total del periodo, tu parte. Ticket medio = total ÷ número de cargos. "
                     "Las variantes de un mismo comercio se unen (p. ej. «Spotify P47D…», «Spotifyes»).")
        com = d.assign(cargo=d["importe_imputado"] < 0).groupby(unificar_comercios(d["comercio"].where(d["comercio"] != "", d["concepto"]))).agg(
            Cargos=("cargo", "sum"), Total=("importe_imputado", lambda s: -s.sum())).sort_values("Total", ascending=False).head(12)
        com["Ticket medio"] = com["Total"] / com["Cargos"].replace(0, np.nan)
        tabla(com.reset_index().rename(columns={"comercio": "Comercio"}), euros=["Total", "Ticket medio"])
    st.subheader("Mes a mes", help="Gasto de la categoría cada mes, por subcategoría.")
    gm = d.assign(sub=clave).pivot_table(index="mes", columns="sub", values="importe_imputado", aggfunc="sum", fill_value=0) * -1
    gm = gm.reindex(periodo).fillna(0)
    fig = go.Figure()
    for col, color in zip(gm.sum().sort_values(ascending=False).index, TONOS_VERDE + PALETA):
        fig.add_bar(x=[mes_lab(i) for i in gm.index], y=gm[col], name=col, marker_color=color, hovertemplate="%{y:,.0f} €")
    _ = fig.update_layout(barmode="stack", bargap=0.35)
    st.plotly_chart(estilo(fig, 340), width="stretch", key="des_mes")

# ------------------------------------------------------------------ Patrimonio
with T["patrimonio"]:
    p = pestana("patrimonio").copy()
    det = pestana("patrimonio_detalle").copy()
    cats = pestana("patrimonio_categorias").copy()
    if p.empty:
        st.info("Aún no hay datos de patrimonio.")
    else:
        p["valor"], p["aportado"] = num(p["valor"]), num(p["aportado"])
        liquidez_cuentas = p.loc[p["plataforma"] == "Cuentas", "valor"].sum()
        if not det.empty:
            for c_ in ("valor", "coste", "cantidad"):
                det[c_] = num(det[c_])
        if not cats.empty:
            for c_ in ("valor", "coste_fiscal", "aportado", "valor_compra"):
                cats[c_] = num(cats[c_]) if c_ in cats.columns else np.nan
        if cats_pat:  # filtro «Tipos de activo» de la barra lateral
            det = det[det["categoria"].isin(cats_pat)] if not det.empty else det
            cats = cats[cats["categoria"].isin(cats_pat)] if not cats.empty else cats
        total = det["valor"].sum() if not det.empty else p["valor"].sum()
        inv = cats[~cats["categoria"].eq("Efectivo en cuentas")] if not cats.empty else cats
        k = st.columns(4)
        k[0].metric("Patrimonio total", eur(total), border=True, help="Cuentas (la conjunta al 50 %), fondos, plan de pensiones, ETF y cripto, a la última valoración de cada fuente.")
        if not inv.empty:
            k[1].metric("Invertido", eur(inv["valor"].sum()), border=True, help="Todo menos el efectivo en cuentas.")
            con_coste = inv[inv["coste_fiscal"].notna() & ~inv["categoria"].str.startswith("Plan de pensiones")]
            pl = con_coste["valor"].sum() - con_coste["coste_fiscal"].sum()
            k[2].metric("Plusvalía latente", eur(pl), pct(pl / con_coste["coste_fiscal"].sum()) if con_coste["coste_fiscal"].sum() else None, border=True,
                        help="Valor − coste fiscal de lo que tiene coste conocido (sin el plan de pensiones). Detalle en la pestaña Fiscal.")
        k[3].metric("Colchón de seguridad", f"{liquidez_cuentas / gas_mes:.1f} meses".replace(".", ",") if gas_mes else "—", border=True,
                    help=f"Saldo de tus cuentas ({eur(liquidez_cuentas)}, la conjunta al 50 %, sin fondos ni cripto) ÷ gasto normal mensual. "
                         "Lo habitual es tener entre 3 y 6 meses.")

        st.subheader("Por categoría", help="Todo tu patrimonio, efectivo incluido. El detalle de lo invertido está en la pestaña Inversiones.")
        if not cats.empty:
            base_v = cats.sort_values("valor", ascending=False)
            tot_v = base_v["valor"].sum()
            a_, b_ = st.columns([2, 3])
            with a_:
                peq = base_v[base_v["valor"] / tot_v < 0.01]
                gran = base_v[base_v["valor"] / tot_v >= 0.01]
                labels, values = list(gran["categoria"]), list(gran["valor"])
                hover = [f"{n}: {eur(v)} ({pct(v / tot_v)})" for n, v in zip(gran["categoria"], gran["valor"])]
                if len(peq):
                    labels.append(f"Otros ({len(peq)})")
                    values.append(peq["valor"].sum())
                    hover.append("<br>".join(f"{n}: {eur(v)} ({pct(v / tot_v, 2)})" for n, v in zip(peq["categoria"], peq["valor"])))
                fig = go.Figure(go.Pie(labels=labels, values=values, hole=0.6, marker_colors=PALETA * 4, sort=False, customdata=hover,
                                       hovertemplate="%{customdata}<extra></extra>", texttemplate="%{percent:.0%}", textposition="inside",
                                       insidetextorientation="horizontal"))
                st.plotly_chart(estilo(fig, 340), width="stretch", key="pat_pie")
            with b_:
                t_ = base_v.assign(plusvalia=base_v["valor"] - base_v["coste_fiscal"], peso=base_v["valor"] / tot_v)
                st.dataframe(t_[["categoria", "valor", "peso", "coste_fiscal", "plusvalia"]], hide_index=True, width="stretch", height=340, column_config={
                    "categoria": "Categoría", "valor": st.column_config.NumberColumn("Valor (€)", format="localized"),
                    "peso": st.column_config.NumberColumn("Peso", format="percent"),
                    "coste_fiscal": st.column_config.NumberColumn("Coste fiscal (€)", format="localized",
                                                                  help="Lo que te costó a efectos fiscales (incluye lo que arrastran los traspasos)."),
                    "plusvalia": st.column_config.NumberColumn("Plusvalía (€)", format="localized")})

        # ---------- Evolución: capital aportado desde el primer dato + valor de cada día con foto
        st.subheader("Evolución", help="Línea: capital neto invertido (compras − ventas de fondos, ETF y cripto según Portfolio Performance y, "
                     "después, las fuentes oficiales). Baja cuando sacas dinero de las inversiones a tus cuentas: Áreas: valor del patrimonio en los días en que hay foto (desde que funciona el pipeline). "
                     "Hasta el último mes reconstruido, el valor sale de scripts/reconstruir_historico.py (mes a mes, con precios históricos); después, de las fotos diarias.")
        fig = go.Figure()
        ops_e = pestana("pp_operaciones").copy()
        if ops_e.empty:  # sin Portfolio Performance: solo las aportaciones de los movimientos
            ops_e = pd.DataFrame({"fecha": [pd.Timestamp("1900-01-01")], "activo": ["-"], "tipo": ["-"], "importe": [0.0]})
        if True:
            ops_e["importe"], ops_e["fecha"] = num(ops_e["importe"]), pd.to_datetime(ops_e["fecha"], errors="coerce")
            sig = {"compra": 1, "venta": -1}
            es_f = ops_e["activo"].str.match(r"^([A-Z]{2}[A-Z0-9]{9}\d|0P|N\d{4}$)")
            ops_e["cat"] = np.where(es_f, "Fondos y ETF", "Cripto")
            corte_pp = ops_e["fecha"].max()
            ops_e = ops_e[ops_e["tipo"].isin(sig)]
            extra = m_all[(m_all["flujo"] == "Ahorro / inversión") & (pd.to_datetime(m_all["fecha"]) > corte_pp)]
            ap = pd.concat([ops_e.assign(flujo=ops_e["tipo"].map(sig).astype(float) * ops_e["importe"].abs())[["fecha", "cat", "flujo"]],
                            pd.DataFrame({"fecha": pd.to_datetime(extra["fecha"]), "cat": "Fondos y ETF", "flujo": -extra["importe"]})])
            if cats_pat:
                quiere = {"Cripto"} if set(cats_pat) <= {"Cripto"} else ({"Fondos y ETF"} if "Cripto" not in cats_pat else {"Fondos y ETF", "Cripto"})
                ap = ap[ap["cat"].isin(quiere)]
            serie = ap.set_index("fecha").sort_index()["flujo"].resample("ME").sum().cumsum()
            fig.add_scatter(x=serie.index, y=serie.values, name="Capital neto invertido", mode="lines", line=dict(color=C["arena"], width=2, shape="hv"),
                            hovertemplate="%{x|%b %Y}: %{y:,.0f} €")
        h = pestana("patrimonio_categorias_historico").copy()
        rec = pestana("patrimonio_historico_reconstruido").copy()
        partes = []
        if not rec.empty:  # meses anteriores, reconstruidos con precios históricos
            rec = rec.rename(columns={"mes": "fecha"})
            partes.append(rec[["fecha", "categoria", "valor"]])
        if not h.empty:    # fotos diarias del pipeline: solo después del último mes reconstruido
            h_ = h[["fecha", "categoria", "valor"]]
            if partes:
                # La reconstrucción manda hasta su última fecha: así, las fotos de días en que algún
                # cálculo estaba mal (y que quedan guardadas tal cual) no crean saltos falsos.
                fin_rec = pd.to_datetime(partes[0]["fecha"]).max()
                h_ = h_[pd.to_datetime(h_["fecha"]) > fin_rec]
            partes.append(h_)
        if partes:
            h = pd.concat(partes, ignore_index=True)
            h["valor"], h["fecha"] = num(h["valor"]), pd.to_datetime(h["fecha"], errors="coerce")
            if cats_pat:
                h = h[h["categoria"].isin(cats_pat)]
            ev = h.groupby(["fecha", "categoria"], as_index=False)["valor"].sum()
            for (cat_, d_), color in zip(ev.groupby("categoria"), PALETA):
                fig.add_scatter(x=d_["fecha"], y=d_["valor"], name=cat_, stackgroup="1", mode="lines",
                                line=dict(color=color, width=1), hovertemplate="%{y:,.0f} €")
        if fig.data:
            st.plotly_chart(estilo(fig, 380), width="stretch", key="pat_evol")

        # ---------- Reparto del patrimonio
        cfg_r = _costes().get("reparto", {})
        if not det.empty and cfg_r:
            st.subheader("Reparto por tipo de activo", help="Clase de cada inversión según `config/costes.yaml` → reparto (el ETC de oro aparte de la renta variable). "
                         "El objetivo también se define ahí.")
            dr = det.assign(clase=lambda x: [cfg_r.get("isin", {}).get(str(a).upper()) or cfg_r.get("clases", {}).get(c, "Otros")
                                             for a, c in zip(x["activo"], x["categoria"])])
            dr.loc[(dr["categoria"] == "Cripto") & (dr["activo"] == "EUR"), "clase"] = "Efectivo"
            rep = dr.groupby("clase")["valor"].sum()
            # Objetivo: pestaña «reparto_objetivo» de la hoja (se edita aquí mismo); si no existe, el de costes.yaml.
            obj_h = pestana("reparto_objetivo")
            if not obj_h.empty and {"clase", "objetivo"} <= set(obj_h.columns):
                obj = pd.Series(num(obj_h["objetivo"]).values, index=obj_h["clase"]).dropna() / 100
            else:
                obj = pd.Series(cfg_r.get("objetivo", {}), dtype=float) / 100
            with st.expander("Editar tu objetivo de reparto"):
                clases = sorted(set(rep.index) | set(obj.index) | {"Renta variable", "Renta fija", "Efectivo", "Cripto", "Oro"})
                ed = st.data_editor(pd.DataFrame({"clase": clases, "objetivo": [round(float(obj.get(c_, 0)) * 100, 1) for c_ in clases]}),
                                    hide_index=True, width="stretch", key="ed_objetivo", num_rows="dynamic", column_config={
                                        "clase": st.column_config.TextColumn("Clase"),
                                        "objetivo": st.column_config.NumberColumn("Objetivo (%)", min_value=0.0, max_value=100.0, step=1.0)})
                suma = float(pd.to_numeric(ed["objetivo"], errors="coerce").fillna(0).sum())
                c_a, c_b = st.columns([3, 1])
                c_a.caption(f"Suma: {str(round(suma, 1)).replace('.', ',')} %" + ("" if abs(suma - 100) < 0.05 else " — tiene que sumar 100 %"))
                if c_b.button("Guardar", disabled=abs(suma - 100) >= 0.05, width="stretch"):
                    guardar_pestana("reparto_objetivo", ed[pd.to_numeric(ed["objetivo"], errors="coerce").fillna(0) > 0])
                    st.rerun()
            tot_r = rep.sum()
            tab_r = pd.DataFrame({"Valor": rep, "Actual": rep / tot_r}).join(obj.rename("Objetivo"), how="outer").fillna(0)
            tab_r["Desviación"] = tab_r["Actual"] - tab_r["Objetivo"]
            tab_r["Para volver al objetivo (€)"] = tab_r["Objetivo"] * tot_r - tab_r["Valor"]
            a_, b_ = st.columns([3, 2])
            with a_:
                fig = go.Figure()
                fig.add_bar(y=tab_r.index, x=tab_r["Actual"], name="Actual", orientation="h", marker_color=C["azul"], hovertemplate="%{y}: %{x:.1%}<extra></extra>")
                fig.add_bar(y=tab_r.index, x=tab_r["Objetivo"], name="Objetivo", orientation="h", marker_color=C["arena"], hovertemplate="%{y}: %{x:.1%}<extra></extra>")
                _ = fig.update_layout(barmode="group")
                fig = estilo(fig, 80 + 50 * len(tab_r), euros_en="x")
                _ = fig.update_xaxes(tickformat=".0%", ticksuffix="")
                st.plotly_chart(fig, width="stretch", key="reparto")
            with b_:
                st.dataframe(tab_r.reset_index().rename(columns={"index": "Clase", "clase": "Clase"}), hide_index=True, width="stretch", column_config={
                    "Valor": st.column_config.NumberColumn("Valor (€)", format="localized"),
                    "Actual": st.column_config.NumberColumn(format="percent"), "Objetivo": st.column_config.NumberColumn(format="percent"),
                    "Desviación": st.column_config.NumberColumn(format="percent"),
                    "Para volver al objetivo (€)": st.column_config.NumberColumn(format="localized",
                                                                                help="Positivo: cuánto añadir a esa clase; negativo: cuánto le sobra. Con aportaciones nuevas se corrige sin vender.")})

            # ---------- Proyección
            st.subheader("Proyección", help="Cómo podría evolucionar tu patrimonio con tus aportaciones y la rentabilidad supuesta de cada clase (en `costes.yaml`). "
                         "La banda muestra ±2 puntos de rentabilidad. Son supuestos, no una previsión.")
            rent_cl = pd.Series(cfg_r.get("rentabilidad", {}), dtype=float) / 100
            peso = rep / tot_r
            r_media = float((peso * rent_cl.reindex(peso.index).fillna(0)).sum())
            inv_mes = -m[m["flujo"] == "Ahorro / inversión"]["importe"].sum() / n_meses
            ahorro_mes = max(ing_mes - gas_mes, 0)
            c1, c2, c3, c4 = st.columns(4)
            aport = c1.number_input("Aportación al mes (€)", 0, 20_000, int(round(ahorro_mes, -1)), 50,
                                    help=f"Por defecto, tu margen de ahorro medio ({eur(ahorro_mes)}/mes). Lo que inviertes hoy de forma explícita: {eur(inv_mes)}/mes.")
            r_sup = c2.number_input("Rentabilidad anual (%)", -5.0, 15.0, round(r_media * 100, 1), 0.5,
                                    help="Por defecto, la media de tus clases ponderada por tu reparto actual.") / 100
            anos_p = c3.select_slider("Años", [5, 10, 15, 20, 25, 30], value=15, key="anos_proy")
            con_casa = c4.toggle("Con la compra de vivienda", value=False,
                                 help="Usa los números de la pestaña Vivienda: la aportación inicial sale del patrimonio y la aportación mensual baja "
                                      "lo que suba tu coste de vivienda.")
            infl = cfg_r.get("inflacion", 2.5) / 100
            ini = tot_r
            if con_casa:
                ini -= st.session_state.get("viv_aportacion", 0.0)
                aport = max(aport - st.session_state.get("viv_delta_mes", 0.0), 0)
                if "viv_aportacion" not in st.session_state:
                    st.caption("Abre una vez la pestaña Vivienda para que la proyección use sus números.")
            meses_p = np.arange(0, anos_p * 12 + 1)
            fechas_p = [hoy + pd.DateOffset(months=int(k_)) for k_ in meses_p]

            def trayectoria(r_):
                i_ = (1 + r_) ** (1 / 12) - 1
                v, out = ini, []
                for _ in meses_p:
                    out.append(v)
                    v = v * (1 + i_) + aport
                return np.array(out)
            base_t, baja, alta = trayectoria(r_sup), trayectoria(r_sup - 0.02), trayectoria(r_sup + 0.02)
            real = base_t / (1 + infl) ** (meses_p / 12)
            fig = go.Figure()
            fig.add_scatter(x=fechas_p, y=alta, line=dict(width=0), showlegend=False, hoverinfo="skip")
            fig.add_scatter(x=fechas_p, y=baja, fill="tonexty", fillcolor="rgba(108,142,191,0.18)", line=dict(width=0), name="±2 puntos", hoverinfo="skip")
            fig.add_scatter(x=fechas_p, y=base_t, name="Valor nominal", line=dict(color=C["azul"], width=3), hovertemplate="%{x|%b %Y}: %{y:,.0f} €")
            fig.add_scatter(x=fechas_p, y=real, name="En euros de hoy", line=dict(color=C["arena"], width=2, dash="dash"), hovertemplate="%{x|%b %Y}: %{y:,.0f} €")
            st.plotly_chart(estilo(fig, 360), width="stretch", key="proyeccion")
            aportado_tot = ini + aport * anos_p * 12
            k = st.columns(4)
            k[0].metric(f"Dentro de {anos_p} años", eur(base_t[-1]), border=True)
            k[1].metric("En euros de hoy", eur(real[-1]), border=True, help=f"Descontando una inflación del {str(cfg_r.get('inflacion', 2.5)).replace('.', ',')} % anual.")
            k[2].metric("De eso, aportado por ti", eur(aportado_tot), border=True, help="Patrimonio de hoy + aportaciones mensuales.")
            k[3].metric("Horquilla", f"{eur(baja[-1])} – {eur(alta[-1])}", border=True, help="Con 2 puntos menos y 2 más de rentabilidad anual.")

        # ---------- Efectivo
        cfg_c = _costes()
        det_c = pestana("patrimonio_detalle").copy()
        det_c["valor"] = num(det_c["valor"]) if not det_c.empty else det_c.get("valor")
        if not det_c.empty:
            # ---------- 2. Efectivo que no rinde
            st.subheader("Efectivo que no rinde", help="Saldo de cada cuenta (la conjunta, tu mitad), su remuneración y lo que dejarías de ganar al año "
                         "por el dinero en cuentas al 0 % por encima de lo que necesitas para el día a día.")
            cu = det_c[det_c["categoria"] == "Efectivo en cuentas"][["activo", "nombre", "valor"]].copy()
            nombres_cta = {c_["id"]: c_.get("nombre", c_["id"]) for c_ in _cuentas_cfg().get("cuentas", [])}
            cu["nombre"] = cu["activo"].map(nombres_cta).fillna(cu["nombre"])
            rem = cfg_c.get("remuneracion", {})
            # Interés real de Trade Republic: lo cobrado en los últimos meses completos ÷ saldo
            intereses = m_all[(m_all["cuenta"] == "TR") & m_all["concepto"].str.contains("Interest|Intereses", case=False, na=False) & (m_all["importe"] > 0)]
            ult3 = [x for x in completos[-3:]]
            tr_real = None
            saldo_tr = cu.loc[cu["activo"] == "TR", "valor"].sum()
            if len(intereses) and saldo_tr > 0:
                int_mes = intereses[intereses["mes"].isin(ult3)]["importe"].sum() / max(len(ult3), 1)
                tr_real = int_mes * 12 / saldo_tr * 100 if int_mes else None
            cu["Remuneración (%)"] = cu["activo"].map(lambda a: rem.get(a, 0.0))
            if tr_real:
                cu.loc[cu["activo"] == "TR", "Remuneración (%)"] = round(tr_real, 2)
            cu["Intereses / año"] = cu["valor"] * cu["Remuneración (%)"] / 100
            alt = cfg_c.get("mejor_alternativa", 0.0)
            operativo = gas_mes * 1  # un mes de gasto en la cuenta del día a día
            sin_rem = cu[cu["Remuneración (%)"] <= 0.01]["valor"].sum()
            ocioso = max(sin_rem - operativo, 0)
            k = st.columns(4)
            k[0].metric("Efectivo total", eur(cu["valor"].sum()), border=True)
            k[1].metric("Colchón recomendado", eur(gas_mes * cfg_c.get("meses_colchon", 3)), border=True,
                        help=f"{cfg_c.get('meses_colchon', 3)} meses de gasto normal. Puede estar en una cuenta remunerada: no necesita estar al 0 %.")
            k[2].metric("En cuentas al 0 %", eur(sin_rem), f"{eur(ocioso)} por encima de un mes de gasto", delta_color="off", border=True)
            k[3].metric("Dejas de ganar al año", eur(ocioso * alt / 100), border=True,
                        help=f"Lo que darían esos {eur(ocioso)} al {str(alt).replace('.', ',')} % (la mejor remuneración sin condiciones que tienes, en `costes.yaml`).")
            st.dataframe(cu.rename(columns={"activo": "Cuenta", "nombre": "Nombre", "valor": "Saldo"}), hide_index=True, width="stretch", column_config={
                "Saldo": st.column_config.NumberColumn("Saldo (€)", format="localized"),
                "Intereses / año": st.column_config.NumberColumn("Intereses al año (€)", format="localized")})
            if tr_real:
                st.caption(f"Trade Republic: remuneración calculada con los intereses que has cobrado de verdad en los últimos 3 meses ({str(round(tr_real, 2)).replace('.', ',')} %). "
                           "Un saldo que ha cambiado mucho en ese tiempo puede desviarla.")

# ------------------------------------------------------------------ Inversiones
with T["inversiones"]:
    det = pestana("patrimonio_detalle").copy()
    cats = pestana("patrimonio_categorias").copy()
    if det.empty or cats.empty:
        st.info("Ejecuta el pipeline para tener el detalle de tus inversiones.")
    else:
        for c_ in ("valor", "coste", "cantidad"):
            det[c_] = num(det[c_])
        for c_ in ("valor", "coste_fiscal", "aportado", "valor_compra"):
            cats[c_] = num(cats[c_]) if c_ in cats.columns else np.nan
        if cats_pat:
            det, cats = det[det["categoria"].isin(cats_pat)], cats[cats["categoria"].isin(cats_pat)]
        # Lo invertido: todo menos el efectivo en cuentas (el efectivo de Indexa o Kraken sí está dentro de su inversión).
        det = det[det["categoria"] != "Efectivo en cuentas"]
        cats = cats[cats["categoria"] != "Efectivo en cuentas"]
        cfg_c = _costes()
        det_c = det.copy()  # para «Lo que te cuestan tus inversiones»
        con_c = cats[cats["coste_fiscal"].notna() & ~cats["categoria"].str.startswith("Plan de pensiones")]
        pl_i = con_c["valor"].sum() - con_c["coste_fiscal"].sum()
        k = st.columns(3)
        k[0].metric("Invertido hoy", eur(cats["valor"].sum()), border=True, help="Fondos, plan de pensiones, ETF, acciones y cripto, a su última valoración.")
        k[1].metric("Plusvalía latente", eur(pl_i), pct(pl_i / con_c["coste_fiscal"].sum()) if con_c["coste_fiscal"].sum() else None, border=True,
                    help="Valor − coste fiscal (sin el plan de pensiones). Detalle fiscal en la pestaña Fiscal.")
        k[2].metric("Peso en tu patrimonio", pct(cats["valor"].sum() / num(pestana("patrimonio_categorias")["valor"]).sum()), border=True)
        # ---------- Composición y resultado de un vistazo (mapa de bloques)
        st.subheader("Composición y resultado", help="Cada bloque es un activo, agrupado por categoría. El tamaño es lo que vale hoy; el color, "
                     "cuánto gana o pierde sobre su coste fiscal (verde: gana; rojo: pierde). Pasa el ratón para ver las cifras.")
        tm = det[det["valor"].fillna(0) > 0.5].copy()
        tm["etiqueta"] = np.where(tm["activo"].eq("EUR"), tm["nombre"], tm["nombre"].fillna(tm["activo"]))
        tm["plusv"] = np.where(tm["coste"] > 0, tm["valor"] / tm["coste"] - 1, np.nan)
        cat_v = tm.groupby("categoria").agg(valor=("valor", "sum"), coste=("coste", lambda x: x.sum(min_count=1)))
        ids_t = list(cat_v.index) + list(tm["categoria"] + "/" + tm["etiqueta"])
        labels_t = list(cat_v.index) + list(tm["etiqueta"])
        parents_t = [""] * len(cat_v) + list(tm["categoria"])
        values_t = list(cat_v["valor"]) + list(tm["valor"])
        colores_t = list(np.where(cat_v["coste"] > 0, cat_v["valor"] / cat_v["coste"] - 1, 0)) + list(tm["plusv"].fillna(0))
        hover_t = [f"{c_}<br>{eur(v_)}" + (f" · coste {eur(k_)} · {pct(v_ / k_ - 1)}" if k_ and k_ > 0 else "")
                   for c_, v_, k_ in zip(cat_v.index, cat_v["valor"], cat_v["coste"])]
        hover_t += [f"{e_}<br>{eur(v_)}" + (f" · coste {eur(k_)} · {pct(v_ / k_ - 1)}" if pd.notna(k_) and k_ > 0 else "")
                    for e_, v_, k_ in zip(tm["etiqueta"], tm["valor"], tm["coste"])]
        fig = go.Figure(go.Treemap(ids=ids_t, labels=labels_t, parents=parents_t, values=values_t, branchvalues="total",
                                   marker=dict(colors=colores_t, colorscale=[[0, C["rojo"]], [0.5, "rgba(140,149,163,0.35)"], [1, C["verde"]]],
                                               cmid=0, cmin=-0.5, cmax=0.5, line=dict(width=1, color=TOOLTIP_BG)),
                                   customdata=hover_t, hovertemplate="%{customdata}<extra></extra>", texttemplate="%{label}<br>%{value:,.0f} €",
                                   tiling=dict(pad=2)))
        fig.update_layout(height=420, margin=dict(t=10, b=10, l=10, r=10), separators=",.", paper_bgcolor="rgba(0,0,0,0)",
                          font=dict(family="Inter, sans-serif", size=12, color=TXT))
        st.plotly_chart(fig, width="stretch", key="inv_treemap")
        st.caption("Color: plusvalía sobre el coste fiscal, de −50 % (rojo) a +50 % (verde) o más.")

        # ---------- Rentabilidad
        st.subheader("Rentabilidad", help="Valor de compra: lo que costó lo que tienes en la plataforma actual (sus compras + el valor con que llegaron los traspasos). "
                     "Aportación real: el dinero que salió de tu bolsillo, incluido lo aportado antes de traspasar (el coste fiscal). "
                     "Ganancia total = valor actual − aportación real. "
                     "Rentabilidad total: ganancia ÷ aportación real. TIR anual: rentabilidad por año teniendo en cuenta cuándo pusiste cada euro.")
        ops_r = pestana("pp_operaciones").copy()
        ops_r_todas = ops_r.copy()
        rx = pestana("cripto_operaciones").copy()
        trv = pestana("tr_valores").copy()
        PATRON_FONDO = r"^([A-Z]{2}[A-Z0-9]{9}\d|0P|N\d{4}$)"
        flujos_por: dict[str, list] = {}
        if not ops_r.empty:
            ops_r["importe"], ops_r["fecha"] = num(ops_r["importe"]), pd.to_datetime(ops_r["fecha"], errors="coerce")
            ops_r = ops_r[ops_r["tipo"].isin(["compra", "venta"])]  # los traspasos no son dinero tuyo que entra o sale
            ops_r["flujo"] = np.where(ops_r["tipo"] == "compra", -1, 1) * ops_r["importe"].abs()
            fondo = ops_r["activo"].str.match(PATRON_FONDO)
            plan = ops_r["activo"].str.startswith(("0P", "N5"))
            grupos = {"Fondos indexados (Sabadell → Indexa → MyInvestor)": fondo & ~plan & ops_r["cuenta_pp"].isin(["Sabadell", "Indexa", "MyInvestor"]),
                      "Plan de pensiones Indexa": plan, "Cripto": ~fondo}
            for g_, mask in grupos.items():
                flujos_por[g_] = list(zip(ops_r.loc[mask, "fecha"], ops_r.loc[mask, "flujo"]))
            ultima_pp = ops_r["fecha"].max()
            sub = m_all[(m_all["cuenta"] == "MYINV") & (m_all["flujo"] == "Ahorro / inversión") & (pd.to_datetime(m_all["fecha"]) > ultima_pp)]
            flujos_por["Fondos indexados (Sabadell → Indexa → MyInvestor)"] += list(zip(pd.to_datetime(sub["fecha"]), sub["importe"]))
            if not rx.empty:
                r_ = rx.assign(fecha=pd.to_datetime(rx["fecha"]), importe=num(rx["importe"]))
                r_ = r_[(r_["fecha"] > ultima_pp) & r_["tipo"].isin(["compra", "venta"]) & r_["importe"].notna()]
                flujos_por["Cripto"] += list(zip(r_["fecha"], np.where(r_["tipo"] == "compra", -r_["importe"], r_["importe"])))
        else:  # sin Portfolio Performance: las suscripciones de MyInvestor de los movimientos
            sub = m_all[(m_all["cuenta"] == "MYINV") & (m_all["flujo"] == "Ahorro / inversión")]
            if len(sub):
                flujos_por["Fondos MyInvestor"] = list(zip(pd.to_datetime(sub["fecha"]), sub["importe"]))
        if not trv.empty:
            trv["importe"], trv["cantidad"] = num(trv["importe"]), num(trv["cantidad"])
            t_ = trv[trv["tipo"].isin(["BUY", "SELL"])]
            flujos_por["ETF y acciones Trade Republic"] = list(zip(pd.to_datetime(t_["fecha"]), np.where(t_["cantidad"] > 0, -t_["importe"], t_["importe"])))

        if cats.empty:
            st.info("Ejecuta el pipeline para ver la rentabilidad.")
        else:
            c_ = cats.set_index("categoria")
            val = lambda k: float(c_.loc[k, "valor"]) if k in c_.index else 0.0  # noqa: E731
            fis = lambda k: float(c_.loc[k, "coste_fiscal"]) if k in c_.index and pd.notna(c_.loc[k, "coste_fiscal"]) else np.nan  # noqa: E731
            apo = lambda k: float(c_.loc[k, "valor_compra"]) if k in c_.index and "valor_compra" in c_.columns and pd.notna(c_.loc[k, "valor_compra"]) else np.nan  # noqa: E731
            filas = []
            for k in ("Fondos Indexa", "Fondos MyInvestor"):
                if k in c_.index:
                    f_k = flujos_por.get(k, [])
                    filas.append({"Inversión": k, "Valor actual": val(k), "Valor de compra": apo(k), "Aportación real": fis(k),
                                  "TIR anual": xirr(f_k + [(hoy, val(k))]) if f_k else np.nan})
            g_f = "Fondos indexados (Sabadell → Indexa → MyInvestor)"
            v_f = val("Fondos Indexa") + val("Fondos MyInvestor")
            if {"Fondos Indexa", "Fondos MyInvestor"} <= set(c_.index):  # el total solo tiene sentido con las dos
                filas.append({"Inversión": "Total fondos indexados", "Valor actual": v_f, "Valor de compra": apo("Fondos Indexa") + apo("Fondos MyInvestor"),
                              "Aportación real": fis("Fondos Indexa") + fis("Fondos MyInvestor"),
                              "TIR anual": xirr(flujos_por.get(g_f, []) + [(hoy, v_f)]) if flujos_por.get(g_f) else np.nan})
            for k, g_ in (("Plan de pensiones Indexa", "Plan de pensiones Indexa"), ("ETF y acciones Trade Republic", "ETF y acciones Trade Republic"), ("Cripto", "Cripto")):
                if k not in c_.index:
                    continue
                f_ = flujos_por.get(g_, [])
                neta = -sum(v for _, v in f_) if f_ else np.nan
                real = fis(k) if k != "Cripto" else neta
                filas.append({"Inversión": k, "Valor actual": val(k), "Valor de compra": apo(k),
                              "Aportación real": real if k != "Plan de pensiones Indexa" else apo(k),
                              "TIR anual": xirr(f_ + [(hoy, val(k))]) if f_ else np.nan})
            rent = pd.DataFrame(filas)
            vista_r = st.segmented_control("Ver por", ["Categoría", "Activo"], default="Categoría", key="vista_rent", label_visibility="collapsed")
            if vista_r == "Activo":
                rent = rentabilidad_por_activo(det, ops_r_todas, rx, trv)
            # De mayor a menor valor; la fila «Total fondos indexados» va justo después de sus fondos.
            total_f = rent[rent["Inversión"] == "Total fondos indexados"] if "Inversión" in rent.columns else rent.iloc[0:0]
            resto_r = rent.drop(total_f.index).sort_values("Valor actual", ascending=False)
            if len(total_f):
                pos = max(resto_r.reset_index(drop=True).index[resto_r["Inversión"].isin(["Fondos Indexa", "Fondos MyInvestor"]).values].tolist() + [-1]) + 1
                resto_r = resto_r.reset_index(drop=True)
                rent = pd.concat([resto_r.iloc[:pos], total_f, resto_r.iloc[pos:]], ignore_index=True)
            else:
                rent = resto_r
            rent["Ganancia total"] = rent["Valor actual"] - rent["Aportación real"]
            rent["Rentabilidad total"] = np.where(rent["Aportación real"] > 0, rent["Ganancia total"] / rent["Aportación real"], np.nan)
            st.dataframe(rent[[c_ for c_ in ["Inversión", "Categoría", "Valor actual", "Valor de compra", "Aportación real", "Ganancia total",
                                             "Rentabilidad total", "TIR anual"] if c_ in rent.columns]],
                         hide_index=True, width="stretch", column_config={
                "Valor actual": st.column_config.NumberColumn("Valor actual (€)", format="localized"),
                "Valor de compra": st.column_config.NumberColumn("Valor de compra (€)", format="localized",
                                                                 help="Fondos: lo que costó en la plataforma actual (incluye el valor con que llegaron los traspasos). Cripto: coste de los lotes FIFO que te quedan."),
                "Aportación real": st.column_config.NumberColumn("Aportación real (€)", format="localized",
                                                                 help="Dinero que salió de tu bolsillo. Fondos: coste fiscal (incluye lo aportado antes de los traspasos). "
                                                                      "Cripto: euros metidos − euros sacados (negativo si has sacado más de lo que metiste)."),
                "Ganancia total": st.column_config.NumberColumn("Ganancia total (€)", format="localized"),
                "Rentabilidad total": st.column_config.NumberColumn(format="percent", help="Ganancia total ÷ aportación real. En cripto no tiene sentido si has sacado más de lo que metiste."),
                "TIR anual": st.column_config.NumberColumn(format="percent",
                                                           help="Rentabilidad anual ponderada por el dinero (XIRR). Por categoría, en fondos se calcula para el conjunto, porque los traspasos "
                                                                "mezclan la historia de Indexa y MyInvestor. Por activo, desde que el activo está en su plataforma actual "
                                                                "(un traspaso de entrada cuenta como compra a su valor de ese día).")})
            st.caption("Cuál mirar: la **rentabilidad total** dice cuánto has ganado sobre lo que pusiste; la **TIR anual** dice a qué ritmo por año, "
                       "contando cuándo entró cada euro (es la comparable con un depósito o con otra inversión).")

        # ---------- Comparar rentabilidades (y mes a mes, como en Indexa)
        st.subheader("Comparar rentabilidades", help="Rentabilidad «ponderada en el tiempo»: la de cada activo según su precio y, en plataformas y carteras, "
                     "la media de sus activos ponderada por lo que valía cada uno a final del mes anterior. No la mueven tus aportaciones ni tus "
                     "reembolsos (es como la publican los fondos e Indexa), así que sirve para comparar unas con otras. Cuánto has ganado tú, con tus "
                     "aportaciones y sus fechas, es la TIR de la tabla de arriba.")
        ph = pestana("precios_historicos").copy()
        va = pestana("patrimonio_historico_activos").copy()
        if ph.empty or va.empty:
            st.info("Aún no hay series para comparar. El pipeline las alarga solo cada mes; para rellenar el pasado, ejecuta una vez "
                    "`scripts/reconstruir_historico.py` (sube los precios y las posiciones de cada mes desde 2020).")
        else:
            ph["precio"], va["valor"] = num(ph["precio"]), num(va["valor"])
            oficial = pestana("indexa_rentabilidad")
            ph, va = RENT.con_indice_oficial(ph, va, oficial)  # Indexa: su propia rentabilidad (neta de comisiones), como en su web
            r_act = RENT.rent_activos(ph)
            det_n = pestana("patrimonio_detalle")
            nombres_a = dict(zip(det_n["activo"], det_n["nombre"])) if not det_n.empty and "nombre" in det_n.columns else {}
            if not ops_r_todas.empty and "nombre" in ops_r_todas.columns:
                alias_ = (_cuentas_cfg().get("inversion", {}) or {}).get("alias_activos") or {}
                for a_, n_ in zip(ops_r_todas["activo"].replace(alias_), ops_r_todas["nombre"]):
                    nombres_a.setdefault(a_, n_)
            aprox = set(ph.loc[ph.get("fuente", pd.Series("", index=ph.index)).astype(str).str.startswith("compras"), "activo"])
            series: dict[str, pd.Series] = {}

            def _grupo(nombre_g: str, mask) -> None:
                s_ = RENT.rent_grupo(va[mask], r_act)
                if s_.notna().sum() >= 1:
                    series[nombre_g] = s_
            cats_va = sorted(va["categoria"].dropna().unique())
            _grupo("Todo lo invertido", va["categoria"].notna())
            fondos_c = [c_ for c_ in cats_va if c_.startswith("Fondos")]
            if len(fondos_c) > 1:
                _grupo("Fondos indexados (todas las plataformas)", va["categoria"].isin(fondos_c))
            for c_ in cats_va:
                _grupo(c_, va["categoria"] == c_)
            n_carteras = len(series)
            vistos = set()
            for a_ in sorted(r_act.columns, key=lambda x: str(nombres_a.get(x, x))):
                if r_act[a_].notna().sum() < 1 or str(a_).endswith(" (oficial)"):
                    continue
                etiqueta = f"{nombres_a.get(a_) or a_} ({a_})" if nombres_a.get(a_) and nombres_a.get(a_) != a_ else str(a_)
                etiqueta += " *" if a_ in aprox else ""
                if etiqueta not in vistos:
                    vistos.add(etiqueta)
                    series[etiqueta] = r_act[a_]
            opciones = list(series)
            por_defecto = [o for o in ["Todo lo invertido", "Fondos Indexa", "Fondos MyInvestor"] if o in series] or opciones[:2]
            elegidas = st.multiselect("Qué comparar", opciones, default=por_defecto, key="cmp_series",
                                      help=f"Las {n_carteras} primeras son plataformas o carteras; después, cada activo. "
                                           "«*»: precio aproximado (sus compras interpoladas), su rentabilidad mes a mes no es real.")
            periodo = st.segmented_control("Periodo", ["Este año", "1 año", "3 años", "5 años", "Todo"], default="3 años", key="cmp_periodo",
                                           label_visibility="collapsed") or "Todo"
            if elegidas:
                fin_s = max(series[e_].last_valid_index() for e_ in elegidas)
                desde = {"Este año": pd.Timestamp(year=fin_s.year, month=1, day=31),
                         "1 año": fin_s - pd.offsets.MonthEnd(11), "3 años": fin_s - pd.offsets.MonthEnd(35),
                         "5 años": fin_s - pd.offsets.MonthEnd(59), "Todo": None}[periodo]
                fig = go.Figure()
                filas_m = []
                for i_, e_ in enumerate(elegidas):
                    r_ = RENT.recortar(series[e_], desde)
                    if r_.dropna().empty:
                        continue
                    ac = RENT.acumulada(r_.fillna(0))
                    fig.add_scatter(x=ac.index, y=ac.values, name=e_, mode="lines", line=dict(width=2.2, color=PALETA[i_ % len(PALETA)]),
                                    hovertemplate="%{y:.1%}")
                    filas_m.append({"Serie": e_, **RENT.metricas(r_)})
                estilo(fig, 380)
                fig.update_yaxes(tickformat=".0%", ticksuffix="")
                if fig.data:  # meses en castellano en el eje
                    xs = pd.DatetimeIndex(sorted({x_ for tr_ in fig.data for x_ in pd.to_datetime(tr_.x)}))
                    paso = max(1, len(xs) // 6)
                    marcas = xs[::paso]
                    fig.update_xaxes(tickvals=marcas, ticktext=[f"{RENT.MESES_ES[d_.month - 1]} {d_.year}" for d_ in marcas])
                fig.add_hline(y=0, line_color=LINEA, line_width=1)
                st.plotly_chart(fig, width="stretch", key="cmp_fig")
                if filas_m:
                    orden_m = ["Serie", "Rentabilidad", "Anualizada", "Peor caída", "Volatilidad anual", "Mejor mes", "Peor mes", "Desde", "Meses"]
                    st.dataframe(pd.DataFrame(filas_m).reindex(columns=orden_m), hide_index=True, width="stretch", column_config={
                        "Rentabilidad": st.column_config.NumberColumn("Rentabilidad del periodo", format="percent"),
                        "Anualizada": st.column_config.NumberColumn(format="percent", help="Solo con 12 meses o más."),
                        "Volatilidad anual": st.column_config.NumberColumn(format="percent", help="Cuánto se mueve: desviación típica de los meses × √12."),
                        "Peor caída": st.column_config.NumberColumn(format="percent", help="La mayor bajada desde un máximo hasta el mínimo siguiente, dentro del periodo."),
                        "Mejor mes": st.column_config.NumberColumn(format="percent"), "Peor mes": st.column_config.NumberColumn(format="percent")})
                st.caption("Con datos de fin de mes. Los traspasos y las aportaciones no cuentan como rentabilidad. Un activo que entra en la cartera "
                           "a mitad de periodo empieza a contar en su primer mes completo."
                           + (" Las cuentas de Indexa usan la rentabilidad que publica Indexa (neta de sus comisiones y con su efectivo); "
                              "sus fondos sueltos, el precio de cada uno." if not oficial.empty else ""))

            # Mes a mes, como la vista de Indexa.
            st.markdown("**Mes a mes**")
            una = st.selectbox("Serie", opciones, index=opciones.index(elegidas[0]) if elegidas else 0, key="cmp_mes_a_mes", label_visibility="collapsed")
            tab_m = RENT.tabla_mes_a_mes(RENT.recortar(series[una]))
            if not tab_m.empty:
                z = tab_m.values.astype(float)
                texto = [[("" if np.isnan(v_) else f"{v_ * 100:+.1f}".replace(".", ",")) for v_ in fila] for fila in z]
                fig = go.Figure(go.Heatmap(z=z, x=list(tab_m.columns), y=[str(a_) for a_ in tab_m.index], text=texto, texttemplate="%{text}",
                                           # Colores suaves y el número siempre en el color del texto, para que se lea en las dos apariencias.
                                           # Rojo o verde claros desde pocas décimas; ±1 % ya se nota y ±3 % es intenso (escala de −6 % a +6 %).
                                           colorscale=[[0, "rgba(220,95,95,0.90)"], [0.25, "rgba(220,95,95,0.70)"], [0.417, "rgba(220,95,95,0.45)"],
                                                       [0.49, "rgba(220,95,95,0.22)"], [0.5, "rgba(140,149,163,0.08)"], [0.51, "rgba(70,170,105,0.22)"],
                                                       [0.583, "rgba(70,170,105,0.45)"], [0.75, "rgba(70,170,105,0.70)"], [1, "rgba(70,170,105,0.90)"]],
                                           zmid=0, zmin=-0.06, zmax=0.06, textfont=dict(color=TXT, size=12),
                                           showscale=False, xgap=2, ygap=2, hovertemplate="%{y} %{x}: %{z:.2%}<extra></extra>"))
                fig.update_layout(height=60 + 34 * len(tab_m), margin=dict(t=10, b=10, l=10, r=10), paper_bgcolor="rgba(0,0,0,0)",
                                  plot_bgcolor="rgba(0,0,0,0)", font=dict(family="Inter, sans-serif", size=12, color=TXT))
                fig.update_xaxes(side="top", showgrid=False)
                fig.update_yaxes(autorange="reversed", showgrid=False, type="category")
                st.plotly_chart(fig, width="stretch", key="cmp_heat")
                st.caption("En %. La última columna es la rentabilidad de cada año (los meses encadenados); el año en curso, hasta el último mes con datos.")

        # ---------- 1. Lo que te cuestan tus inversiones
        st.caption(f"Comisiones y TER de `config/costes.yaml`, revisados el {cfg_c.get('revisado', '?')}. Compruébalos de vez en cuando en cada entidad.")
        st.subheader("Lo que te cuestan tus inversiones", help="Gestión y custodia (las cobra la plataforma) + gastos corrientes de cada fondo o ETF (TER), "
                     "en % sobre el valor y en euros al año. No incluye la cripto (sus costes están en los precios de compra y venta).")
        filas = []
        for cat_, d_ in det_c[det_c["categoria"].isin(cfg_c["categorias"].keys()) & (det_c["activo"] != "EUR")].groupby("categoria"):
            cc = cfg_c["categorias"][cat_]
            v_ = d_["valor"].sum()
            if cc.get("ter") is not None:
                ter_ = cc["ter"]
            else:
                ters = d_["activo"].map(lambda i: cfg_c["ter_isin"].get(str(i).upper()))
                ok = ters.notna()
                ter_ = float((d_.loc[ok, "valor"] * ters[ok]).sum() / d_.loc[ok, "valor"].sum()) if ok.any() else 0.0
            total_pct = cc.get("gestion", 0) + cc.get("custodia", 0) + ter_
            filas.append({"Inversión": cat_, "Valor": v_, "Gestión (%)": cc.get("gestion", 0), "Custodia (%)": cc.get("custodia", 0), "TER (%)": ter_,
                          "Total (%)": total_pct, "€ / año": v_ * total_pct / 100, "Fuente": cc.get("fuente", "")})
        cost = pd.DataFrame(filas).sort_values("€ / año", ascending=False)
        ref = cfg_c.get("referencia_bajo_coste", 0.15)
        horizonte = st.select_slider("Horizonte para ver el efecto acumulado (años)", [5, 10, 15, 20, 25, 30], value=20, key="hor_costes")
        rent_b = st.number_input("Rentabilidad bruta supuesta (% anual)", 0.0, 12.0, 6.0, 0.5, key="rent_costes") / 100
        # Efecto acumulado de una comisión f sobre un valor V: V·(1+r)^n − V·(1+r−f)^n
        cost["En " + str(horizonte) + " años"] = cost.apply(lambda r: r["Valor"] * ((1 + rent_b) ** horizonte - (1 + rent_b - r["Total (%)"] / 100) ** horizonte), axis=1)
        cost["Frente a la referencia / año"] = (cost["Total (%)"] - ref).clip(lower=0) / 100 * cost["Valor"]
        tot_v, tot_e = cost["Valor"].sum(), cost["€ / año"].sum()
        k = st.columns(4)
        k[0].metric("Coste total al año", eur(tot_e), border=True, help="Suma de lo que pagas al año en comisiones y gastos de los fondos, con el valor de hoy.")
        k[1].metric("Coste medio", pct(tot_e / tot_v, 2) if tot_v else "—", border=True, help="Ponderado por el valor de cada inversión.")
        k[2].metric(f"Efecto en {horizonte} años", eur(cost[f"En {horizonte} años"].sum()), border=True,
                    help=f"Lo que dejarían de crecer tus inversiones de hoy por los costes, con un {pct(rent_b, 1)} bruto anual y sin aportaciones nuevas.")
        k[3].metric(f"Por encima de un {str(ref).replace('.', ',')} %", eur(cost["Frente a la referencia / año"].sum()) + " / año", border=True,
                    help="Lo que pagas al año por encima de una cartera indexada equivalente montada por tu cuenta en un bróker sin custodia. "
                         "Esa alternativa no incluye el rebalanceo ni la gestión automática.")
        st.dataframe(cost, hide_index=True, width="stretch", column_config={
            "Valor": st.column_config.NumberColumn("Valor (€)", format="localized"),
            "€ / año": st.column_config.NumberColumn("Coste (€/año)", format="localized"),
            f"En {horizonte} años": st.column_config.NumberColumn(f"Efecto en {horizonte} años (€)", format="localized"),
            "Frente a la referencia / año": st.column_config.NumberColumn("Por encima de la referencia (€/año)", format="localized"),
            "Fuente": st.column_config.TextColumn(width="medium")})
        st.caption("Ten en cuenta que lo que pagas en Indexa incluye la gestión automática (rebalanceo, elección de fondos, aportaciones periódicas). "
                   "El plan de pensiones, además, tiene ventaja fiscal al aportar. Un coste más alto puede compensar si te ahorra trabajo o errores.")

        # ---------- Detalle por activo (antes en el selector)
        if not det.empty:
            st.subheader("Detalle por activo", help="Cada fondo, ETF, cripto y cuenta, con cómo se calcula su valor y su coste.")
            d_ = det[det["categoria"] != "Efectivo en cuentas"].assign(plusvalia=lambda x: x["valor"] - x["coste"]).sort_values(["categoria", "valor"], ascending=[True, False])
            st.dataframe(d_[["categoria", "plataforma", "nombre", "activo", "cantidad", "valor", "coste", "plusvalia", "fecha_valor", "fuente"]],
                         hide_index=True, width="stretch", height=460, column_config={
                "categoria": "Categoría", "plataforma": "Plataforma", "nombre": "Nombre", "activo": "ISIN / código",
                "cantidad": st.column_config.NumberColumn("Cantidad", format="%.6f"),
                "valor": st.column_config.NumberColumn("Valor (€)", format="localized"),
                "coste": st.column_config.NumberColumn("Coste fiscal (€)", format="localized"),
                "plusvalia": st.column_config.NumberColumn("Plusvalía (€)", format="localized"),
                "fecha_valor": st.column_config.TextColumn("Valorado a"), "fuente": st.column_config.TextColumn("Cómo se calcula", width="medium")})



# ------------------------------------------------------------------ Fiscal
with T["fiscal"]:
    st.caption("Estimaciones orientativas con la escala del ahorro (19 % hasta 6.000 €, 21 % hasta 50.000 €, 23 % hasta 200.000 €, 27 % hasta 300.000 €, 30 % más allá) "
               "y la normativa estatal; no incluyen deducciones autonómicas. Para declarar, revísalo con un asesor.")
    fv, fl = pestana("fiscal_ventas").copy(), pestana("fiscal_lotes").copy()
    pat_f = pestana("patrimonio").copy()
    cfg_f = _costes().get("fiscal", {})
    anos_disp = sorted({int(x) for x in m_all["fecha"].str[:4].dropna().unique()} | {hoy.year}
                       | ({int(x) for x in num(fv["ejercicio"]).dropna().astype(int)} if not fv.empty and "ejercicio" in fv.columns else set()), reverse=True)
    ano = st.selectbox("Año fiscal", anos_disp, index=0, key="ano_fiscal",
                       help="Por defecto, el año en curso. Para preparar la declaración, elige el año anterior.")
    m_ano = m_all[m_all["fecha"].str[:4] == str(ano)]
    en_curso = ano == hoy.year
    primer_dato = m_all["fecha"].min()
    if str(ano) <= primer_dato[:4] and primer_dato[5:7] != "01":
        st.warning(f"Tus movimientos clasificados empiezan el {pd.to_datetime(primer_dato):%d/%m/%Y}: los intereses, el plan de pensiones y los donativos "
                   f"de {ano} solo incluyen lo posterior a esa fecha. Las ventas de cripto sí están completas (vienen de Portfolio Performance).", icon="⚠️")

    # ---------- Renta del año en curso
    st.subheader(f"Tu renta del ahorro de {ano}" + (", hasta hoy" if en_curso else ""), help="Lo que llevas este año en la base del ahorro: ganancias y pérdidas por ventas y permutas de cripto "
                 "(FIFO), e intereses, dividendos y remuneraciones. Los traspasos de fondos no tributan. Estimación; no incluye retenciones ya practicadas.")
    for c_ in ("importe_venta", "coste_fifo", "ganancia", "sin_lote", "cantidad"):
        if c_ in fv.columns:
            fv[c_] = num(fv[c_])
    if not fv.empty:
        fv["ejercicio"] = fv["ejercicio"].astype(str).str[:4]
    gp_ano = fv.loc[fv["ejercicio"] == str(ano), "ganancia"].sum() if not fv.empty else 0.0
    # Pérdidas netas de los 4 años anteriores aún sin compensar (supone que se declararon tal cual).
    pend = 0.0
    if not fv.empty:
        saldo = fv.groupby("ejercicio")["ganancia"].sum()
        perdidas = []  # [año, importe pendiente]
        for a_ in sorted(saldo.index):
            g_ = saldo[a_]
            perdidas = [p_ for p_ in perdidas if int(a_) - int(p_[0]) <= 4]
            if a_ == str(ano):
                break
            if g_ < 0:
                perdidas.append([a_, -g_])
            else:
                for p_ in perdidas:
                    usa = min(p_[1], g_)
                    p_[1] -= usa
                    g_ -= usa
        pend = sum(p_[1] for p_ in perdidas if ano - int(p_[0]) <= 4)
    rcm = m_ano[(m_ano["flujo"] == "Ingreso") & (m_ano["categoria"] == "Intereses, dividendos y cashback")]["importe"].sum()
    gp_neto = gp_ano - min(pend, max(gp_ano, 0))
    if gp_neto < 0:  # las pérdidas compensan hasta el 25 % de los rendimientos
        base_ahorro = max(rcm - min(-gp_neto, 0.25 * rcm), 0)
    else:
        base_ahorro = gp_neto + rcm
    k = st.columns(4)
    k[0].metric("Ganancias por ventas y permutas", eur(gp_ano), border=True, help="Cripto (FIFO) vendida o permutada este año. Negativo = pérdida.")
    k[1].metric("Intereses y dividendos", eur(rcm), border=True, help="Lo cobrado este año (neto de las retenciones que ya te hayan aplicado).")
    k[2].metric("Pérdidas anteriores compensables", eur(pend), border=True,
                help="Pérdidas netas de los 4 años anteriores según el FIFO, si se declararon así. Restan de las ganancias de este año.")
    k[3].metric("Cuota estimada", eur(cuota_ahorro(base_ahorro)), f"base {eur(base_ahorro)}", delta_color="off", border=True,
                help="Escala del ahorro sobre la base estimada. Las retenciones ya practicadas (p. ej. sobre intereses) se descuentan después.")

    # Pérdidas latentes que se podrían realizar
    det_f0 = pestana("patrimonio_detalle").copy()
    if en_curso and not fl.empty and not det_f0.empty:
        fl_ = fl.assign(coste=num(fl["coste"]))
        det_f0["valor"] = num(det_f0["valor"])
        val_c = det_f0[(det_f0["categoria"] == "Cripto") & (det_f0["activo"] != "EUR")].groupby("activo")["valor"].sum()
        cos_c = fl_.groupby("activo")["coste"].sum()
        lat_c = pd.DataFrame({"Valor hoy": val_c, "Coste FIFO": cos_c}).dropna()
        lat_c["Pérdida latente"] = lat_c["Valor hoy"] - lat_c["Coste FIFO"]
        perd = lat_c[lat_c["Pérdida latente"] < -1].sort_values("Pérdida latente")
        if len(perd):
            with st.expander(f"Pérdidas latentes en cripto: {eur(perd['Pérdida latente'].sum())}"):
                st.dataframe(perd.reset_index().rename(columns={"index": "Activo", "activo": "Activo"}), hide_index=True, width="stretch",
                             column_config={c_: st.column_config.NumberColumn(f"{c_} (€)", format="localized") for c_ in ("Valor hoy", "Coste FIFO", "Pérdida latente")})
                st.caption("Vender con pérdida permite compensar ganancias del mismo año. Hay reglas sobre volver a comprar el mismo activo poco después "
                           "y su aplicación a las cripto no es del todo clara: consúltalo con un asesor antes de hacerlo.")

    # ---------- Deducciones
    st.subheader(f"Deducciones de {ano}", help="Lo que reduce tu IRPF por decisiones que tomas durante el año.")
    a_, b_ = st.columns(2)
    with a_:
        lim = cfg_f.get("limite_plan_pensiones", 1500)
        aport = -m_ano[m_ano["categoria"] == cfg_f.get("categoria_plan_pensiones", "")]["importe"].sum()
        marg = st.number_input("Tu tipo marginal (%)", 0.0, 55.0, float(cfg_f.get("tipo_marginal", 37)), 1.0,
                               help="El de tu última renta (tramo general, estatal + autonómico). Lo que ahorras por aportar es la aportación × este tipo.") / 100
        st.metric("Aportado al plan de pensiones", eur(aport), f"te quedan {eur(max(lim - aport, 0))} hasta {eur(lim)}", delta_color="off", border=True,
                  help="Las aportaciones a un plan individual reducen la base general hasta 1.500 € al año (o el 30 % de los rendimientos del trabajo, si es menor).")
        st.caption(f"Ahorro fiscal estimado de lo aportado: **{eur(min(aport, lim) * marg)}**. "
                   + ((f"Aportar lo que falta antes del 31 de diciembre ahorraría unos {eur(max(lim - aport, 0) * marg)} más." if aport < lim else "Ya estás en el máximo.")
                      if en_curso else ""))
    with b_:
        don = m_ano[m_ano["concepto"].str.upper().str.contains(cfg_f.get("donativos_patron", "^$"), regex=True, na=False) & (m_ano["importe"] < 0)]
        donado = -don["importe_imputado"].sum()
        meses_t = max(hoy.month, 1) if en_curso else 12
        donado_ano = donado * 12 / meses_t
        pct_resto = 0.45 if cfg_f.get("donativos_recurrentes") else 0.40
        deduc = lambda d: 0.8 * min(d, 250) + pct_resto * max(d - 250, 0)  # noqa: E731
        st.metric("Donativos", eur(donado), f"deducción estimada {eur(deduc(donado))}", delta_color="off", border=True,
                  help="Donativos a entidades de la Ley 49/2002: 80 % de los primeros 250 € y 40 % del resto (45 % si donas a la misma entidad 3 años seguidos). "
                       "La entidad lo comunica a Hacienda; comprueba que aparece en tus datos fiscales.")
        if en_curso:
            st.caption(f"A este ritmo donarás unos {eur(donado_ano)} en {ano}, con una deducción de unos {eur(deduc(donado_ano))}. "
                       + (f"Con {eur(max(250 - donado_ano, 0))} más llegarías a los 250 € que se deducen al 80 %." if donado_ano < 250 else ""))
        else:
            st.caption("Comprueba en tus datos fiscales de la Agencia Tributaria que aparecen los mismos donativos: la entidad los comunica.")

    st.subheader("Cripto: ventas y permutas por ejercicio",
                 help="FIFO por activo sumando todos los exchanges. Cada permuta cripto-cripto cuenta como venta. Los traspasos entre cuentas propias no.")
    if fv.empty:
        st.info("En la demo, Alex no tiene cripto." if DEMO else "Aún no hay cálculo fiscal: ejecuta el pipeline con el histórico de Portfolio Performance subido.")
    else:
        for c_ in ("importe_venta", "coste_fifo", "ganancia", "sin_lote", "cantidad"):
            fv[c_] = num(fv[c_])
        fv["ejercicio"] = fv["ejercicio"].astype(str).str[:4]
        res = fv.groupby("ejercicio").agg(Operaciones=("activo", "size"), Ventas=("importe_venta", "sum"), Coste=("coste_fifo", "sum"),
                                          Ganancia=("ganancia", "sum"), sin_lote=("sin_lote", lambda x: int((x > 1e-8).sum()))).reset_index()
        res["Cuota estimada"] = res["Ganancia"].map(lambda g: cuota_ahorro(g))
        st.dataframe(res.rename(columns={"ejercicio": "Ejercicio", "sin_lote": "Ventas sin lote de compra"}), hide_index=True, width="stretch", column_config={
            "Ventas": st.column_config.NumberColumn("Importe vendido (€)", format="localized"),
            "Coste": st.column_config.NumberColumn("Coste FIFO (€)", format="localized"),
            "Ganancia": st.column_config.NumberColumn("Ganancia neta (€)", format="localized", help="Ganancias − pérdidas del año. Si es negativa, se compensa en los 4 años siguientes."),
            "Cuota estimada": st.column_config.NumberColumn("Cuota estimada (€)", format="localized", help="Solo con esta ganancia, sin otras rentas del ahorro."),
            "Ventas sin lote de compra": st.column_config.NumberColumn(help="Ventas en las que no había compras registradas suficientes: se ha tomado coste 0 para la parte que falta.")})
        with st.expander("Detalle por ejercicio"):
            ej = st.selectbox("Ejercicio", sorted(fv["ejercicio"].unique(), reverse=True))
            d_ = fv[fv["ejercicio"] == ej].groupby("activo", as_index=False)[["importe_venta", "coste_fifo", "ganancia"]].sum().sort_values("ganancia")
            st.dataframe(d_, hide_index=True, width="stretch", column_config={c_: st.column_config.NumberColumn(f"{c_} (€)", format="localized")
                                                                                for c_ in ("importe_venta", "coste_fifo", "ganancia")})

    st.subheader("Plusvalías latentes", help="Lo que tributarías como ganancia si vendieras hoy todo. Cripto: valor oficial − coste de los lotes FIFO que quedan. "
                 "Fondos: valor − coste fiscal oficial (el que arrastran los traspasos). Plan de pensiones: tributa como rendimiento del trabajo al rescatarlo, no aquí.")
    filas = []
    cats_f = pestana("patrimonio_categorias").copy()
    if not cats_f.empty:
        for c_ in ("valor", "coste_fiscal"):
            cats_f[c_] = num(cats_f[c_]) if c_ in cats_f.columns else np.nan
        for _, r in cats_f[cats_f["coste_fiscal"].notna() & ~cats_f["categoria"].isin(["Cripto", "Efectivo en cuentas"])
                           & ~cats_f["categoria"].str.startswith("Plan de pensiones")].iterrows():
            filas.append({"Producto": r["categoria"], "Valor hoy": r["valor"], "Coste fiscal": r["coste_fiscal"], "Valorado a": str(r.get("fecha_valor", ""))[:10],
                          "Origen del coste": r.get("nota") or "suma de costes por activo"})
    det_f = pestana("patrimonio_detalle").copy()
    if not fl.empty and not det_f.empty:
        fl["cantidad"], fl["coste"] = num(fl["cantidad"]), num(fl["coste"])
        det_f["valor"] = num(det_f["valor"])
        cr = det_f[(det_f["categoria"] == "Cripto") & (det_f["activo"] != "EUR")].groupby("activo")["valor"].sum()
        for activo, v in cr.items():
            filas.append({"Producto": f"Cripto · {activo}", "Valor hoy": v, "Coste fiscal": fl.loc[fl["activo"] == activo, "coste"].sum(),
                          "Valorado a": "hoy", "Origen del coste": "FIFO de tus lotes"})
    if filas:
        lat = pd.DataFrame(filas)
        lat["Plusvalía"] = lat["Valor hoy"] - lat["Coste fiscal"]
        lat = lat.sort_values("Plusvalía", ascending=False)
        k = st.columns(3)
        k[0].metric("Plusvalía latente total", eur(lat["Plusvalía"].sum()), border=True)
        k[1].metric("Impuestos si vendieras todo hoy", eur(cuota_ahorro(lat["Plusvalía"].sum())), border=True,
                    help="Cuota de la base del ahorro sobre la plusvalía total, en un solo año. Orientativo.")
        k[2].metric("Tipo efectivo", pct(cuota_ahorro(lat["Plusvalía"].sum()) / lat["Plusvalía"].sum()) if lat["Plusvalía"].sum() > 0 else "—", border=True)
        st.dataframe(lat, hide_index=True, width="stretch", column_config={
            **{c_: st.column_config.NumberColumn(f"{c_} (€)", format="localized") for c_ in ("Valor hoy", "Coste fiscal", "Plusvalía")},
            "Valorado a": st.column_config.TextColumn(help="Fecha del precio usado. Indexa: su propia valoración (suele ir 1–2 días hábiles por detrás). "
                                                           "MyInvestor: valor liquidativo público del Financial Times × participaciones estimadas."),
            "Origen del coste": st.column_config.TextColumn(width="medium")})
        st.caption("El plan de pensiones no está aquí: al rescatarlo tributa entero como rendimiento del trabajo, no como plusvalía.")

    st.subheader("Simulador de venta", help="Cuánto tributaría vender hoy una cantidad. Cripto: FIFO exacto con tus lotes. Fondos: proporcional al coste fiscal medio "
                 "(la gestora aplica FIFO por lotes y retiene un 19 % de la ganancia al reembolsar).")
    if filas:
        c1, c2 = st.columns([2, 1])
        prod = c1.selectbox("Producto", lat["Producto"].tolist())
        fila = lat[lat["Producto"] == prod].iloc[0]
        importe = c2.number_input("Importe a vender (€)", 0.0, float(max(fila["Valor hoy"], 0)), float(min(1000.0, max(fila["Valor hoy"], 0))), 100.0)
        if prod.startswith("Cripto · ") and not fl.empty:
            activo = prod.split(" · ")[1]
            cant_tot = fl.loc[fl["activo"] == activo, "cantidad"].sum()
            precio = fila["Valor hoy"] / cant_tot if cant_tot else 0
            r_ = simular_venta(fl, activo, importe / precio if precio else 0, precio)
            ganancia = r_["ganancia"]
        else:
            ganancia = importe * (1 - fila["Coste fiscal"] / fila["Valor hoy"]) if fila["Valor hoy"] else 0
        cuota = cuota_ahorro(ganancia)
        k = st.columns(3)
        k[0].metric("Ganancia que tributa", eur(ganancia), border=True)
        k[1].metric("Impuesto estimado", eur(cuota), border=True)
        k[2].metric("Te quedaría neto", eur(importe - cuota), border=True)

# ------------------------------------------------------------------ Vivienda
with T["vivienda"]:
    # Supuestos guardados en la hoja (pestaña «vivienda_supuestos»); si no hay, los de ejemplo.
    POR_DEFECTO = {"precio": 300_000, "entrada": 20.0, "gastos_compra": 12.0, "tipo": 3.0, "plazo": 30, "parte": 50.0, "ibi": 600,
                   "comunidad": 80, "seguros": 600, "suministros": 170, "mantenimiento": 600}
    sup_h = pestana("vivienda_supuestos")
    guardados = dict(zip(sup_h["clave"], num(sup_h["valor"]))) if not sup_h.empty and {"clave", "valor"} <= set(sup_h.columns) else {}
    v_ = lambda k: type(POR_DEFECTO[k])(guardados.get(k, POR_DEFECTO[k]))  # noqa: E731
    st.caption(("Tus supuestos guardados" if guardados else "Valores de ejemplo: cámbialos por los tuyos y pulsa «Guardar como mis valores»")
               + ". Los gastos de la vivienda son del hogar y se te imputa tu parte. No es asesoramiento financiero.")
    with st.container(border=True):
        c = st.columns(4)
        precio = c[0].number_input("Precio (€)", 0, 3_000_000, v_("precio"), 5_000)
        entrada = c[1].number_input("Entrada (%)", 0.0, 100.0, v_("entrada"), 1.0) / 100
        gastos_c = c[2].number_input("Impuestos y gastos de compra (%)", 0.0, 30.0, v_("gastos_compra"), 0.5,
                                     help="ITP de segunda mano en Cataluña más notaría, registro y gestoría (estimación).") / 100
        tipo = c[3].number_input("Tipo de interés (% anual)", 0.0, 15.0, v_("tipo"), 0.05) / 100
        c = st.columns(4)
        plazo = c[0].number_input("Plazo (años)", 1, 40, v_("plazo"))
        parte = c[1].number_input("Tu parte (%)", 0.0, 100.0, v_("parte"), 5.0) / 100
        ibi = c[2].number_input("IBI y basuras (€/año)", 0, 10_000, v_("ibi"), 50)
        comunidad = c[3].number_input("Comunidad (€/mes)", 0, 2_000, v_("comunidad"), 5)
        c = st.columns(3)
        seguros = c[0].number_input("Seguros de hogar y vida (€/año)", 0, 10_000, v_("seguros"), 50)
        suministros = c[1].number_input("Suministros (€/mes)", 0, 2_000, v_("suministros"), 10,
                                        help="Luz, agua, gas e internet de la casa. Hoy, la fibra la pagáis aparte y el resto parece ir incluido en el alquiler.")
        manten = c[2].number_input("Mantenimiento (€/año)", 0, 20_000, v_("mantenimiento"), 50)
        actuales = {"precio": precio, "entrada": entrada * 100, "gastos_compra": gastos_c * 100, "tipo": tipo * 100, "plazo": plazo, "parte": parte * 100,
                    "ibi": ibi, "comunidad": comunidad, "seguros": seguros, "suministros": suministros, "mantenimiento": manten}
        cambiados = any(abs(float(actuales[k]) - float(v_(k))) > 1e-9 for k in actuales)
        if st.button("Guardar como mis valores", disabled=not cambiados, help="Se guardan en la hoja y se cargan cada vez que abras el dashboard."):
            guardar_pestana("vivienda_supuestos", pd.DataFrame({"clave": list(actuales), "valor": [round(float(x), 4) for x in actuales.values()]}))
            st.rerun()

    prestamo, r, n = precio * (1 - entrada), tipo / 12, plazo * 12
    cuota = prestamo * r / (1 - (1 + r) ** -n) if r > 0 else prestamo / n
    derivados = ibi / 12 + comunidad + seguros / 12 + suministros + manten / 12
    alquiler = -normal[(normal["categoria"] == "Vivienda") & (normal["subcategoria"] == "Alquiler")]["importe_imputado"].sum() / n_meses
    telecom = -normal[normal["categoria"] == "Suministros y telecom"]["importe_imputado"].sum() / n_meses
    nomina = men["nomina"].mean()
    # Hoy: alquiler + fibra. Con hipoteca: cuota + gastos de la casa, con internet ya dentro de «Suministros».
    viv_hoy, viv_nueva = alquiler + telecom, (cuota + derivados) * parte
    gasto_nuevo = gas_mes - alquiler - telecom + (cuota + derivados) * parte
    margen_nuevo = (ing_mes - gasto_nuevo) / ing_mes
    pat = pestana("patrimonio")
    liquidez = num(pat.loc[pat["plataforma"] == "Cuentas", "valor"]).sum() if not pat.empty else np.nan
    aport = precio * (entrada + gastos_c) * parte
    st.session_state["viv_aportacion"] = aport            # para la proyección de Patrimonio
    k = st.columns(5)
    k[0].metric("Cuota de la hipoteca", eur(cuota), border=True, help=f"Cuota de todo el préstamo ({eur(prestamo)}), sistema francés.")
    k[1].metric("Tu coste de vivienda", eur(viv_nueva), f"{eur_delta(viv_nueva - viv_hoy)} vs hoy", delta_color="inverse", border=True,
                help="Tu parte de la cuota y de los gastos de la casa (suministros con internet incluidos). Hoy: tu mitad del alquiler y de la fibra.")
    k[2].metric("Tu cuota / tu nómina", pct(cuota * parte / nomina) if nomina else "—", border=True,
                help="Los bancos suelen mirar que la cuota no pase de un 30–35 % de los ingresos netos (del hogar).")
    k[3].metric("Margen con hipoteca", pct(margen_nuevo), f"hoy {pct(margen)}", delta_color="off", border=True,
                help="(Ingresos − nuevo gasto normal) ÷ ingresos. El nuevo gasto cambia tu parte del alquiler por tu parte de la cuota y los gastos.")
    k[4].metric("Tu aportación inicial", eur(aport), f"liquidez: {eur(liquidez)}", delta_color="off", border=True,
                help="Tu parte de la entrada + impuestos y gastos de compra. Liquidez: saldo de tus cuentas (sin fondos ni cripto).")
    st.session_state["viv_delta_mes"] = max(viv_nueva - viv_hoy, 0.0)
    comp = pd.DataFrame({"Vivienda": [viv_hoy, viv_nueva], "Resto del gasto": [gas_mes - alquiler - telecom] * 2,
                         "Margen": [ing_mes - gas_mes, ing_mes - gasto_nuevo]}, index=["Hoy", "Con hipoteca"])
    fig = go.Figure()
    for col, color in zip(comp.columns, (C["rojo"], C["azul"], C["verde"])):
        fig.add_bar(y=comp.index, x=comp[col], name=col, orientation="h", marker_color=color, hovertemplate="%{x:,.0f} €/mes")
    _ = fig.update_layout(barmode="stack", bargap=0.4)
    st.plotly_chart(estilo(fig, 220, euros_en="x"), width="stretch", key="g10")

# ------------------------------------------------------------------ Estado
with T["estado"]:
    reg = pestana("registro")
    a, b = st.columns([3, 2])
    with a:
        st.subheader("Última ejecución del pipeline", help="Solo la ejecución más reciente: cada fuente una vez. Los nombres antiguos del registro "
                     "(de versiones anteriores del pipeline) ya no salen.")
        if not reg.empty:
            ult_f = reg["fecha"].max()
            u = reg[reg["fecha"] == ult_f].sort_values("fuente")[["fuente", "estado", "filas", "detalle"]]
            u["estado"] = u["estado"].map({"ok": "✅ ok", "error": "❌ error", "sin configurar": "⚪ sin configurar"}).fillna(u["estado"])
            st.caption(f"Ejecución del {ult_f}")
            st.dataframe(u, hide_index=True, width="stretch")
    with b:
        st.subheader("Último dato de cada cuenta", help=f"Fecha del último movimiento que ha llegado de cada cuenta, por cualquier fuente (también los "
                     f"provisionales). Las fuentes manuales con más de {DIAS_AVISO} días sin datos salen como aviso arriba.")
        raw_t = pestana("raw")
        base_u = raw_t if not raw_t.empty else m_all
        ud = base_u.groupby("cuenta").agg(ultimo=("fecha", "max"), fuente=("fuente", lambda x: ", ".join(sorted(set(x))))).reset_index()
        ud["días"] = (hoy - pd.to_datetime(ud["ultimo"])).dt.days
        st.dataframe(ud.rename(columns={"cuenta": "Cuenta", "ultimo": "Último movimiento", "fuente": "Fuentes"}), hide_index=True, width="stretch")
    # ---------- Clasificar pendientes desde aquí
    pend_t = pestana("pendientes").copy()
    st.subheader(f"Pendientes de clasificar ({len(pend_t)})",
                 help="Elige la categoría de cada uno y decide si es solo para ese movimiento (ajuste) o para todos los de ese comercio (regla). "
                      "Se guarda en las pestañas «ajustes» y «reglas» de la hoja y se aplica en la siguiente ejecución del pipeline.")
    if len(pend_t):
        # Cada categoría con su flujo y tipo de gasto más habitual en tus datos.
        perfil = m_all[m_all["categoria"] != ""].groupby("categoria").agg(
            flujo=("flujo", lambda x: x.value_counts().index[0]), tipo=("tipo_gasto", lambda x: x.value_counts().index[0] if len(x) else ""))
        cats_all = sorted(c_ for c_ in perfil.index if c_ not in ("Sin clasificar",) and not c_.startswith("Pendiente"))
        ed_p = pend_t[["tx_id", "fecha", "cuenta", "concepto", "importe"]].copy()
        ed_p["importe"] = num(ed_p["importe"])
        ed_p["categoria"] = None
        ed_p["subcategoria"] = ""
        ed_p["aplicar_a"] = "Solo este movimiento"
        ed_p["nota"] = ""
        editado = st.data_editor(ed_p, hide_index=True, width="stretch", key="ed_pendientes",
                                 disabled=["tx_id", "fecha", "cuenta", "concepto", "importe"], column_config={
            "tx_id": None, "fecha": "Fecha", "cuenta": "Cuenta", "concepto": "Concepto",
            "importe": st.column_config.NumberColumn("Importe (€)", format="localized"),
            "categoria": st.column_config.SelectboxColumn("Categoría", options=cats_all, width="medium"),
            "subcategoria": st.column_config.TextColumn("Subcategoría (opcional)"),
            "aplicar_a": st.column_config.SelectboxColumn("Aplicar a", options=["Solo este movimiento", "Todos los de este comercio"],
                                                          help="«Todos los de este comercio» crea una regla: también clasificará los futuros."),
            "nota": st.column_config.TextColumn("Nota")})
        elegidos = editado[editado["categoria"].notna() & (editado["categoria"] != "")]
        if st.button(f"Guardar {len(elegidos)} clasificaciones", disabled=elegidos.empty, type="primary"):
            nuevos_aj, nuevas_re = [], []
            for _, r in elegidos.iterrows():
                fl_, tp_ = perfil.loc[r["categoria"], "flujo"], perfil.loc[r["categoria"], "tipo"]
                if r["aplicar_a"] == "Todos los de este comercio":
                    # Patrón: el concepto sin cifras ni referencias, escapado (p. ej. «COMPRA TARJ. LM PASEO SANT JOAN-BADALONA»).
                    import unicodedata
                    txt_c = unicodedata.normalize("NFKD", str(r["concepto"]).upper()).encode("ascii", "ignore").decode()
                    palabras_c = [w for w in txt_c.split() if not re.search(r"\d", w)]
                    while palabras_c and palabras_c[0].strip(".") in {"COMPRA", "TARJ", "PAGO", "ADEUDO", "RECIBO", "CARGO", "TRANSFERENCIA", "A", "DE"}:
                        palabras_c.pop(0)  # prefijos del banco: la regla busca el nombre del comercio
                    base_c = " ".join(palabras_c).strip() or txt_c
                    nuevas_re.append({"prioridad": 6, "patron": re.escape(base_c), "cuenta": "", "categoria": r["categoria"],
                                      "subcategoria": r["subcategoria"], "tipo_gasto": tp_, "flujo": fl_, "nota": r["nota"] or "Desde el dashboard"})
                else:
                    nuevos_aj.append({"tx_id": r["tx_id"], "flujo": fl_, "categoria": r["categoria"], "subcategoria": r["subcategoria"],
                                      "tipo_gasto": tp_, "pct_imputado": "", "nota": r["nota"] or "Desde el dashboard"})
            for nombre_, filas_ in (("ajustes", nuevos_aj), ("reglas", nuevas_re)):
                if filas_:
                    actual = pestana(nombre_)
                    guardar_pestana(nombre_, pd.concat([actual, pd.DataFrame(filas_).astype(str)], ignore_index=True) if not actual.empty
                                    else pd.DataFrame(filas_))
            st.success(f"Guardado: {len(nuevos_aj)} ajustes y {len(nuevas_re)} reglas. Se aplicarán en la próxima ejecución del pipeline "
                       "(automática dos veces al día, o lánzalo desde GitHub → Actions → Run workflow con «Solo reclasificar»).")

    t = pestana("posibles_duplicados")
    st.subheader(f"Posibles duplicados ({len(t)})")
    if len(t):
        st.dataframe(t, hide_index=True, width="stretch")

    conc = pestana("conciliacion")
    if not conc.empty:
        for c_ in ("cantidad_pp", "cantidad_oficial", "valor_oficial", "diferencia"):
            conc[c_] = num(conc[c_])
        ok_ = conc["estado"].eq("cuadra") | conc["estado"].str.startswith(("explicada", "PP desactualizado"))
        with st.expander(f"Conciliación con Portfolio Performance ({int(ok_.sum())} de {len(conc)} cuadran o están explicadas)"):
          st.caption("Sirvió para validar el histórico de PP contra las fuentes oficiales. Como PP ya no se usa, solo te interesa si aparece una "
                     "diferencia nueva sin explicar (por ejemplo, un activo oficial que no cuadra).")
          ver = st.toggle("Ver también las que cuadran o están explicadas", value=False)
          st.dataframe(conc if ver else conc[~ok_], hide_index=True, width="stretch", column_config={
            "cantidad_pp": st.column_config.NumberColumn("Cantidad en PP", format="%.6f"),
            "cantidad_oficial": st.column_config.NumberColumn("Cantidad oficial", format="%.6f"),
            "valor_oficial": st.column_config.NumberColumn("Valor oficial (€)", format="localized"),
            "diferencia": st.column_config.NumberColumn("Diferencia", format="%.6f")})

