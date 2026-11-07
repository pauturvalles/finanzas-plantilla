# Estado del proyecto

Rellena este documento a medida que avances: es la memoria del proyecto (para ti y para cualquier asistente de IA que te ayude).

## Para qué lo quiero

(Escribe tus objetivos. Los del proyecto original eran: patrimonio centralizado y automático; control de gastos; ver dónde ahorrar sin esfuerzo; detectar gasto «inocuo»; simular escenarios como una hipoteca; saber si otro producto rendiría más; y entender la fiscalidad.)

## Mis fuentes de datos

| Fuente | Cómo llega | Estado |
|---|---|---|
| … | Enable Banking / fichero / API | |

## Qué incluye la plantilla

- Pipeline con Enable Banking, ficheros (Trade Republic, Revolut, Sabadell, Coverflex, MyInvestor) y APIs (Indexa, Kraken, Revolut X) → Google Sheets, dos veces al día con GitHub Actions.
- Dashboard en Streamlit con 12 pestañas (ver README).
- Reconstrucción histórica del patrimonio y fiscalidad FIFO de cripto.

## Ideas que quedaron para el futuro

- **Avisos de presupuesto** (Telegram, ntfy o email) al llegar al 80 % y al 100 %.
- **Instancia por persona** para una pareja: mismo código, una hoja y un dashboard para cada uno, con la conjunta en las dos.
- **Hipoteca real** en la pestaña de Vivienda: TAE, vinculaciones, cuadro de amortización, amortizar frente a invertir.
- **Hosting sin esperas**: Google Cloud Run o Hugging Face Spaces en lugar de Streamlit Community Cloud.

## Bitácora

- …
- **7/11/2026:** modo demo con datos inventados (`demo/`, persona «Alex»), lanzador `Demo.bat`, desgloses de pagos únicos, paso de ficheros a conexión por API sin duplicar (p. ej. un banco que antes se subía en Excel), rentabilidad y capital invertido sin Portfolio Performance, compatibilidad con pandas 3.1.
