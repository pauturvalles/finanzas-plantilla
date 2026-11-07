# Finanzas personales — plantilla

Sistema para tener **centralizado y automatizado** tu dinero: gastos clasificados, ingresos, ahorro, patrimonio, inversiones, fiscalidad orientativa y simulaciones (vivienda, recortes, proyección).

```
Bancos (Open Banking) ──┐
Ficheros que subes ─────┼──► Pipeline en Python ──► Google Sheets ──► Dashboard en Streamlit
APIs de brókers/cripto ─┘    (GitHub Actions, 2×/día)
```

Viene de un proyecto real en uso, **sin ningún dato ni conexión personal**. Está pensado para personalizarlo, solo o con un asistente de IA.

## Demo

Antes de instalar nada puedes ver el dashboard con **datos inventados** (Alex: 2.000 € al mes, piso compartido, dos fondos indexados, gimnasio, guitarra y cine):

- **En la web**: el enlace que te hayan pasado (una app pública de Streamlit desplegada desde este repositorio).
- **En tu ordenador**: `lanzadores/Demo.bat` en Windows, o `FINANZAS_DEMO=1 streamlit run dashboard/app.py`.

El dashboard entra en modo demo solo cuando no tiene `SHEET_ID` configurado (o con `MODO_DEMO = true` en los secretos). Los datos los crea `demo/generar_demo.py` pasando movimientos inventados por el mismo motor de reglas que usarás tú, y se regeneran solos para llegar siempre hasta ayer. Lo que edites en la demo solo dura mientras tengas la página abierta.

## Por dónde empezar

1. **`docs/GUIA_PERSONALIZACION.md`** — paso a paso: cuentas, Google, Open Banking, ficheros, APIs, automatización, dashboard, decisiones y problemas conocidos.
2. **`CLAUDE.md`** — si trabajas con un asistente de IA, pásale este fichero primero.
3. **`docs/DECISIONES.md`** — los criterios con que se clasifica y calcula todo.
4. **`docs/OPERACIONES.md`** — comandos, rutina y solución de problemas.
5. **`docs/ESTADO.md`** — qué incluye y qué se podría añadir.

## Qué incluye

- **Fuentes**: Enable Banking (Open Banking PSD2), CSV/Excel de Trade Republic, Revolut, Sabadell, Coverflex y MyInvestor; APIs de Indexa Capital, Kraken y Revolut X; precios públicos de fondos y cripto; importación de Portfolio Performance.
- **Motor de reglas** idempotente (sin duplicados al subir ficheros solapados), cuenta conjunta al 50 % con reposiciones y compensaciones por Bizum, extraordinarios, pendientes y ajustes.
- **Dashboard**: resumen, mes en curso con presupuestos y cierre del mes anterior, ingresos, gastos (Sankey interactivo, año contra año, estacionalidad), desglose por categoría, dónde ahorrar (gasto hormiga, suscripciones, «¿y si…?»), cuenta conjunta, patrimonio (evolución, reparto con objetivo, proyección, efectivo), inversiones (composición, rentabilidad y TIR, costes), fiscalidad (renta del año, deducciones, FIFO de cripto, simulador de venta), vivienda y calidad del dato.

## Estructura

```
config/            cuentas.yaml, reglas_comercios.csv, costes.yaml  ← lo que personalizas
src/finanzas/      pipeline, modelo, reglas, patrimonio, fiscal, historico, sheets, fuentes/
dashboard/         app.py (+ vendor/ con D3 para el Sankey)
scripts/           autorización de bancos, diagnóstico, importaciones de una vez
demo/              datos inventados y su generador (modo demo del dashboard)
lanzadores/        .bat de Windows: dashboard, demo, pipeline y autorizar banco
tests/             pytest (con personas y datos ficticios)
docs/              guía, criterios, operación, estado
.github/workflows/ ejecución automática
```

## Requisitos

Python 3.12, cuenta de GitHub (repositorio **privado**), cuenta de Google. Todo lo demás tiene plan gratuito suficiente para uso personal.

## Aviso

Los cálculos fiscales y de inversión son **orientativos**: sirven para tener los números ordenados, no sustituyen a un asesor.
