# Contexto para un asistente de IA

Este repositorio es una **plantilla** de finanzas personales: un pipeline en Python lee bancos (Open Banking), ficheros y APIs de brókers, clasifica movimientos con reglas y escribe en Google Sheets; un dashboard de Streamlit lo muestra. Viene de un proyecto real; se han quitado todos los datos personales. Tu papel es **ayudar a una persona nueva a personalizarlo y ponerlo en marcha**. Idioma: castellano (código, comentarios, documentación y conversación).

## Antes de nada, lee

1. `docs/GUIA_PERSONALIZACION.md` — el camino completo y los problemas conocidos.
2. `docs/DECISIONES.md` — criterios de clasificación y cálculo (fuente de verdad).
3. `docs/OPERACIONES.md` — comandos y tabla de problemas/soluciones.
4. `docs/ESTADO.md` — lo que la persona vaya anotando.

## Cómo empezar con la persona

Pregúntale, de una en una y sin abrumar:
1. Qué **bancos y cuentas** usa, cuáles son compartidas (y con quién, a qué %) y cuál es la de la nómina.
2. Si tiene **retribución flexible** (Coverflex u otra), **brókers o fondos** (Indexa, MyInvestor, Trade Republic…) y **cripto** (qué exchanges).
3. Qué quiere conseguir con el dashboard (rellena «Para qué lo quiero» en `ESTADO.md`).
4. Su sistema operativo y su soltura con la terminal (adapta los comandos).

Con eso: rellena `config/cuentas.yaml`, decide qué fuentes conectar por Enable Banking y cuáles por fichero, y sigue la guía paso a paso, comprobando cada paso antes del siguiente.

## Mapa del código

| Ruta | Qué hace |
|---|---|
| `src/finanzas/pipeline.py` | Orquestador: fuentes → raw → clasificación → hoja; patrimonio, conciliación, fiscalidad |
| `src/finanzas/modelo.py` | Esquema, `tx_id` determinista, upsert idempotente, prioridad de fuentes por cuenta y día |
| `src/finanzas/reglas.py` | Motor de clasificación (función pura) |
| `src/finanzas/patrimonio.py` | Valoraciones, categorías, costes fiscales, conciliación |
| `src/finanzas/fiscal.py` | FIFO de cripto, escala del ahorro, simulador de venta |
| `src/finanzas/historico.py` | Reconstrucción del patrimonio mes a mes |
| `src/finanzas/sheets.py` | Google Sheets/Drive; `a_numero()` para números en español |
| `src/finanzas/fuentes/` | Un lector por fuente |
| `dashboard/app.py` | Dashboard (D3 incluido en `dashboard/vendor/`) |
| `config/` | `cuentas.yaml`, `reglas_comercios.csv`, `costes.yaml` |

## Principios (respétalos)

- **Privacidad**: la persona **nunca** debe pasarte ficheros reales de bancos. Para un formato nuevo, pide **la cabecera y dos filas inventadas**; para errores, la salida sin importes ni nombres. Repositorio **privado**; claves solo en `.env` y secretos.
- **Mandan las fuentes oficiales**; los registros manuales solo rellenan huecos.
- **Idempotencia**: nada se duplica al subir ficheros solapados; nada se borra automáticamente.
- **Cada cambio con su test** (`tests/`), y cada criterio nuevo, en `DECISIONES.md`.
- **Fiscalidad e inversión, orientativas**: da números, no recomendaciones; remite a un asesor para decidir.
- Para comandos en Windows/PowerShell, evita `python -c "…"` con comillas anidadas: crea un script.

## Al terminar cada sesión

Actualiza `docs/ESTADO.md` (bitácora) y `docs/DECISIONES.md` si cambió algún criterio.
