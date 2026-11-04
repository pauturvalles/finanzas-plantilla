# Manual de operación

Cómo se usa y se mantiene el sistema en el día a día. Las reglas de negocio (qué cuenta como qué) están en `DECISIONES.md`; el estado del proyecto y lo pendiente, en `ESTADO.md`.

## 1. Entorno local (Windows, PowerShell)

Requisitos: Python 3.12 (las librerías de Google ya no dan soporte a 3.10), Git, VS Code o GitHub Desktop.

```powershell
cd <carpeta del proyecto>
.\.venv\Scripts\Activate.ps1          # el prompt pasa a empezar por (.venv)
$env:PYTHONPATH = "src"               # se pierde al cerrar la terminal
```

Crear el entorno desde cero (si se borra `.venv` o cambia la versión de Python):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r dashboard/requirements.txt
python -m pytest -q
```

En VS Code: `Ctrl+Shift+P` → «Python: Select Interpreter» → el de `.venv`, y las terminales nuevas ya salen activadas.

## 2. Comandos

| Para qué | Comando |
|---|---|
| Pipeline completo (bancos + ficheros de Drive + inversiones) | `python -X utf8 -m finanzas.pipeline` |
| Solo reclasificar (sin bancos ni Drive) | `python -X utf8 -m finanzas.pipeline --sin-bancos` |
| Dashboard en local | `streamlit run dashboard/app.py` → http://localhost:8501 |
| Tests | `python -m pytest -q` |
| Autorizar un banco en Enable Banking | `python scripts/autorizar_banco.py "Nombre exacto" [--dias 90]` (`--listar` para ver nombres) |
| Ver qué ve la cuenta de servicio en la carpeta de Drive | `python -X utf8 scripts/ver_carpeta.py` |
| Estructura de la API de Indexa (sin datos personales) | `python -X utf8 scripts/estructura_indexa.py` |
| Claves de Revolut X | `python scripts/generar_clave_revolutx.py CARPETA_FUERA_DEL_REPO` |
| Secretos para Streamlit Cloud (formato TOML) | `python -X utf8 scripts/secretos_streamlit.py` |
| Importar Portfolio Performance (una vez; histórico) | `python -X utf8 scripts/importar_portfolio_performance.py CARPETA [--sin-subir]` |
| Pendientes agrupados por comercio (para resolverlos con reglas) | `python -X utf8 scripts/pendientes_por_comercio.py [--top 30]` |
| Reconstruir el patrimonio mes a mes desde 2020 (una vez) | `python -X utf8 scripts/reconstruir_historico.py CARPETA [--sin-subir]` |

La reconstrucción histórica lee de la hoja lo que escribe el pipeline: **primero el pipeline, después la reconstrucción**. Solo hay que repetirla si se corrige algo del pasado (PP, una cuenta antigua).

## 3. Rutina

**Automático** (GitHub Actions, 7:15 y 19:15 hora peninsular): Revolut conjunta y personal, saldo y movimientos provisionales de Trade Republic, Indexa, Kraken, Revolut X, valores liquidativos.

**Manual, cada semana o dos** — dejar en la carpeta de entrada de Drive (el nombre del fichero da igual salvo en el Sabadell):

| Fichero | De dónde | Para qué |
|---|---|---|
| CSV de transacciones de Trade Republic | App → exportar actividad | Detalle de los pagos con tarjeta (sustituye a los provisionales) y cartera de valores |
| Excel de movimientos del Sabadell (si tu banco no conecta por API) | Web → cuentas → movimientos → Excel | Si el IBAN de la cabecera no basta, poner «corriente» o «ahorro» en el nombre. Para otros bancos hay que añadir un lector (ver GUIA_PERSONALIZACION) |
| CSV de Coverflex | Web de Coverflex | Comidas y transporte pagados con la retribución flexible |
| CSV de la cuenta de MyInvestor | Web → Mi cuenta → movimientos | Compras de fondos con su fecha de valor |
| Extracto CSV de Revolut (solo para el pasado) | App → cuenta → extractos | Meses anteriores a Enable Banking. El de la conjunta, con «conjunta» en el nombre |

Los ficheros pueden solaparse: nada se duplica. La pestaña «Calidad del dato» del dashboard avisa si una fuente lleva más de 35 días sin datos.

**Mensual:** revisar los pendientes en el dashboard → «Calidad del dato»: elegir categoría y «solo este movimiento» (ajuste) o «todos los de este comercio» (regla), y guardar. Se aplica en la siguiente ejecución del pipeline.

**Cada ~6 meses:** renovar el consentimiento de cada banco en Enable Banking (`scripts/autorizar_banco.py`), actualizar el uid en `.env` y en el secreto `EB_ACCOUNTS`.

**Al cambiar algo grande** (traspaso, venta): actualizar `costes_fiscales` con los datos de la web de la entidad.

**Cada 3–6 meses:** revisar comisiones, TER y remuneraciones en `config/costes.yaml` (cambian; p. ej. el interés de Trade Republic sigue al BCE) y actualizar su fecha `revisado`.

## 4. Pestañas que se rellenan a mano en la hoja

| Pestaña | Contenido |
|---|---|
| `reglas` | Patrón → categoría. Se siembra desde `config/reglas_comercios.csv` la primera vez; después manda la hoja |
| `ajustes` | Corrección puntual de un movimiento por su `tx_id` |
| `presupuestos` | Tope mensual por categoría |
| `fondos_inicial` | Foto inicial de MyInvestor (participaciones, invertido, coste fiscal y fecha) |
| `conciliaciones` | Participaciones reales de MyInvestor en una fecha (si el CSV faltara) |
| `costes_fiscales` | Coste fiscal o plusvalía latente de la web de Indexa / MyInvestor, por fondo (ISIN) o por categoría |
| `valores_liquidativos` | Precios; el pipeline añade los públicos y aquí se pueden añadir a mano (acciones de Trade Republic) |
| `posiciones_manual` | Activos que ninguna fuente ve |
| `vivienda_supuestos` | Supuestos del simulador de vivienda; **se guardan desde el dashboard** |
| `reparto_objetivo` | Objetivo de reparto por clase de activo; **se edita desde el dashboard** (Patrimonio → «Editar tu objetivo de reparto») |
| `inversiones_manual` | Fotos manuales de valor (uso residual) |

Pestañas auxiliares que no hay que tocar: `costes_fiscales_base`, `tr_valores`, `patrimonio_*`, `fiscal_*`, `cripto_operaciones`, `pp_*`, `conciliacion`, `registro`, `raw`, `movimientos`, `saldos`.

## 5. Secretos

En `.env` (local, nunca en el repo) y en GitHub → Settings → Secrets → Actions. En Streamlit Cloud solo `SHEET_ID` y `GOOGLE_SERVICE_ACCOUNT_JSON`.

`EB_APPLICATION_ID`, `EB_PRIVATE_KEY`, `EB_ACCOUNTS` (JSON en una sola línea: `{"TR": "…", "REV_CONJ": "…", "REV_PERS": "…"}`), `GOOGLE_SERVICE_ACCOUNT_JSON`, `SHEET_ID`, `DRIVE_FOLDER_ENTRADA`, `INDEXA_TOKEN`, `KRAKEN_API_KEY`, `KRAKEN_API_SECRET` (permisos: Query Funds + Query Ledger Entries), `REVX_API_KEY`, `REVX_PRIVATE_KEY`, y opcionales `IBAN_*`.

Formato en `.env`: cada variable en **una sola línea**; los JSON entre comillas simples; las claves PEM entre comillas dobles. En GitHub, los JSON y las PEM **sin** las comillas de fuera.

## 6. Problemas conocidos y su solución

| Síntoma | Causa | Solución |
|---|---|---|
| `KeyError` de una variable de entorno | El script no cargaba el `.env`, o la variable está partida en varias líneas | Todos los `__main__` llaman a `load_dotenv()`; revisar que cada variable ocupe una línea |
| Comandos `python -c "…"` con comillas fallan en PowerShell | PowerShell rompe las comillas escapadas | Usar un script `.py` en `scripts/` |
| `-7,88` no se convierte a número | La hoja en español devuelve números formateados | Se lee con `UNFORMATTED_VALUE` y `a_numero()` acepta «1.234,56» y «8.000» |
| Drive: 0 ficheros | ID de carpeta equivocado o carpeta no compartida con la cuenta de servicio | `scripts/ver_carpeta.py` |
| Sabadell en Enable Banking: «major issues» | Incidencia de su conexión | Excel manual; reintentar de vez en cuando |
| MyInvestor en Enable Banking: `server_error` | Su pasarela; la contraseña además tenía que ser corta | CSV manual |
| Trade Republic por Enable Banking sin concepto | Limitación de Trade Republic (solo fecha e importe) | Provisionales + CSV |
| Revolut X: `429 Rate limit exceeded` | Cada día del rango consultado gasta un crédito | Pausa entre consultas y reintento automático |
| Kraken: `Permission denied` en el ledger | Falta el permiso «Query Ledger Entries» | Añadirlo a la clave |
| Gráficos repetidos en Streamlit | «Magic» de Streamlit pinta expresiones sueltas | `magicEnabled = false` en `.streamlit/config.toml` |
| Sankey en blanco | Las librerías no se pueden cargar de un CDN dentro del iframe | D3 va incluido en `dashboard/vendor/` |
| `pip` no encuentra `streamlit==1.66.1` | Python 3.10 | Python 3.12 |
| Binance / KuCoin solo exportan 6 meses | Limitación de los exchanges | Portfolio Performance es la fuente de su pasado |
