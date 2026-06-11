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

## Pasada 2 — relectura profunda + cobertura de tests

| Archivo | Hallazgo | Gravedad | Estado |
|---|---|---|---|
| `insights.py` | Un accesorio caro en categoría "TV Mounts & Stands" sin palabra de accesorio en la descripción contaba como TV "seguro" (la exclusión solo miraba la descripción) | Media | ✅ Corregido + test |
| `insights.py` | Anotación `-> tuple` sin parametrizar; redacción del quick-read ("3 de granel", titular de regalados sin distinguir seguros de dudosos) | Cosmética | ✅ Corregido |
| `pipeline.py` | **La lógica de ventanas T-30/T-5 no tenía ni un test** (lo más crítico de cara al usuario) | Alta (riesgo) | ✅ 6 tests nuevos: dispara una vez en ventana, sin duplicados, nada fuera de ventana ni tras el cierre, última llamada solo si ≤10%, degradación a T-30 tardío, exclusión por umbral |
| `storage.py` | La migración de BD antigua tampoco tenía test | Media (riesgo) | ✅ Test con esquema original real |
| `scripts/` | `run_monitor_hidden.vbs` huérfano (sustituido por el genérico `run_hidden.vbs`; verificado que la tarea programada usa el nuevo) | Cosmética | ✅ Eliminado |
| `README.md` | Ejemplo del CLI con `--pct 0.25` antiguo | Cosmética | ✅ Corregido |
| `.env.example` ↔ `config.py` | Paridad verificada: todas las variables documentadas existen y con los mismos defaults | — | ✅ Coherente |
| `reports.py` | Relectura completa tras el fix de `try/finally`; verificado en ejecución real (watch + digest funcionaron en vivo) | — | ✅ Limpio |

**Estado final: 64 tests, 0 warnings, todos los módulos revisados al menos
una vez línea a línea, los críticos dos veces.**
