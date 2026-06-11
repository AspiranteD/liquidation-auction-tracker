# Revisión archivo por archivo — bugs anotados y corregidos

Pasada sistemática sobre todo el proyecto (11/06/2026). Cada archivo, lo
encontrado y su estado. Los arreglos están commiteados; los tests (56+)
pasan tras cada cambio.

## Pasada 1 — código de producción (`liquidation_tracker/`)

| Archivo | Hallazgo | Gravedad | Estado |
|---|---|---|---|
| `reports.py` | Si Cloudflare/red fallaba a mitad de un escaneo, `save_state()` no se ejecutaba y se perdían las marcas "ya avisado" → **WhatsApps duplicados** en la siguiente pasada | Alta | ✅ Corregido (`try/finally`) |
| `cli.py` | Errores de red transitorios sin capturar en `monitor`/`watch`/`digest` → traceback sucio en las tareas programadas cada minuto | Media | ✅ Corregido (captura `requests.RequestException`, exit 2 limpio) |
| `cli.py` | `bid --pct` y el ejemplo de la docstring seguían con el 0.25 antiguo (incoherente con la estrategia del 12%) | Baja | ✅ Corregido (0.12) |
| `cli.py` | `import datetime` dentro de `cmd_digest` en vez de en cabecera | Cosmética | ✅ Corregido |
| `models.py`, `storage.py` | `datetime.utcnow()` obsoleto (warning en cada test; timestamps naive) | Baja | ✅ Corregido (`datetime.now(timezone.utc)`) |
| `client.py` | `download_manifest` no respetaba la pausa de cortesía entre peticiones (sí lo hacía el resto del cliente) | Baja | ✅ Corregido |
| `notifier.py` | Mensajes WhatsApp sin límite de longitud: CallMeBot los manda por querystring GET y un texto muy largo podría dar error 414 | Baja | ✅ Corregido (truncado a 1800) |
| `__init__.py` | Docstring desactualizada (solo mencionaba email) | Cosmética | ✅ Corregido |
| `parser.py` | Bug "4 Pallets of" → transporte 0 € (detectado en pruebas en vivo, sesión de mañana) | Alta | ✅ Ya corregido + test de regresión |
| `insights.py` | Falsos positivos: cámaras de vigilancia como "objetivos", menciones de compatibilidad ("para iPhone"), accesorios de TV, pallets de objetos grandes como "cajas vacías" (detectados con manifiestos reales) | Media | ✅ Ya corregidos + tests |
| `reports.py` | `multi_cell` de fpdf2 ≥2.8 dejaba el cursor a la derecha → crash "Not enough horizontal space" | Alta | ✅ Ya corregido |
| `alerts.py`, `calculator.py`, `config.py`, `analyzer.py`, `pipeline.py` | Revisados línea a línea: sin bugs encontrados en esta pasada | — | ✅ Limpios |
| `examples/demo.py` | Revisado; funciona offline | — | ✅ Limpio |

## Pendiente para la pasada 2

- Relectura desde disco de `insights.py` y `reports.py` completos (son los más
  nuevos y largos).
- Revisión de los tests en sí (cobertura de huecos: pipeline con ventanas
  T-30/T-5, storage con migración sobre BD vieja).
- Coherencia `.env.example` ↔ `config.py` ↔ README.
- Scripts de `scripts/` y verificación de las 5 tareas programadas.
