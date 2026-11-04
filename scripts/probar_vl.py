"""Comprueba que se puede descargar el valor liquidativo de tus fondos de MyInvestor."""
import os
import sys

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from finanzas.fuentes.valores_liquidativos import ultimo_vl  # noqa: E402

cfg = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), "..", "config", "cuentas.yaml"), encoding="utf-8"))
for f in cfg["inversion"]["myinvestor_fondos"]:
    print(f["isin"], f["nombre_en_extracto"], "→", ultimo_vl(f["isin"]))
