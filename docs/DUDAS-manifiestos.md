# Análisis de manifiestos — dudas y decisiones pendientes

> ## ACTUALIZACIÓN 17/06/2026 — refactor según feedback (102 tests en verde)
>
> Cinco cambios pedidos, ya implementados:
>
> 1. **La columna `condition` ya NO afecta a la valoración.** Se mantiene como
>    dato (desglose por condición en el informe), pero nada deriva valor de
>    ella. Resuelve el punto 7.
> 2. **Adiós "dudosos": ahora se verifican.** `liquidation_tracker/pricing.py`
>    resuelve el precio real de cada sospechoso en orden **BD Reusalia →
>    caché → scraping Amazon** (scraper extraído del backend, standalone). Si
>    el declarado < 40% del precio real → regalado CONFIRMADO; si ≈ real → se
>    descarta (falso positivo). Lo que no se puede verificar online queda en un
>    bucket aparte "sin verificar" (no contamina el valor). Resuelve puntos 4 y 5.
> 3. **TV = solo taxonomía.** Es TV (pérdida) únicamente si la categoría es
>    "Televisions" o la subcategoría contiene "TVs <pulgadas>". Proyectores y
>    monitores ya NO son pérdida. Resuelve punto 6.
> 4. **Cajas regaladas con rango (lo prioritario).** Por cada pallet de cajas
>    incompleto se estima el valor de las cajas que faltan = media de las cajas
>    declaradas del pallet, con rango [caja más barata, caja más cara]. El "6
>    cajas por pallet" está validado en 217 pallets históricos (máx=moda=6).
> 5. **Puja por recuperación, sin reglas fijas.** Se elimina el 12%/15%. La
>    puja recomendada hace que el coste aterrizado = recuperación/3 del retail,
>    con la recuperación real por departamento del macro-estudio Reusalia
>    (`data/recovery.json`, generado por `scripts/build_recovery.py`). Se dan
>    DOS cifras: sobre retail declarado y sobre el retail REAL (con cajas
>    regaladas + mal clasificados) — tu ventaja informativa.
>
> Lo que sigue abajo es el estado anterior (contexto histórico); los puntos 4,
> 5, 6 y 7 quedan resueltos por lo de arriba.

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

## 2. Contenedores: modelo actual (revisado 11/06 noche)

Cada pallet se clasifica por su contenido real:

- **Pallet de cajas** (≥2 PkgIDs distintos): ~6 cajas de Amazon apiladas con
  objetos pequeños (0,4-2 kg de media en los lotes reales). Dos chequeos:
  - *Caja demasiado vacía*: una caja con muchos menos objetos que la mediana
    de cajas del lote (≤35%, suelo 4) → "puede haber regalados dentro". El
    **valor NO cuenta**: una caja llena de cosas baratas es normal.
  - *Pallet con cajas de menos*: menos de 6 cajas declaradas → "puede haber
    cajas enteras sin declarar".
- **Pallet de objetos grandes** (1 PkgID, peso medio ≥15 kg): cintas de
  correr, muebles... 1-3 unidades es lo normal → **nunca se marca**.
- **Pallet a granel** (1 PkgID, objetos medianos): conteos variables
  legítimos → tampoco se marca.

**Preguntas:** (a) el corte de "objeto grande" está en 15 kg de peso medio —
¿te cuadra?; (b) marco los pallets con 5 de 6 cajas (sale a menudo: ¿falta
una caja de verdad o a veces apilan 5?); ¿lo dejo solo para ≤4?

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

## 9. Informes programados: limitaciones y pendientes (11/06 tarde)

- **CallMeBot no puede enviar PDFs por WhatsApp** (API gratuita, solo texto).
  Lo implementado: al detectar lote nuevo te llega un **resumen en texto** al
  WhatsApp y el **PDF completo viaja en el email** de las 9/12/21h. Si quieres
  el PDF dentro de WhatsApp de verdad, hace falta WhatsApp Cloud API de Meta
  (gratis hasta 1.000 conversaciones/mes, requiere alta de Meta Business) o
  Twilio (de pago). Dime y lo monto.
- **Email pendiente de credenciales SMTP**: los huecos están en `.env`
  (`SMTP_USERNAME`, `SMTP_PASSWORD` — con Gmail usa un App Password —,
  `EMAIL_RECIPIENTS`, y poner `EMAIL_ALERTS_ENABLED=true`). Hasta entonces el
  digest se genera y queda en `data/reports/pdf/` pero no se envía (lo dice el
  log). Todo lo demás ya corre.
- **Ventana del monitor 12:30-16:00**: si algún día una subasta cerrara fuera
  de esa franja, no tendría recordatorio. Hasta ahora todas cierran entre
  13:00 y 15:30, así que encaja. El watch de lotes nuevos sí corre 24/7.
- **El PC debe estar encendido a las 12:30**: la tarea diaria con repetición
  no se recupera si en ese momento está apagado (se reanuda al día siguiente).

## 10. Llamada de voz a ≤5 min (12/06)

Implementada la escalera 30/15/10/5 por WhatsApp + **llamada de voz** cuando
quedan ≤5 min y el lote sigue dentro del umbral. La llamada es gratuita vía
CallMeBot: te suena como llamada de **Telegram** y una voz lee la alerta
(puja actual, % de coste, máximo recomendado).

**Para activarla necesito de ti** (2 minutos):
1. Instala Telegram (si no lo tienes) con tu número.
2. Busca el bot `@CallMeBot_txtbot` y envíale `/start`.
3. Pásame tu usuario de Telegram (ej. `@guillem`) — lo pongo en el `.env` y
   hago una llamada de prueba.

**Limitación**: NO es una llamada a tu número de teléfono normal (eso solo se
puede gratis... no se puede; sería Twilio, ~0,05 €/llamada + número ~5 €/mes).
La llamada de Telegram suena y se contesta exactamente igual que una llamada
normal si tienes la app instalada.

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
