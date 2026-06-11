# Análisis de manifiestos — dudas y decisiones pendientes

Estado: el módulo está construido, testeado (47 tests) y validado con los 5
manifiestos reales descargables de las 8 subastas ES activas a 11/06/2026.
Informes generados en `data/reports/`. Estas son las dudas que quedan para
pulir juntos.

## 1. Manifiestos que requieren login (3 de 8)

Los lotes `MIXED_*` (51469, 50868, 51467) devuelven HTML en vez de CSV: el
endpoint de manifiestos solo los sirve con **sesión autenticada de B-Stock**.
Los `ESBX*` bajan sin login.

**Pregunta:** ¿tienes cuenta de B-Stock? Si me pasas las cookies de sesión (o
usuario/contraseña) añado login al cliente y cubrimos el 100%.

## 2. Umbrales de "caja/pallet sospechoso" (calibrados con 5 manifiestos)

Regla actual: contenedor sospechoso si sus unidades ≤ 35% de la mediana del
lote (con suelo absoluto: 4 uds caja, 15 uds pallet), o si su valor declarado
≤ 35% de la mediana. En los lotes reales esto marca 1-5 cajas por lote.

- Las cajas reales llevan 12-61 uds; tu ejemplo de "caja con 2 productos"
  saltaría clarísimo.
- También se marca la caja con valor anómalamente bajo aunque tenga unidades
  normales (ej. caja spNVKDLPNPY del lote 51546: 47 uds pero 1.564 € vs
  mediana 8.411 €) — ahí es donde suelen esconderse los regalados.

**Pregunta:** ¿35% te parece bien o lo quieres más/menos sensible? Se cambia
en `InsightRules` ([insights.py](../liquidation_tracker/insights.py)).

## 3. Lista de productos premium y sus precios "típicos"

`PREMIUM_PRODUCTS` en insights.py: iPhone, MacBook, iPad, Apple Watch,
AirPods, Galaxy S/Z/Tab, Pixel, PS5, Xbox, Switch, RTX, Dyson, GoPro, DJI,
cuerpos de cámara y objetivos (Canon/Nikon/Sony/Sigma/Tamron). Los precios
típicos son **mínimos conservadores** (el iPhone más barato real ~250 €...)
solo para detectar precios absurdos.

**Pregunta:** ¿qué más marcas/productos te interesan? (Bose, Sonos, Garmin,
Makita/DeWalt, bicicletas eléctricas...?). Añadir uno es una línea.

## 4. Falsos positivos conocidos en nivel "dudoso"

Los "seguros" salen limpios tras la validación, pero en "dudoso" aún cuelan
auriculares/periféricos gaming que mencionan "PS5" en listas de plataformas
("PC/PS5/Xbox") sin la preposición "para" delante (ej. Mars Gaming MHW-100,
Logitech G535). Son 2 de 9 detecciones en el lote real — asumible porque
"dudoso" implica revisión manual, pero se puede afinar detectando listas de
plataformas separadas por barras.

## 5. Verificación de precio en Amazon (--verify) es experimental

`inspect --verify` / `manifests --verify` intenta leer el precio real del
ASIN en amazon.es. Amazon bloquea bots agresivamente: cuando falla, el
informe deja el **enlace directo al producto** para verificar a mano (1 clic).

**Alternativas serias si quieres automatizarlo de verdad:** API de Keepa
(~19 €/mes, histórico de precios por ASIN, fiable) o Amazon PA-API (requiere
cuenta de afiliado). Recomiendo Keepa si esto se usa para decidir pujas.

## 6. TVs: qué cuenta como pérdida

- "Seguro" (se descuenta del retail efectivo): categoría TV o descripción con
  pulgadas/panel (OLED/QLED/4K...), **y precio declarado ≥ 100 €** (un
  conversor HDMI "4K" de 24 € no es un panel).
- "Posible" (se lista pero NO se descuenta): menciona TV sin pulgadas, o
  parece TV pero cuesta < 100 €.

**Preguntas:** ¿los **monitores** y **proyectores** también llegan siempre
rotos (los descuento igual)? ¿Y el suelo de 100 € te cuadra?

## 7. La condición (Defective/Customer Damage...) no pondera el valor

El desglose por condición está en el informe, pero el "retail efectivo" solo
descuenta TVs. Si quieres, aplicamos un % de recuperación por condición (ej.
Defective 40%, Customer Damage 70%...) para estimar valor real de reventa.

## 8. Integración con las alertas de WhatsApp

Ahora mismo el análisis de manifiestos es bajo demanda (comandos `inspect` y
`manifests`). Siguiente paso natural: que el recordatorio T-30 baje el
manifiesto automáticamente y añada al WhatsApp el retail efectivo (sin TVs),
los regalados y las cajas sospechosas. No lo hice para no tocar la cadencia
del monitor sin hablarlo (son 2 peticiones extra por subasta clave).

## Resueltas sobre la marcha (FYI)

- Los CSV reales vienen en cp1252, no UTF-8 → el parser detecta y decodifica.
- Columnas reales confirmadas: `DEPARTMENT`, `Pallet ID` y `PkgID` (caja).
- Cámaras de videovigilancia ya no disparan el patrón de "objetivos".
- Menciones de compatibilidad ("funda para iPhone", "lápiz para Galaxy S25",
  "disquetera compatible con MacBook") ya no disparan regalados.

## Cómo usarlo

```bash
# Un manifiesto suelto
python -m liquidation_tracker.cli inspect data/manifests/<archivo>.csv

# Todos los de las subastas activas (descarga + informe por lote + resumen)
python -m liquidation_tracker.cli manifests --country ES

# Con verificación de precios en Amazon (lenta, experimental)
python -m liquidation_tracker.cli manifests --country ES --verify
```

Informes en `data/reports/` (markdown, uno por lote + `resumen_ES.md`).
