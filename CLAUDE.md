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

- `.env` está en `.gitignore` (**no** está en GitHub ni en Doppler). Restaurar desde backup.
- La carpeta `data/` (DB + manifiestos) tampoco está en git: es estado vivo, respaldar aparte.

## Convenciones

- Respuestas en **español**. Cambios mínimos, sin refactors fuera de alcance.
- Ver `README.md` (arquitectura completa), `GUIA.md` y `pendientes.md`.
