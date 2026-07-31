# Liquidation Auction Tracker

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

## Convenciones

- Respuestas en **español**. Cambios mínimos, sin refactors fuera de alcance.
- Ver `README.md` (arquitectura completa), `GUIA.md` y `pendientes.md`.
