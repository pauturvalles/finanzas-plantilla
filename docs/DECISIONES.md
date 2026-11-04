# Criterios del sistema (fuente de verdad)

Cómo clasifica, calcula y muestra el sistema. Si el código y este documento no coinciden, hay que aclararlo antes de tocar nada.
Al personalizarlo, **anota aquí tus propias decisiones** (en la sección 13) para que tú, o un asistente de IA, sepáis siempre por qué un número sale como sale.

Los valores entre `comillas` son claves de `config/cuentas.yaml` o `config/costes.yaml`.

## 1. Cuentas y parte imputada

Cada cuenta tiene un `pct_titular`: la parte de cada gasto que te corresponde. Una cuenta compartida con tu pareja o con quien convivas va al **50 %** (`conjunta: true`). El resto, al 100 %.

## 2. Tipos de flujo

Cada movimiento tiene un **flujo**:

| Flujo | Qué es | ¿Cuenta como gasto? |
|---|---|---|
| Gasto | Consumo real | Sí |
| Ingreso | Nómina, intereses, dividendos, cashback, ventas de segunda mano | — |
| Traspaso interno | Entre cuentas propias; aportaciones a la conjunta | No |
| Ahorro / inversión | Aportaciones a fondos, planes de pensiones, brókers, cripto | No |
| Bizum personas | Bizums que no se pueden asociar a un gasto | Aparte, en neto |
| Compensación de compra | Bizum que devuelve una compra hecha para otra persona | No |

La **nómina** se asigna al mes que paga: si entra entre el día 1 y el 5, cuenta para el mes anterior.

## 3. Cuenta conjunta y reposiciones

- Todo gasto de la conjunta se imputa al 50 %. Las aportaciones de cada persona son **traspasos** (`Aportación <nombre> a conjunta`).
- **Reposiciones**: cuando una persona paga algo común de su bolsillo, la conjunta le devuelve el importe.
  - Si se encuentra el pago original del titular (mismo importe ±0,01 €, entre 3 días antes y 7 después, en cualquier cuenta suya o en Coverflex), ese pago queda **al 0 %** y la reposición hereda su categoría.
  - Si no, pero la transferencia lleva nota (Revolut la incluye: «Bon preu To …»), se clasifica por la nota con las mismas reglas.
  - Si no hay nada, queda en «Compartido: reposiciones <titular>/<pareja> (sin detalle)», gasto común al 50 %. **No es un pendiente.**
- Un reparto del dinero sobrante de la conjunta a las cuentas personales es un traspaso, no gasto (se marca con un ajuste).

## 4. Compensaciones por Bizum

- **Compras para otra persona** (típicamente Amazon): si llega un Bizum de alguien de `compensan_compras` por un importe parecido (±5 %) en ±3 días, la compra queda al 0 %.
- **Comidas pagadas por todos**: solo te imputas tu parte; los Bizums de amigos que llegan entre 1 día antes y 4 después restan dentro de Restauración. Los de `familia` nunca compensan.

## 5. Fuentes y prioridad

- **Una sola fuente por cuenta y día**: si la misma cuenta llega por API y por fichero, manda la API (`PRIORIDAD_FUENTES`). Así no hay duplicados al pasar de ficheros a conexión.
- **Excepción, Trade Republic**: Enable Banking no da el comercio, solo fecha e importe. Esos movimientos entran como **provisionales** («Pendiente de detalle (Trade Republic)»: cuentan en el gasto y en los presupuestos) y el **CSV de transacciones** los sustituye: todos los provisionales hasta el último día del CSV y, después, los del mismo importe a ≤ 4 días (la fecha contable va por detrás).
- **Identificadores estables**: cada movimiento tiene un `tx_id` (referencia del banco o hash de cuenta + fecha + importe + concepto + número de repetición). Subir el mismo fichero dos veces o ficheros solapados no duplica nada.
- **Nada se borra solo**: lo dudoso va a `pendientes`, `posibles_duplicados` o `conciliacion`.

## 6. Reglas y ajustes

- `reglas` (pestaña de la hoja): patrón (expresión regular, en MAYÚSCULAS y sin tildes) → flujo, categoría, subcategoría y tipo de gasto. Se prueban por prioridad (número más bajo primero) contra «concepto | contraparte» y contra el concepto solo (para que `^…$` funcione).
- `ajustes`: corrección de un movimiento concreto por su `tx_id`. Manda sobre las reglas.
- Desde el dashboard («Calidad del dato») se pueden crear las dos cosas sin tocar la hoja.

## 7. Gasto fijo, variable y extraordinario

- **Fijo**: recurrente (alquiler, fibra, seguros, suscripciones, cuotas).
- **Variable**: el resto del día a día.
- **Extraordinario**: puntual y grande (una boda, un viaje excepcional, una obra, el pago del IRPF). No cuenta en las medias ni en el «gasto normal». Se marca con un ajuste o una regla.

