"""Google Sheets y Drive con una cuenta de servicio.

Comparte la hoja y la carpeta de entrada de Coverflex con el email de la cuenta de
servicio (permiso de editor en la hoja, lector en la carpeta).
"""
from __future__ import annotations

import io
import json
import os
import re

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive.readonly"]

# Pestañas de la hoja. Las que escribe el pipeline se sobrescriben enteras en cada
# ejecución (idempotente); las de entrada manual solo se leen.
ESCRITAS = ["raw", "movimientos", "pendientes", "posibles_duplicados", "saldos", "patrimonio", "registro"]
MANUALES = {
    "reglas": ["prioridad", "patron", "cuenta", "categoria", "subcategoria", "tipo_gasto", "flujo", "nota"],
    "ajustes": ["tx_id", "flujo", "categoria", "subcategoria", "tipo_gasto", "pct_imputado", "nota"],
    "fondos_inicial": ["isin", "fondo", "fecha", "participaciones", "valor_mercado", "coste_medio", "invertido", "coste_fiscal"],
    "conciliaciones": ["isin", "fecha", "participaciones", "valor_real"],
    "valores_liquidativos": ["isin", "fecha", "vl", "fuente"],
    "inversiones_manual": ["plataforma", "producto", "fecha", "valor"],
    "presupuestos": ["categoria", "presupuesto_mensual", "nota"],
    "posiciones_manual": ["categoria", "plataforma", "isin", "nombre", "cantidad", "valor", "coste_fiscal", "fecha"],
    "costes_fiscales": ["categoria", "isin", "fecha", "coste_fiscal", "plusvalia_latente", "valor", "nota"],
}


def a_numero(serie: pd.Series) -> pd.Series:
    """Convierte a float aceptando tanto 1234.5 como "1.234,50" (lo que escribe a mano una hoja en español)."""
    def conv(v):
        if isinstance(v, (int, float)):
            return float(v)
        t = str(v).strip().replace("€", "").replace(" ", "")
        if not t:
            return float("nan")
        if "," in t or re.fullmatch(r"-?[1-9]\d{0,2}(\.\d{3})+", t):  # «1.234,56» o «8.000» (miles con punto)
            t = t.replace(".", "").replace(",", ".")
        return float(t)
    return serie.map(conv)


def _creds() -> Credentials:
    return Credentials.from_service_account_info(json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]), scopes=SCOPES)


class Hoja:
    def __init__(self, sheet_id: str | None = None):
        self.sh = gspread.authorize(_creds()).open_by_key(sheet_id or os.environ["SHEET_ID"])

    def _ws(self, nombre: str, cabecera: list[str] | None = None):
        try:
            return self.sh.worksheet(nombre)
        except gspread.WorksheetNotFound:
            ws = self.sh.add_worksheet(nombre, rows=100, cols=max(10, len(cabecera or [])))
            if cabecera:
                ws.update([cabecera])
            return ws

    def leer(self, nombre: str) -> pd.DataFrame:
        cab = MANUALES.get(nombre)
        # Números sin formato (si no, una hoja en español devuelve "-7,88") y fechas como texto.
        filas = self._ws(nombre, cab).get_all_values(value_render_option="UNFORMATTED_VALUE",
                                                     date_time_render_option="FORMATTED_STRING")
        if not filas:
            return pd.DataFrame(columns=cab or [])
        cols = [str(c).strip().lower() for c in filas[0]]
        df = pd.DataFrame(filas[1:], columns=cols)
        return df.loc[:, [c for c in cols if c]]  # ignora columnas sin cabecera

    def escribir(self, nombre: str, df: pd.DataFrame) -> None:
        ws = self._ws(nombre)
        valores = [list(df.columns)] + df.astype(object).where(df.notna(), "").values.tolist()
        ws.clear()
        ws.resize(rows=max(len(valores), 2), cols=max(len(df.columns), 1))
        ws.update(valores, value_input_option="RAW")

    def crear_pestanas_manuales(self) -> None:
        for nombre, cab in MANUALES.items():
            self._ws(nombre, cab)


def ficheros_de_carpeta(extension: str, folder_id: str | None = None) -> list[tuple[str, bytes]]:
    """Descarga los ficheros de la carpeta de entrada con esa extensión
    (.csv = exports de Coverflex, .pdf = extractos de Trade Republic).

    Se listan todos los ficheros y se filtra aquí: el filtro `name contains` de Drive
    solo compara prefijos de palabra y no sirve para extensiones. Si Drive convirtió
    un CSV en Hoja de cálculo al subirlo, se exporta de vuelta a CSV."""
    drive = build("drive", "v3", credentials=_creds(), cache_discovery=False)
    carpeta = folder_id or os.environ.get("DRIVE_FOLDER_ENTRADA") or os.environ["DRIVE_FOLDER_COVERFLEX"]
    tipos = {".csv": ("text/csv", "application/vnd.google-apps.spreadsheet"), ".pdf": ("application/pdf",),
             ".xls": ("application/vnd.ms-excel",), ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",)}[extension]
    res = drive.files().list(q=f"'{carpeta}' in parents and trashed = false", pageSize=1000,
                             fields="files(id,name,mimeType)", supportsAllDrives=True,
                             includeItemsFromAllDrives=True).execute().get("files", [])
    out = []
    for f in res:
        if not (f["name"].lower().endswith(extension) or f["mimeType"] in tipos[:1]):
            if f["mimeType"] != "application/vnd.google-apps.spreadsheet" or extension != ".csv":
                continue
        if f["mimeType"] == "application/vnd.google-apps.spreadsheet":
            pet = drive.files().export_media(fileId=f["id"], mimeType="text/csv")
        else:
            pet = drive.files().get_media(fileId=f["id"])
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, pet)
        hecho = False
        while not hecho:
            _, hecho = dl.next_chunk()
        out.append((f["name"], buf.getvalue()))
    return out
