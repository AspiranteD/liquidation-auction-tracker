# Guía completa del sistema — qué hace y cómo usarlo

Escrito el 12/06/2026. Este documento explica todo lo construido, para qué
sirve cada pieza y cómo trabajar desde otro PC. Lo pendiente vive en
[pendientes.md](pendientes.md); las dudas de producto en
[docs/DUDAS-manifiestos.md](docs/DUDAS-manifiestos.md).

---

## 1. Qué es esto

Sistema automático que vigila las subastas de camiones de liquidación de
**Amazon EU en B-Stock** (bstock.com/amazoneu) y:

1. **Avisa por WhatsApp** cuando una subasta interesante está a punto de
   cerrar (escalera de recordatorios) y **llama por Telegram** en el último
   momento.
2. **Analiza los manifiestos** (CSV de contenido de cada camión) buscando
   lo que de verdad importa: productos **regalados** (mal clasificados a
   precio absurdo), **cajas enteras sin declarar**, **cajas demasiado
   vacías** (esconden contenido) y **TVs** (siempre rotas = pérdida).
3. **Envía informes en PDF por email** tres veces al día y al detectar
   lotes nuevos.

Todo corre solo en el PC de casa con tareas programadas de Windows.

## 2. Las reglas de compra (configuradas en `.env`)

- Solo subastas de **España** de tipo **4 Pallets** (retail ≥ 20.000 €),
  **Small Truckload** (≥ 50.000 €) o **Truckload** (≥ 100.000 €).
- Interesa si el **COSTE TOTAL** (puja + transporte + IVA 21% + fee B-Stock
  4% + recargo de equivalencia 5,2%) queda **≤ 12% del retail** — o **≤ 15%
  si el lote lleva electrónica** (Wireless, PC Goods, cámaras...). La puja
  en sí suele quedar entonces en el 5-10% del retail.
- La **calculadora de puja máxima** resuelve el modelo de costes al revés:
  te dice cuánto puedes pujar como máximo para no pasarte del techo.

## 3. Alertas de pujas (monitor)

- Corre **cada minuto de 12:30 a 16:00** (ahí se concentran los cierres).
- **Escalera de recordatorios** por WhatsApp a **30, 15, 10 y 5 minutos**
  del cierre, siempre evaluando con la puja de ese momento: si el lote ya
  supera el techo del 12/15%, no avisa (y no vuelve a avisar).
- A **≤5 minutos**: además del WhatsApp de última llamada, **llamada de
  voz** vía CallMeBot que suena en Telegram y lee la alerta (puja actual,
  % de coste, máximo recomendado). ⚠️ Requiere autorizar una vez al bot
  `@CallMeBot_txtbot` con `/start` (ver pendientes.md).
- Cada subasta y cada etapa avisan **una sola vez** (deduplicado en SQLite,
  tabla `alert_log`).

## 4. Análisis de manifiestos (el cerebro)

Módulo `liquidation_tracker/insights.py`. Detecta:

### Regalados (productos premium a precio absurdo)
- Lista de productos premium (iPhone, MacBook, iPad, Watch, Galaxy, Pixel,
  PS5, Xbox, Switch, RTX, Dyson, GoPro, DJI, objetivos de cámara...) con
  precio típico mínimo conservador.
- Dos niveles: **seguro** (declarado < 10% del típico) y **dudoso**
  (< 40%, verificar con el enlace de Amazon que lleva cada hallazgo).
- Protecciones contra falsos positivos (todas validadas con datos reales):
  accesorios ("funda para iPhone"), menciones de compatibilidad
  ("auriculares para PS5"), periféricos gaming, cámaras de vigilancia,
  cables en alemán... La regla clave es **posicional**: una palabra de
  accesorio solo descarta si va ANTES del producto en el título.
- Ejemplo real cazado: 2× Samsung Galaxy S24 Ultra declarados a **8,51 €**.

### Cajas y pallets (estructura física)
- Cada pallet se clasifica: **de cajas** (≈6 cajas de Amazon apiladas),
  **objetos grandes** (1-3 unidades pesadas: normal, no se marca) o
  **granel**.
- **Pallet de cajas con menos de 6 declaradas → las que faltan
  probablemente van REGALADAS** (regla confirmada empíricamente: el 79% de
  2.651 pallets históricos llevan exactamente 6).
- **Caja demasiado vacía**: pocos objetos para lo normal de SU CATEGORÍA
  (baseline histórico de 24.256 cajas reales: Motor 38-106 objetos/caja,
  Electrónica 79-161, Muebles ~7...) Y de su propio lote, sin que el peso
  lo justifique (pocos objetos voluminosos también llenan una caja). El
  valor declarado NUNCA es criterio. El informe lista el contenido
  declarado de cada caja sospechosa con enlaces.
- Baseline regenerable: `python scripts/build_baselines.py <carpetas>` →
  `data/baselines.json` (commiteado).

### TVs
- Siempre llegan rotas: su retail se descuenta → **retail efectivo** (la
  cifra buena para calcular la puja). Detección por categoría o
  pulgadas/panel, con suelo de 100 € y excluyendo soportes, mandos,
  barras de sonido y Fire TV sticks.

## 5. Informes

- **Watch** (cada 15 min, 24/7): detecta subastas nuevas → analiza su
  manifiesto → genera PDF + markdown → te manda resumen por WhatsApp.
