# Guía para montar tu propia versión

Esta plantilla viene de un proyecto real de finanzas personales, ya en uso, del que se han quitado todos los datos y conexiones. Aquí está **cómo dejarla funcionando con tus cuentas**, qué decisiones tendrás que tomar y qué problemas aparecieron por el camino (y cómo se resolvieron).

Puedes seguirla tú solo o con un asistente de IA: en ese caso, empieza por pasarle `CLAUDE.md`.

---

## 0. Qué vas a tener al final

```
Bancos (Open Banking) ──┐
Ficheros que subes ─────┼──► Pipeline en Python ──► Google Sheets ──► Dashboard (Streamlit)
APIs de brókers/cripto ─┘    (GitHub Actions,          (tus datos)       (privado)
                              2 veces al día)
```

- **Pipeline** (`src/finanzas/`): descarga movimientos y saldos, los clasifica con reglas y calcula patrimonio, rentabilidades y fiscalidad.
- **Google Sheets**: almacén de datos y lugar donde editas reglas, ajustes y supuestos.
- **Dashboard** (`dashboard/app.py`): gasto, ingresos, ahorro, patrimonio, inversiones, fiscalidad, simulador de vivienda y calidad de los datos.

**Coste:** cero con uso personal (cuentas gratuitas de Enable Banking, Google Cloud, GitHub y Streamlit Community Cloud).

**Tiempo:** una tarde para lo básico (un banco + hoja + dashboard); una o dos más para añadir brókers, ficheros y afinar reglas.

---

## 1. Requisitos

- **Python 3.12** (las librerías de Google ya no dan soporte a 3.10).
- **Git** y una cuenta de **GitHub**.
- Una cuenta de **Google** (para Sheets, Drive y una cuenta de servicio).
- Conocimientos básicos de terminal. Con un asistente de IA basta con saber copiar y pegar comandos.

## 2. Crea tu repositorio (privado)

1. Crea un repositorio **privado** en GitHub y copia ahí el contenido de esta plantilla. Tiene que ser privado: el pipeline guardará tus reglas y, si quieres, un histórico de movimientos.
2. En tu ordenador:
   ```powershell
   git clone <tu repo>
   cd <tu repo>
   py -3.12 -m venv .venv
   .\.venv\Scripts\Activate.ps1          # en macOS/Linux: source .venv/bin/activate
   pip install -r requirements.txt -r dashboard/requirements.txt
   python -m pytest -q                   # todo en verde = el entorno está bien
   ```
3. Copia `.env.example` a `.env`. Ahí irán todas tus claves; **nunca se sube** (está en `.gitignore`).

## 3. Describe tus cuentas (`config/cuentas.yaml`)

Es el fichero más importante. Para cada cuenta:

| Campo | Qué poner |
|---|---|
| `id` | Un nombre corto en mayúsculas (`BBVA_CORR`, `REV_CONJ`…). Se usa en todas partes |
| `fuente` | `enable_banking` si conecta por Open Banking; si no, el lector de ficheros que corresponda |
| `aspsp` | Nombre exacto del banco en Enable Banking (ver paso 5) |
| `pct_titular` | 1.0 para cuentas tuyas; 0.5 para una cuenta compartida (añade `conjunta: true`) |

Además:
- `nombre_titular` / `nombre_pareja`: nombres cortos para categorías y dashboard.
- `titular`, `alias_titular`, `pareja`, `alias_pareja`: cómo aparecéis **en los conceptos de las transferencias** (mayúsculas, sin tildes). Es lo que permite reconocer aportaciones a la conjunta y reposiciones.
- `compensan_compras` y `familia`: personas cuyos Bizums compensan compras que les haces, y familiares cuyos Bizums no se toman como «su parte» de una comida.
- `clasificar_desde`: primer mes que quieres analizar. **Elige uno en el que tengas datos de todas tus cuentas.**
- `inversion`: solo si usas MyInvestor, Revolut X, etc. Si no, deja las listas vacías.

Si no tienes cuenta conjunta, quita la entrada `REV_CONJ` y no pasa nada.

## 4. Google: hoja, carpeta y cuenta de servicio

1. **Google Cloud** (console.cloud.google.com; no hace falta activar facturación ni la prueba gratuita): crea un proyecto, activa **Google Sheets API** y **Google Drive API**, crea una **cuenta de servicio** y descarga su clave **JSON** (guárdala fuera del repo).
2. Crea una **hoja de cálculo vacía** y compártela con el email de la cuenta de servicio como **Editor**.
3. Crea una **carpeta de Drive** para los ficheros que subirás a mano (extractos, CSV) y compártela con la cuenta de servicio como **Lector**.
4. En `.env`:
   ```
   SHEET_ID=<lo que va entre /d/ y /edit en la URL de la hoja>
   DRIVE_FOLDER_ENTRADA=<lo que va después de /folders/ en la URL de la carpeta>
   GOOGLE_SERVICE_ACCOUNT_JSON='<el JSON en una sola línea>'
   ```
   Para pasar el JSON a una línea: `python -c "import json; print(json.dumps(json.load(open(r'RUTA.json'))))"`.
