# Liquidation Auction Tracker

> # 🔴 ABSORBIDO por `reusalia-backend` (2026-08-17) — decisión del dueño (§7.9 del plan del Panel de Socios)
>
> Lo que este repo hacía en el PC de Guillem **ya corre en el servidor** (`reusalia-backend`),
> con los datos en la BD y la operativa en la pestaña **Pujas** de `frontend-socios`
> (`socios-reusalia.pages.dev/pujas`). Piezas y a dónde fueron:
>
> | Aquí (tracker) | Ahora (backend) |
> |---|---|
> | `data/manifests/*.csv` (182) | `bstock_manifest_item` (los 182 importados; el monitor carga los nuevos) |
> | `insights.deep_analyze` (TVs, cajas, regalados, departamentos) | `scripts/services/bstock/analisis_lote.py` → `bstock_lot_analisis` (job tras `bstock_monitor`) |
> | `scripts/build_recovery.py` → `data/recovery.json` | `scripts/services/bstock/recovery.py` → `bstock_recovery_departamento` (job semanal, sin TVs) |
> | `calculator.py` (9 %) | `scripts/services/bstock/calculator.py` + espejo JS `frontend-socios/pujas-calc.js`; el % es del dueño (`config_empresa['bstock_pct_objetivo']`, 10 %) |
> | `pipeline.py` (escalera 30/15/10/5, WhatsApp) | job `puja_recordatorio` (T-30, WhatsApp + email, una vez) |
> | `digest` (PDF 09/12/21) | correo `pujas_del_dia` de las 09:00 **con PDF adjunto** (`pujas_pdf.py`), uno al día |
> | `watch` (lotes nuevos) | `bstock_monitor` cada 6 h + sondeo de ids nuevos (Buy Now aparte) |
>
> ⛔ **Las tareas programadas de Windows de este repo (`Bstock Liquidation Tracker`, `Bstock Manifest
> Watch`, `Bstock Digest 09/12/21`) se DESACTIVAN**: dos sistemas mandando avisos = avisos duplicados
> y dos verdades. Este repo queda como **motor de referencia** (tests, análisis a mano con `cli lot`);
> su `calculator.py` es un ESPEJO del del backend (país + N palés, test `test_espejo_backend_n_pallets_y_pais`).
> Si vas a cambiar una regla de valoración, cámbiala en el backend y trae aquí la copia.