## 8. Ingresos y margen de ahorro

- **Ingresos del trabajo** = nómina neta + todo lo pagado con la retribución flexible (Coverflex) **en bruto**: es sueldo en especie; lo que consumes está en el gasto y lo que te reponen otros vuelve como dinero.
- **Margen de ahorro** = (ingresos del trabajo − gasto normal) ÷ ingresos del trabajo. Ventas, intereses y regalos van aparte como «otros ingresos».

## 9. Periodo analizado

- `clasificar_desde`: primer mes que clasifica el motor. `historico`: un año revisado a mano, opcional, que manda sobre el motor.
- El dashboard **excluye solo de las medias y comparativas** los meses en los que falta una cuenta principal (≥ 20 movimientos al mes de media) porque aún no había datos suyos. Así no se comparan meses en desigualdad de condiciones.

## 10. Inversiones y patrimonio

- **Mandan las fuentes oficiales** (APIs de Indexa, Kraken, Revolut X; CSV de MyInvestor y Trade Republic). Un registro manual (p. ej. Portfolio Performance) solo rellena huecos del pasado y se concilia.
- **MyInvestor** (sin API): participaciones = foto inicial (`fondos_inicial`) + compras de su CSV (con su fecha de valor) × valor liquidativo público. Si falta el CSV, se deducen las compras de las transferencias con los pesos de `myinvestor_fondos`.
- **Trade Republic (valores)**: FIFO por ISIN con su CSV; valor = títulos × último precio conocido.
- **Cripto**: saldos por API valorados con precios públicos; FIFO **global por activo** (los traspasos entre cuentas propias no son ventas; cada permuta cripto-cripto sí). Staking y unidades sin registrar: coste 0 (criterio conservador).
- **Coste fiscal**: el de la entidad (incluye lo que arrastran los traspasos de fondos). Indexa no lo da por API: se apunta en `costes_fiscales` (por categoría o por fondo) y se le suma lo aportado después.
- **Categorías de patrimonio**: Fondos Indexa, Plan de pensiones Indexa, Fondos MyInvestor, ETF y acciones Trade Republic, Cripto, Efectivo en cuentas (la conjunta al 50 %).
- **Rentabilidad**: valor de compra (en la plataforma actual), aportación real (coste fiscal), ganancia total = valor − aportación real, rentabilidad total y **TIR anual** (XIRR con las fechas de cada flujo). Por activo, la TIR mide desde que el activo está en su plataforma actual.
- **Evolución histórica**: `scripts/reconstruir_historico.py` reconstruye el valor mes a mes (participaciones × precios históricos + saldos de cuentas). Después, fotos diarias del pipeline.

## 11. Fiscalidad (orientativa, territorio común, 2025–2026)

- Escala del ahorro: 19 % hasta 6.000 €, 21 % hasta 50.000 €, 23 % hasta 200.000 €, 27 % hasta 300.000 €, 30 % a partir de ahí.
- Renta del año: ganancias y pérdidas de cripto (FIFO) − pérdidas netas de los 4 años anteriores; si el saldo es negativo, compensa hasta el 25 % de los rendimientos. Rendimientos = intereses, dividendos y cashback cobrados.
- Fondos: la gestora calcula la ganancia y retiene al reembolsar; aquí solo se estima con el coste fiscal.
- Deducciones: plan de pensiones individual hasta 1.500 €/año (ahorro ≈ aportación × `tipo_marginal`); donativos a entidades de la Ley 49/2002: 80 % de los primeros 250 €, 40 % del resto (45 % si son recurrentes).
- **Todo es orientativo**: para declarar, un asesor.

## 12. Cálculos del dashboard

- **Este mes**: proyección = gastado + media de cada categoría × parte del mes que queda. **Cierre del mes anterior**: frente a la media de los 6 meses previos; comercios nuevos; cargos inusuales (> 30 €, más del doble de la mediana de su categoría y por encima de su percentil 95); posibles duplicados (mismo comercio e importe a ≤ 3 días).
- **Dónde ahorrar**: gasto hormiga (compras ≤ umbral, frecuentes, anualizadas); recurrentes (gasto fijo o el mismo importe en ≥ 3 meses, con su frecuencia); «¿y si…?» (ahorro y su valor invertido).
- **Costes de inversión**: gestión + custodia + TER de `costes.yaml`, en €/año y su efecto acumulado.
- **Proyección**: patrimonio + aportación mensual a la rentabilidad media de tu reparto, con banda de ±2 puntos y en euros de hoy.

## 13. Tus decisiones

Anota aquí lo que decidas al personalizarlo (casos especiales, comercios ambiguos, extraordinarios, por qué una cuenta va a un % distinto…).

- …
