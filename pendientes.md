# Pendientes

Actualizado: 12/06/2026. El sistema está operativo y desatendido; esto es lo
que queda abierto, por prioridad.

## Necesitan algo de Guillem

1. **Activar la llamada de voz (T-5)**: enviar `/start` al bot de Telegram
   `@CallMeBot_txtbot` (enlace enviado por WhatsApp el 12/06). El sistema ya
   está configurado con el +34601033998 y reintenta solo; en cuanto autorices,
   funciona. Sin esto, a ≤5 min solo llega el WhatsApp de última llamada.
2. ~~**Credenciales de B-Stock** para los manifiestos `MIXED_*`.~~ **RESUELTO
   (2026-07-03): los MIXED son PÚBLICOS, no requieren sesión.** El bloqueo real
   era un bug de capitalización del sku: `parse_lot_id` hacía `.upper()`, pero el
   endpoint de manifiestos es *case-sensitive* por tipo de lote (`ESBX`→MAYÚSCULAS,
   `Mixed`→Title-case), así que `MIXED_005` redirigía a `/oops` (parecía muro de
   auth). Arreglado con `client.sku_candidates` (prueba variantes de capitalización).
   Se construyó además un **login Playwright opcional** (`cli login`, credenciales en
   Doppler `liquidation-tracker/prd`, FusionAuth SSO sin 2FA), pero quedó **latente e
   innecesario** — fuera del flujo normal, por si algún endpoint futuro sí exige sesión.
3. **Manifiesto del camión A2Z49096** (tu última compra, 2.600 artículos):
   no está cargado en la tabla `manifest` de la BBDD ni en la carpeta MEGA.
   Si aparece el CSV, analizarlo (`python -m liquidation_tracker.cli inspect`).

## Mejoras técnicas pendientes

4. **Precio real por ASIN — hecho a medias (31-jul-2026), falta la fuente fiable.**
   Ya no se raciona: `deep_analyze` llama a `PriceResolver.prewarm()` y calienta
   la caché con TODO el manifiesto antes de juzgar nada, y las resoluciones que ya
   están en caché/BD no gastan presupuesto. Efecto medido en el lote 54293: pasó de
   "2 regalados por 880 €" a **1 regalado real de 1.697 €** (MacBook Pro M4 tasado a
   600 € por el "típico" cuando vale 1.713) y el falso positivo de la tapa de objetivo
   (300 € inventados, vale 8,99) **se cayó solo**.
   Lo que falta es la **cobertura**: la BD de Reusalia (paso 2, arreglado — apuntaba a
   una carpeta archivada) resuelve el **29 %** de un lote nuevo gratis, y el scraping
   directo **no es sostenible**: 445 ASIN a 10 hilos salieron limpios y ~20 min después
   la IP estaba con captcha. Sigue en pie valorar **Keepa (~19 €/mes)**; es la única vía
   para tener el 100 % del manifiesto verificado todos los días.
5. **PDF del digest diario con el diseño nuevo**: el estudio de camiones ya
   usa el diseño dashboard (`scripts/render_estudio_pdf.py`); portar ese
   estilo (KPIs, semáforo) a `reports.py::build_digest_pdf` para los emails
   de las 9/12/21h.
6. **Camiones atípicos y baseline**: el criterio híbrido (anómala para su
   categoría Y para su lote) corta los falsos positivos en lotes raros
   (ej. A2Z38018, juguetes), pero el umbral local (35% de la mediana, suelo
   4) merece calibrarse con más feedback real.
7. **Pallets con 5 de 6 cajas**: se marcan todos. Sale a menudo (283 de
   2.651 históricos): decidir si marcar solo ≤4 para reducir ruido.
8. **Dudas de producto** en `docs/DUDAS-manifiestos.md` (umbrales, monitores
   y proyectores como pérdida, lista de marcas premium...).
9. **Artículos baratos (<10€) en manifiestos**: nuevas métricas en el análisis
   de manifiestos. Para cada camión, detectar y reportar:
   - Cantidad de artículos con precio lista < 10€
   - Valor total que representan esos artículos
   - % del total del camión que son "basura de bajo valor"
   
   Justificación: estos artículos tienen muy alto riesgo de no venderse nunca
   en el almacén/canal de Reusalia (poco margen, pto de venta débil) y son
   casi pérdida pura → decisión de puja se debe penalizar si el lote lleva
   mucho volumen de estos.

## Operativa (referencia rápida)

- Tareas programadas Windows: monitor 12:30-16:00 cada minuto; watch lotes
  nuevos cada 15 min (WhatsApp); digest email 9:00/12:00/21:00.
- Config en `.env` (no está en git): CallMeBot WhatsApp + llamada, SMTP
  Gmail, reglas (12%/15% electrónica, mínimos 20k/50k/100k por tipo).
- Baseline por categoría: `data/baselines.json` (commiteado); regenerar con
  `python scripts/build_baselines.py <carpetas>`.
- Estudio de camiones propios: `python scripts/estudio_nuestros.py` y
  `python scripts/render_estudio_pdf.py`.
- En el otro PC: clonar, `pip install -r requirements.txt`, copiar `.env`
  (pedírselo a Claude o rellenar desde `.env.example`), y recrear las tareas
  programadas si se quiere monitorizar desde allí (comandos en README).
