import gspread
import pandas as pd
import pytest

from finanzas import sheets


class _Resp:
    def __init__(self, code):
        self.status_code = code

    def json(self):
        return {"error": {"code": self.status_code, "message": "cuota", "status": "RESOURCE_EXHAUSTED"}}


def test_reintenta_si_google_dice_429(monkeypatch):
    monkeypatch.setattr(sheets.time, "sleep", lambda s: None)
    llamadas = []

    def f():
        llamadas.append(1)
        if len(llamadas) < 3:
            raise gspread.exceptions.APIError(_Resp(429))
        return "ok"
    assert sheets._con_reintentos(f) == "ok" and len(llamadas) == 3


def test_otros_errores_no_se_reintentan(monkeypatch):
    monkeypatch.setattr(sheets.time, "sleep", lambda s: None)
    with pytest.raises(gspread.exceptions.APIError):
        sheets._con_reintentos(lambda: (_ for _ in ()).throw(gspread.exceptions.APIError(_Resp(403))))


class _WS:
    def __init__(self):
        self.ops = []

    def resize(self, rows, cols):
        self.ops.append(("resize", rows, cols))

    def update(self, valores, **kw):
        self.ops.append(("update", valores))


def test_escribir_usa_dos_escrituras_y_pestanas_en_cache():
    h = object.__new__(sheets.Hoja)
    ws = _WS()
    h._pestanas = {"saldos": ws}
    h.escribir("saldos", pd.DataFrame({"a": [1, None]}))
    assert ws.ops == [("resize", 3, 1), ("update", [["a"], [1.0], [""]])]


def test_a_fecha_no_da_la_vuelta_a_las_fechas_iso():
    from finanzas.sheets import a_fecha
    r = a_fecha(pd.Series(["2026-11-12", "2026-11-17", "12/11/2026", "01/12/2026", "2026-11-05 00:00:00", "", "nan"]))
    assert list(r.dropna().dt.strftime("%Y-%m-%d")) == ["2026-11-12", "2026-11-17", "2026-11-12", "2026-12-01", "2026-11-05"]
    assert r.isna().tolist() == [False, False, False, False, False, True, True]