- **Digest** (9:00, 12:00 y 21:00): un PDF combinado de todos los lotes
  activos, **enviado por email** con resumen en el cuerpo.
- Cada informe abre con **"Lectura rápida"**: bullets en claro con cifras
  ("X objetos que podrían venderse por ~Y € están declarados por Z €").
- **Estudio de camiones propios**: `scripts/estudio_nuestros.py` (analiza
  los manifiestos de la carpeta MEGA + los exportados de la BBDD) y
  `scripts/render_estudio_pdf.py` (PDF ejecutivo con portada dashboard:
  KPIs, ranking con semáforo, detalle por camión). El del 12/06 se envió
  por email: 18 regalados seguros (≥5.793 €) y 79 cajas sin declarar en
  27 camiones.

## 6. Lo que corre solo en el PC de casa (Windows)

| Tarea programada | Cuándo | Qué hace |
|---|---|---|
| Bstock Liquidation Tracker | 12:30-16:00, cada minuto | monitor de pujas + alertas |
| Bstock Manifest Watch | cada 15 min | lotes nuevos → PDF + WhatsApp |
| Bstock Digest 09/12/21 | 9:00, 12:00, 21:00 | email con PDF combinado |

Los `.cmd`/`.vbs` están en `scripts/` (sin ventanas; logs en `logs/`).
El PC debe estar encendido a las 12:30 para la ventana del monitor.

## 7. Montarlo en otro PC

```bash
git clone https://github.com/AspiranteD/liquidation-auction-tracker.git
cd liquidation-auction-tracker
pip install -r requirements.txt
copy .env.example .env   # y rellenar (ver abajo)
python -m pytest -q      # 75 tests deben pasar
```

**El `.env` NO está en git** (lleva credenciales). Opciones: copia el del
PC de casa (`C:\Users\guill\Claude\liquidation-auction-tracker\.env`) por
MEGA/USB, o rellena `.env.example` con: API key de CallMeBot WhatsApp,
teléfono, App Password de Gmail y usuario de Telegram para llamadas.

Si quieres que el OTRO PC también monitorice, recrea las tareas (ajusta la
ruta):

```powershell
schtasks /create /tn "Bstock Liquidation Tracker" /tr "wscript.exe \"<RUTA>\scripts\run_hidden.vbs\" run_monitor.cmd" /sc daily /st 12:30 /ri 1 /du 03:30 /f
schtasks /create /tn "Bstock Manifest Watch" /tr "wscript.exe \"<RUTA>\scripts\run_hidden.vbs\" run_watch.cmd" /sc minute /mo 15 /f
schtasks /create /tn "Bstock Digest 09" /tr "wscript.exe \"<RUTA>\scripts\run_hidden.vbs\" run_digest.cmd" /sc daily /st 09:00 /f
# (idem Digest 12 y Digest 21)
```

⚠️ Si los dos PCs corren a la vez recibirás avisos duplicados (cada uno
tiene su SQLite). Para cambiar de PC: deshabilita las tareas en el viejo
(`schtasks /change /tn "<nombre>" /disable`).

## 8. Comandos útiles (CLI)

```bash
python -m liquidation_tracker.cli list --country ES      # subastas activas + puja máx
python -m liquidation_tracker.cli bid --retail 50000 --type "4 Pallets"  # calculadora
python -m liquidation_tracker.cli monitor                # un ciclo de alertas
python -m liquidation_tracker.cli inspect <csv>          # análisis profundo de un manifiesto (md + PDF)
python -m liquidation_tracker.cli manifests --country ES # baja y analiza todos los activos
python -m liquidation_tracker.cli watch                  # detectar lotes nuevos + WhatsApp
python -m liquidation_tracker.cli digest                 # PDF combinado + email
```

## 9. Estructura del código

```
liquidation_tracker/
├── client.py      # red B-Stock (requests; swap a Playwright si Cloudflare)
├── parser.py      # HTML → subastas
├── calculator.py  # modelo de costes y puja máxima
├── alerts.py      # ¿califica la subasta? (umbrales, mínimos por tipo)
├── pipeline.py    # escalera de recordatorios + llamada
├── notifier.py    # WhatsApp (CallMeBot), email (SMTP), llamada (Telegram)
├── analyzer.py    # parseo de manifiestos (encoding + mojibake reparado)
├── insights.py    # análisis profundo (regalados, cajas, TVs, baselines)
├── reports.py     # PDFs por lote, digest, estado del watch
├── storage.py     # SQLite (subastas, histórico de pujas, alert_log)
└── cli.py         # todos los comandos
scripts/           # tareas programadas + build_baselines + estudio propio
data/baselines.json  # estadística por categoría (24k cajas) — commiteado
tests/             # 75 tests (pytest -q)
```

## 10. Historial de validación (por qué fiarse)

- Probado en vivo durante 2 días: recordatorios T-30/T-5 reales entregados,
  8 lotes nuevos detectados y notificados, digests enviados.
- **Agente revisor independiente** (sin contexto) auditó la lógica contra
  los 13 manifiestos reales: sus 11 hallazgos están corregidos y blindados
  con tests de regresión (registro en [docs/REVISION.md](docs/REVISION.md)).
- Baseline construido con 2.765 manifiestos históricos tuyos.
- Caso real que justifica el sistema: la lógica inicial infravaloraba las
  TVs del lote 50865 en ~11.500 € — la actual encuentra las 36.
