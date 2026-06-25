# Pendientes

Actualizado: 12/06/2026. El sistema está operativo y desatendido; esto es lo
que queda abierto, por prioridad.

## Necesitan algo de Guillem

1. **Activar la llamada de voz (T-5)**: enviar `/start` al bot de Telegram
   `@CallMeBot_txtbot` (enlace enviado por WhatsApp el 12/06). El sistema ya
   está configurado con el +34601033998 y reintenta solo; en cuanto autorices,
   funciona. Sin esto, a ≤5 min solo llega el WhatsApp de última llamada.
2. **Credenciales de B-Stock** (usuario/contraseña): para descargar los
   manifiestos `MIXED_*` que requieren sesión (≈1/3 de los lotes). Si
   Cloudflare bloquea el login con requests, el plan B es cliente Playwright
   (la arquitectura ya lo permite: 3 métodos en `client.py`).
3. **Manifiesto del camión A2Z49096** (tu última compra, 2.600 artículos):
   no está cargado en la tabla `manifest` de la BBDD ni en la carpeta MEGA.
   Si aparece el CSV, analizarlo (`python -m liquidation_tracker.cli inspect`).

## Mejoras técnicas pendientes

4. **Scraping de Amazon para cajas sospechosas**: pedido por Guillem
   ("hacerlo siempre para ese tipo de cajas"). Hoy el informe lista el
   contenido con enlaces; falta el fetch automático de dimensiones/precio
   por ASIN. Amazon bloquea bots: valorar API de Keepa (~19 €/mes, fiable)
   antes de invertir en scraping frágil. `verify_giveaway_prices()` en
   `insights.py` ya es el gancho experimental.
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