5. Comprueba que la cuenta de servicio ve la carpeta: `python -X utf8 scripts/ver_carpeta.py`.

El pipeline crea solo todas las pestañas de la hoja en su primera ejecución.

## 5. Bancos por Open Banking (Enable Banking)

Enable Banking da acceso de solo lectura a tus cuentas mediante PSD2, gratis para uso personal («restricted mode»: solo tus propias cuentas).

1. Crea una cuenta en enablebanking.com, registra una **aplicación** en modo producción y descarga su clave privada (`.pem`, fuera del repo). Como URL de redirección vale `https://localhost/finanzas-callback`.
2. En `.env`: `EB_APPLICATION_ID=<id>` y `EB_PRIVATE_KEY="<contenido del .pem con \n>"`.
3. Busca el nombre exacto de tu banco y autorízalo:
   ```powershell
   python scripts/autorizar_banco.py --listar | Select-String "MiBanco"
   python scripts/autorizar_banco.py "Nombre exacto"          # --dias 90 si da error con 180
   ```
   Te abre el login del banco; al terminar, el navegador va a una página de `localhost` que no carga: **copia esa URL completa** y pégala en la terminal. El script te da el `uid` de cada cuenta.
4. Pon los `uid` en `.env`, **en una sola línea**: `EB_ACCOUNTS='{"BBVA_CORR": "uid…", "REV_CONJ": "uid…"}'`.
5. El consentimiento caduca (unos 90–180 días): repite el paso 3 cuando toque.

**Lo que aprendimos de bancos concretos** (puede haber cambiado):

| Banco | Experiencia |
|---|---|
| Revolut | Funciona perfecto, con comercio y nota de las transferencias. Solo da ~90 días de historial: para el pasado, su extracto CSV |
| Trade Republic | Conecta, pero solo da fecha e importe. Se usa para el saldo y como provisional; el detalle sale de su CSV |
| Sabadell | La conexión tuvo incidencias largas: se usa su Excel de movimientos |
| MyInvestor | `server_error` en su pasarela (y exige contraseña corta): se usa su CSV |

Si tu banco no conecta, no pasa nada: usa sus ficheros (paso 6).

## 6. Ficheros que subes a mano

Déjalos en la carpeta de Drive; el pipeline los reconoce por sus columnas (el nombre da igual salvo donde se indica). Pueden solaparse sin duplicar nada.

| Fichero | Lector | Notas |
|---|---|---|
| CSV de transacciones de Trade Republic | `fuentes/trade_republic_csv.py` | La mejor fuente de TR: comercio, IBAN, cartera de valores |
| CSV de Coverflex | `fuentes/coverflex.py` | Dos formatos (antiguo y «movimientos») soportados |
| CSV de la cuenta de MyInvestor | `fuentes/myinvestor.py` | Compras de fondos con fecha de valor |
| Excel del Sabadell | `fuentes/sabadell.py` | «corriente»/«ahorro» en el nombre si el IBAN no basta |
| Extracto CSV de Revolut | `fuentes/revolut_csv.py` | Para meses anteriores a la conexión. «conjunta» en el nombre |

**¿Tu banco no está?** Hace falta un lector nuevo en `src/finanzas/fuentes/` que devuelva las columnas de `COLUMNAS_RAW` (`modelo.py`), y registrarlo en `pipeline.py`. Con un asistente de IA: pásale **la cabecera y dos filas inventadas** de tu fichero (nunca el fichero real) y pídele el lector y su test.

## 7. Brókers y cripto por API (opcionales)

| Plataforma | Cómo | Variables |
|---|---|---|
| Indexa Capital | Token personal (Configuración → Aplicaciones). Comprueba la estructura con `scripts/estructura_indexa.py` | `INDEXA_TOKEN` |
| Kraken | Clave con **solo** «Query Funds» y «Query Ledger Entries» | `KRAKEN_API_KEY`, `KRAKEN_API_SECRET` |
| Revolut X | Par de claves Ed25519: `scripts/generar_clave_revolutx.py CARPETA_FUERA_DEL_REPO`; registra la pública en Revolut X | `REVX_API_KEY`, `REVX_PRIVATE_KEY` |

Las fuentes sin configurar salen como «sin configurar» en el registro, no como error.

## 8. Primera ejecución y reglas