Pipeline autónomo que monitoriza **subastas de liquidación de Amazon EU en B-Stock**
(https://bstock.com/amazoneu/), descarga los manifiestos de los lotes, calcula
rentabilidad (coste real con transporte, IVA, fee de marketplace y recargo de
equivalencia) y **avisa por email/WhatsApp** cuando un lote cumple tus criterios.
**No puja** — solo monitoriza y aconseja la puja máxima para un margen objetivo.

## Stack

- **Python** + **SQLite** (`data/auctions.db`). Scraping con `requests` + `BeautifulSoup`.
- CLI: `python -m liquidation_tracker.cli <comando>`.
- Sin dependencias externas más allá del sitio público de B-Stock y (opcional) SMTP/WhatsApp.

## Cómo arrancar

```powershell
pip install -r requirements.txt   # usa el python GLOBAL (las tareas no activan venv)
python -m liquidation_tracker.cli monitor   # un ciclo de monitorización
python -m liquidation_tracker.cli digest    # PDF combinado de lotes activos por email
```

Los `.cmd` en `scripts/` (`run_monitor.cmd`, `run_digest.cmd`, `run_watch.cmd`)
hacen `cd` a la raíz y llaman a la CLI escribiendo en `logs/`. Los lanza el
Programador de tareas vía `scripts/run_hidden.vbs` (sin ventana de consola).

## Estructura

| Carpeta | Qué hay |
|---------|---------|
| `liquidation_tracker/` | Código (parser, calculadora de puja, analizador de manifiestos, CLI) |
| `scripts/` | Lanzadores `.cmd`/`.vbs` y utilidades (`estudio_nuestros.py`, `recomendador_camiones.py`…) |
| `data/` | **NO en git**: `auctions.db`, `manifests/`, `nuestros/`. Datos vivos — respaldar aparte |
| `logs/` | Logs de monitor/digest/watch (se regeneran solos) |
| `docs/`, `examples/`, `tests/` | Documentación, ejemplos y tests |

## ⚠️ Tareas programadas de Windows (Task Scheduler)

Estas tareas apuntan a rutas **fijas** dentro de esta carpeta:

- `Bstock Digest 09 / 12 / 21` → `scripts\run_hidden.vbs run_digest.cmd`
- `Bstock Liquidation Tracker` → `scripts\run_hidden.vbs run_monitor.cmd`
- `Bstock Manifest Watch` → `scripts\run_hidden.vbs run_watch.cmd`

**Si mueves o renombras esta carpeta, esas tareas se rompen** (saltará el error
"No se encuentra el archivo de comandos run_hidden.vbs"). Ruta esperada:
`C:\Users\guill\Claude\liquidation-auction-tracker\`.

## Secretos y datos

- **Los secretos viven en Doppler** (`liquidation-tracker/prd`), no en un `.env` que haya que
  pasarse por USB. Este repo **no materializa `.env`**: `config.py` los lee del entorno, así
  que ejecuta siempre con **`doppler run -- python -m liquidation_tracker.cli …`**.
  `doppler.yaml` (commiteado, sin secretos) declara proyecto+config → un PC nuevo solo necesita
  `doppler setup --no-interactive` y un service token. Un `.env` local sigue funcionando como
  fallback (`load_dotenv()`), pero ya no es el camino.
- **Alertas apagadas = secretos que faltan.** `config.py` lee `EMAIL_ALERTS_ENABLED` y compañía
  con default `"false"`: si no están en Doppler, las alertas **no fallan, simplemente no salen**.
  Para rellenarlas sin exponerlas: `python scripts/set_alert_creds.py`.
- La carpeta `data/` (DB + manifiestos) tampoco está en git: es estado vivo, respaldar aparte.
- **Login B-Stock (OPCIONAL, latente):** `cli login` autentica vía Playwright (FusionAuth SSO)
  con `BSTOCK_USER`/`BSTOCK_PASS`, guardados en **Doppler `liquidation-tracker/prd`**. Cachea la
  cookie en `data/bstock_cookie.json` (gitignorado). **No hace falta para nada del flujo normal**
  (ver gotcha de MIXED). Para meter las credenciales sin exponerlas: `python scripts/set_bstock_creds.py`.

## Gotchas (leer antes de tocar descargas de manifiestos)

- **Los manifiestos MIXED son PÚBLICOS** — NO requieren login. Lo que fallaba era la
  **capitalización del sku**: el endpoint `manifest-prod.bstock.com/downloads/get` es
  *case-sensitive* por tipo de lote (`ESBX…`→MAYÚSCULAS, `Mixed`→Title-case). `parse_lot_id`
  normaliza a mayúsculas y `client.sku_candidates` prueba las variantes (upper → tipo en
  Title-case) hasta obtener CSV. Si un tipo nuevo falla, añade su variante ahí.
- **`fpdf2` es obligatorio** para `inspect`/`digest`/`watch` (generan PDF). Está en
  `requirements.txt`; si ves `ModuleNotFoundError: fpdf`, `pip install -r requirements.txt`.
- 🧨 **Amazon bloquea CON RETARDO: una tanda limpia no prueba nada.** El 31-jul-2026, 445 ASIN
  a 10 hilos y sin pausa dieron **441 respuestas 200 y cero bloqueos**… y ~20 min después los
  mismos ASIN devolvían captcha desde la misma IP. Si ves un pase limpio, **no subas
  `prewarm(workers=…)`**: el default conservador (3 hilos, respetando `scrape_delay`) es a
  propósito. La cobertura barata la da el **paso 2 del resolver (BD de Reusalia)**, que cubre
  el **29 %** de los ASIN de un lote nuevo gratis; para cobertura total a diario, la vía es
  Keepa, no más hilos. Ojo: `ReusaliaDB` se traga el `OSError` si no encuentra el `.env` y se
  marca fallida **en silencio** — `DEFAULT_ENV` estuvo apuntando a una carpeta archivada y el
  paso 2 llevaba muerto sin avisar, con todo cayendo en el "típico" inventado.
- **Detector de regalados** (`insights.py`): se fía de la **taxonomía del manifiesto**
  (category/subcategory) para descartar juegos/accesorios (p.ej. "PS5 Games", "Controllers"),
  igual que la detección de TVs. El heurístico "1 caja → 5 regaladas" solo aplica si el lote
  tiene algún pallet multi-caja real (los MIXED mapean 1 caja por pallet y lo inflaban).
- 🔴 **El formato del pallet lo DECLARA el manifiesto en su columna `FC` — no se deduce del peso.**
  `MAD4` = pallet de cajas (siempre 6 físicas); `MAD6`/`XMA8`/`MXP5`/`XMP3` = pallet entero de un
  solo bulto. Medido sobre **731 pallets de 103 manifiestos**, 0 excepciones, y ningún MAD4 declara
  más de 6. ⛔ **No vuelvas a clasificar por unidades/peso**: esa heurística fallaba en las **dos**
  direcciones y con dinero de por medio — en el lote 54360 se **inventaba** 5 cajas (7.699 EUR) sobre
  un MAD6, y en el 54639 se **dejaba sin contar** 5 cajas reales (3.278 EUR) porque sus artículos
  pesaban demasiado. Un `FC` que no esté en esas listas cae a la heurística de siempre, no se adivina.
- 🔴 **Dos parámetros de coste que estaban mal y hacían pujar de MÁS en todos los lotes:**
  el **fee de B-Stock era 4 %** cuando la propia ficha lo publica en un campo oculto
  (`buyersPremiumPercent`, leído **0,05** el 2026-08-05 — subió en 2026); y el **transporte de
  un lote de 1-3 palés** no casaba con ninguna tarifa, así que entraba a **0 €**. Un lote de
  2 palés y 20.160 € de retail daba una puja máxima de **1.365 €** cuando son **659 €**. Regla:
  **1-3 palés se facturan como 4** (se paga el hueco del camión). El fee vive **en espejo** con
  `scripts/services/bstock/calculator.py` del backend.
- 🔴 **La regla de compra del dueño es el 9 % de coste total** (`TARGET_COST_PCT` en `ranking.py`,
  columna `PUJA_9%`), y ese número se pega en el campo nativo **«Your Maximum Bid»** de B-Stock.
  ⛔ **No montes un bot de pujas**: la puja automática ya la hace la plataforma; automatizarla por
  fuera solo añade riesgo de cuenta. La base del 9 % es el retail **sin TVs**.
- 🔴 **Un lote que NO sale en el listado suele ser de PRECIO FIJO («Buy Now»), no uno cerrado.**
  Esos no se subastan, así que nunca aparecen en `list_auctions`, y antes el informe los daba por
  **«sin pujas» y en VERDE**: el 54639 salía como chollo cuando su precio era **fijo, 10.180 EUR,
  casi el doble** del máximo recomendado (5.670). El precio se lee de la propia ficha
  (`parse_is_fixed_price` / `parse_headline_price`, funcionan **sin login**) y el veredicto pasa a
  ROJO. ⚠️ En un Buy Now no hay nada que esperar: el número es final, no una puja que aún tiene aire.

## Convenciones

- Respuestas en **español**. Cambios mínimos, sin refactors fuera de alcance.
- Ver `README.md` (arquitectura completa), `GUIA.md` y `pendientes.md`.