```powershell
$env:PYTHONPATH = "src"
python -X utf8 -m finanzas.pipeline             # todo
python -X utf8 -m finanzas.pipeline --sin-bancos   # solo reclasificar
```

1. Mira la pestaña `registro`: cada fuente con su estado.
2. Mira `pendientes`: lo que el motor no sabe clasificar. Para verlos agrupados: `python -X utf8 scripts/pendientes_por_comercio.py`.
3. Resuélvelos con **reglas** (si se repetirá) o **ajustes** (si es puntual), en la hoja o desde el dashboard. Las reglas de `config/reglas_comercios.csv` son genéricas y solo siembran la hoja la primera vez: a partir de ahí manda la pestaña `reglas`.
4. Repite hasta que queden pocos. Con 30–40 reglas propias suele bastar.

## 9. Automatizar con GitHub Actions

Settings → Secrets and variables → Actions: crea un secreto por cada variable del `.env` (los JSON y las claves PEM **sin** las comillas de fuera). El workflow `.github/workflows/sincronizar.yml` corre dos veces al día y se puede lanzar a mano («Solo reclasificar» tras editar reglas).

## 10. Dashboard

- **En local**: copia `.streamlit/secrets.toml.example` a `.streamlit/secrets.toml` (o genera su contenido con `python -X utf8 scripts/secretos_streamlit.py`) y `streamlit run dashboard/app.py`.
- **En Streamlit Community Cloud**: share.streamlit.io → tu repo (autoriza repos privados) → `dashboard/app.py`, Python 3.12, los dos secretos → Deploy. Después, Settings → Sharing: **privada**, solo tu email. Se duerme tras horas sin uso y tarda un minuto en despertar; alternativas: Hugging Face Spaces privado, Google Cloud Run.

## 11. Decisiones que tendrás que tomar

Revisa `docs/DECISIONES.md` y decide, para tu caso:

- **Qué cuentas son compartidas** y a qué %.
- **Retribución flexible** (Coverflex o similar): si la cuentas como ingreso en bruto (recomendado) o no.
- **Nómina**: si se asigna al mes que paga (por defecto, la de los días 1–5 va al mes anterior).
- **Extraordinarios**: qué gastos grandes y puntuales no deben contar en tus medias.
- **Reposiciones y compensaciones**: si tu caso se parece (conjunta que te devuelve pagos, amigos que te pagan su parte).
- **Objetivo de reparto, rentabilidades esperadas, tipo marginal, comisiones de tus fondos** (`config/costes.yaml` y el propio dashboard).

Anota tus decisiones en la sección 13 de `DECISIONES.md`.

## 12. Opcional: tu historial de inversiones

- **Portfolio Performance**: `scripts/importar_portfolio_performance.py` importa sus CSV de transacciones (uno por cuenta), empareja traspasos entre cuentas propias y clasifica comisiones, staking y pérdidas. Manda siempre la fuente oficial; la conciliación te muestra lo que no cuadra.
- **Evolución desde el principio**: `scripts/reconstruir_historico.py` reconstruye el valor mes a mes con precios históricos (PP para fondos, Binance pública para cripto) y los saldos de tus extractos. Ejecuta antes el pipeline.
- **Costes fiscales**: Indexa no da el coste fiscal por API; cópialo de su web a la pestaña `costes_fiscales` (por fondo o por categoría).

## 13. Problemas que encontramos y su solución

Ver la tabla completa en `docs/OPERACIONES.md` (sección 6). Los más habituales al empezar:

- **`KeyError` de una variable**: el `.env` tiene una variable partida en varias líneas o con comillas mal.
- **Comandos `python -c "…"` que fallan en PowerShell**: PowerShell rompe las comillas; usa un script `.py`.
- **Drive: 0 ficheros**: ID de carpeta equivocado o carpeta no compartida con la cuenta de servicio (`scripts/ver_carpeta.py`).
- **Números como `-7,88` que no se convierten**: la hoja en español devuelve números formateados; ya está resuelto leyendo sin formato.
- **Gráficos repetidos en Streamlit**: la función «magic»; está desactivada en `.streamlit/config.toml`.
- **Sankey en blanco**: las librerías no cargan desde un CDN dentro del iframe; D3 va incluido en `dashboard/vendor/`.

## 14. Privacidad y seguridad

- Repositorio **privado**; claves solo en `.env` y en secretos de GitHub/Streamlit.
- Claves de API **de solo lectura** siempre que la plataforma lo permita.
- No subas extractos al repo: van a tu carpeta de Drive o se procesan en local (`.gitignore` ya bloquea Excel, PDF y exports conocidos).
- Si usas un asistente de IA, pásale **estructuras** (cabeceras, filas inventadas, salidas sin importes), no tus ficheros reales.
