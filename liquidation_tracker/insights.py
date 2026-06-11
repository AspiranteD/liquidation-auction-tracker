"""Deep manifest analysis: where the value (and the loss) really is.

Goes beyond analyzer.py's aggregate stats to answer buyer questions:

- Units/value per department, category and subcategory.
- TVs: in liquidation truckloads the panels are effectively always broken, so
  their declared retail is treated as a loss and subtracted from the
  "effective retail" of the lot.
- Giveaways ("regalados"): premium products (iPhones, MacBooks, lenses...)
  declared at absurd retail prices (10-16 EUR) because they were misclassified.
  Detection is keyword-based with accessory exclusion, in two confidence
  tiers, plus an optional live Amazon price check for the doubtful ones.
- Box/pallet density: Amazon fills boxes and pallets to the top. A box with
  2 declared items means undeclared content — flag containers whose unit
  count or declared value is far below the lot's own median.
"""
from __future__ import annotations

import logging
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .models import ManifestItem

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunable rules
# ---------------------------------------------------------------------------

@dataclass
class InsightRules:
    """Thresholds for the deep analysis. Defaults calibrated on real
    manifests (Amazon EU truckloads: boxes carry ~12-60 units each)."""

    # Giveaways: declared price under this fraction of the product's typical
    # price -> "seguro" (certain) / "dudoso" (doubtful, verify before relying).
    giveaway_sure_fraction: float = 0.10
    giveaway_doubt_fraction: float = 0.40

    # Containers: flag when units (or declared value) fall at or below this
    # fraction of the lot's median container, or below the absolute floor.
    container_sparse_fraction: float = 0.35
    box_min_units_abs: int = 4
    pallet_min_units_abs: int = 15

    # A "sure" TV must be declared at least at this price; below it the line
    # is downgraded to "posible" (converters/dongles mention 4K too).
    tv_min_price: float = 100.0


# Premium products and the typical *minimum* market price of the cheapest
# real variant (EUR). Deliberately conservative: used only to detect absurd
# declared prices, not to estimate resale value.
PREMIUM_PRODUCTS: Dict[str, float] = {
    r"iphone\s?(?:1[1-9]|se|pro|plus|max)": 250.0,
    r"\bmacbook\b": 600.0,
    r"\bimac\b": 700.0,
    r"\bmac\s?mini\b": 500.0,
    r"\bipad\b": 250.0,
    r"apple\s?watch": 180.0,
    r"\bairpods\b": 100.0,
    r"galaxy\s?(?:s2[0-9]|z\s?(?:fold|flip)|note)": 250.0,
    r"galaxy\s?tab\s?s": 200.0,
    r"\bpixel\s?[6-9]": 250.0,
    r"\bps5\b|playstation\s?5": 300.0,
    r"xbox\s+series\s?[xs]": 250.0,
    r"nintendo\s+switch": 180.0,
    r"\brtx\s?[2-5]0[5-9]0": 250.0,
    r"\bdyson\b": 180.0,
    r"\bgopro\b": 150.0,
    r"\bdji\b": 150.0,
    # Camera bodies and lenses ("objetivos")
    r"\b(?:eos|alpha|a[67]\s?(?:iii|iv|r)|nikon\s?z|lumix\s?(?:s|gh))\b": 300.0,
    r"\b(?:sigma|tamron)\b.*\bmm\b": 200.0,
    r"\bobjetivo\b.*\bmm\b": 150.0,
    r"\b(?:canon|nikon|sony)\b.*\b(?:[0-9]{2,3}\s?mm|f/[0-9.]+)": 200.0,
}

# Words that mean the line is an accessory FOR a premium product / TV, not
# the product itself. Spanish, English, German, French, Italian.
ACCESSORY_WORDS = [
    "funda", "case", "carcasa", "cover", "hülle", "huelle", "coque", "custodia",
    "protector", "cristal", "glass", "vidrio", "panzerglas", "film", "folie",
    "pelicula", "película", "screen protector",
    "cable", "cargador", "charger", "ladegerät", "ladegeraet", "chargeur",
    "adaptador", "adapter", "adattatore", "dock", "hub",
    "soporte", "stand", "mount", "bracket", "halterung", "wandhalterung",
    "support mural", "staffa", "supporto",
    "ratón", "raton ", "mouse", "teclado", "keyboard", "alfombrilla",
    "mousepad", "repuesto", "recambio", "replacement",
    "convertidor", "converter", "splitter",
    "mando", "remote", "fernbedienung", "télécommande", "telecomando",
    "correa", "strap", "armband", "pulsera", "bracelet", "cinturino", "band",
    "stylus", "pencil tip", "skin", "sticker", "vinilo",
    "antena", "antenna", "antenne",
    "teclado para", "keyboard for", "tastatur für",
    "bateria para", "batería para", "battery for", "akku für",
    "tv stick", "fire tv", "chromecast", "tv box", "android tv box",
    "riser", "mueble", "mesa tv", "tv-bank", "meuble tv",
]

# Context that disqualifies the camera/lens patterns: surveillance gear has
# legitimate low prices and its descriptions mention lens specs in mm.
SURVEILLANCE_WORDS = [
    "vigilancia", "videovigilancia", "cctv", "surveillance", "ip cam",
    "camara ip", "cámara ip", "cupula", "cúpula", "dome", "dahua", "hikvision",
    "reolink", "annke", "nvr", "dvr", "webcam", "endoscop", "boroscop",
    "trail camera", "camara de caza", "cámara de caza",
]

# TV detection
_TV_KEYWORD_RE = re.compile(
    r"\b(?:tv|televisor(?:es)?|televisi[oó]n|television|fernseher|"
    r"t[ée]l[ée]viseur|televisore|smart\s?tv)\b",
    re.IGNORECASE,
)
_SCREEN_SIZE_RE = re.compile(
    r"\b(\d{2,3})\s*(?:\"|”|″|''|inch(?:es)?|pulgadas|zoll|pouces|pollici)",
    re.IGNORECASE,
)
_TV_PANEL_TECH_RE = re.compile(
    r"\b(?:oled|qled|nanocell|uled|4k|uhd|ultra\s?hd|led\s?tv)\b", re.IGNORECASE
)
_TV_CATEGORY_RE = re.compile(r"\btv|television", re.IGNORECASE)

AMAZON_URL = "https://www.amazon.es/dp/{asin}"

# "para Samsung Galaxy S25", "compatible con MacBook", "for PS5"... — the
# premium keyword names what the item works WITH, not what it is.
_COMPATIBILITY_RE = re.compile(
    r"(?:\bpara|\bfor|\bcompatible[s]?(?:\s+(?:con|with))?|\bfür|\bfuer|"
    r"\bpour|\bper|\badatto)\s+(?:[\w./+-]+\s+){0,3}$",
    re.IGNORECASE,
)


def _has_accessory_word(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in ACCESSORY_WORDS)


def _is_compatibility_mention(desc: str, match_start: int) -> bool:
    """True when the premium keyword is preceded by a compatibility phrase."""
    prefix = desc[max(0, match_start - 45):match_start]
    return bool(_COMPATIBILITY_RE.search(prefix))


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass
class GroupStats:
    name: str
    lines: int = 0
    units: int = 0
    retail: float = 0.0
    pct_units: float = 0.0
    pct_retail: float = 0.0


@dataclass
class TVFinding:
    item: ManifestItem
    confidence: str  # "seguro" | "posible"
    reason: str


@dataclass
class GiveawayFinding:
    item: ManifestItem
    tier: str          # "seguro" | "dudoso"
    matched: str       # which premium product matched
    typical_price: float
    reason: str
    amazon_url: Optional[str] = None
    verified_price: Optional[float] = None  # filled by the optional live check


@dataclass
class ContainerStats:
    container_id: str
    kind: str  # "caja" | "pallet"
    lines: int = 0
    units: int = 0
    retail: float = 0.0
    suspicious: bool = False
    reason: str = ""


@dataclass
class ManifestInsights:
    label: str
    total_lines: int
    total_units: int
    total_retail: float
    avg_unit_retail: float
    by_department: List[GroupStats]
    by_category: List[GroupStats]
    by_subcategory: List[GroupStats]
    by_condition: List[GroupStats]
    tvs: List[TVFinding]
    tv_units: int
    tv_loss_retail: float          # declared retail of certain TVs (loss)
    effective_retail: float        # total_retail - tv_loss_retail
    giveaways: List[GiveawayFinding]
    boxes: List[ContainerStats]
    pallets: List[ContainerStats]
    suspicious_boxes: List[ContainerStats]
    suspicious_pallets: List[ContainerStats]
    top_items: List[ManifestItem]
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Breakdowns
# ---------------------------------------------------------------------------

def breakdown(items: List[ManifestItem], attr: str) -> List[GroupStats]:
    """Units/lines/retail grouped by an item attribute, sorted by retail."""
    groups: Dict[str, GroupStats] = {}
    total_units = sum(i.qty for i in items) or 1
    total_retail = sum(i.line_retail for i in items) or 1.0

    for item in items:
        key = (getattr(item, attr) or "Desconocido").strip() or "Desconocido"
        group = groups.setdefault(key, GroupStats(name=key))
        group.lines += 1
        group.units += item.qty
        group.retail += item.line_retail

    for group in groups.values():
        group.retail = round(group.retail, 2)
        group.pct_units = round(100.0 * group.units / total_units, 1)
        group.pct_retail = round(100.0 * group.retail / total_retail, 1)

    return sorted(groups.values(), key=lambda g: g.retail, reverse=True)


# ---------------------------------------------------------------------------
# TVs (always broken -> loss)
# ---------------------------------------------------------------------------

def find_tvs(
    items: List[ManifestItem], rules: Optional[InsightRules] = None
) -> List[TVFinding]:
    rules = rules or InsightRules()
    findings: List[TVFinding] = []
    for item in items:
        desc = item.description or ""
        cat_text = " ".join(filter(None, [item.category, item.subcategory]))

        if _has_accessory_word(desc):
            continue  # TV stand / mount / remote / stick: not a panel

        category_says_tv = bool(_TV_CATEGORY_RE.search(cat_text))
        keyword = bool(_TV_KEYWORD_RE.search(desc))
        size = bool(_SCREEN_SIZE_RE.search(desc))
        panel_tech = bool(_TV_PANEL_TECH_RE.search(desc))

        if category_says_tv or (keyword and (size or panel_tech)):
            if item.unit_retail < rules.tv_min_price:
                findings.append(
                    TVFinding(
                        item=item,
                        confidence="posible",
                        reason=(
                            f"parece TV pero declarado a {item.unit_retail:.2f} EUR "
                            f"(< {rules.tv_min_price:.0f}): probable accesorio"
                        ),
                    )
                )
            else:
                findings.append(
                    TVFinding(
                        item=item,
                        confidence="seguro",
                        reason="categoría TV" if category_says_tv else "descripción con pulgadas/panel",
                    )
                )
        elif keyword:
            findings.append(
                TVFinding(item=item, confidence="posible", reason="menciona TV sin pulgadas")
            )
    return findings


# ---------------------------------------------------------------------------
# Giveaways ("regalados")
# ---------------------------------------------------------------------------

def find_giveaways(
    items: List[ManifestItem], rules: Optional[InsightRules] = None
) -> List[GiveawayFinding]:
    rules = rules or InsightRules()
    findings: List[GiveawayFinding] = []

    # Signal 1: premium product at an absurd declared price.
    for item in items:
        desc = item.description or ""
        if not desc or item.unit_retail <= 0:
            continue
        if _has_accessory_word(desc):
            continue
        lowered = desc.lower()
        if any(word in lowered for word in SURVEILLANCE_WORDS):
            continue  # CCTV/webcams: cheap by nature, lens specs mislead
        for pattern, typical in PREMIUM_PRODUCTS.items():
            match = re.search(pattern, desc, re.IGNORECASE)
            if match:
                if _is_compatibility_mention(desc, match.start()):
                    continue  # "para iPhone 16", "compatible con MacBook"...
                ratio = item.unit_retail / typical
                if ratio < rules.giveaway_sure_fraction:
                    tier = "seguro"
                elif ratio < rules.giveaway_doubt_fraction:
                    tier = "dudoso"
                else:
                    break  # premium product at a plausible price
                findings.append(
                    GiveawayFinding(
                        item=item,
                        tier=tier,
                        matched=pattern,
                        typical_price=typical,
                        reason=(
                            f"declarado a {item.unit_retail:.2f} EUR, "
                            f"tipico >= {typical:.0f} EUR ({ratio:.0%})"
                        ),
                        amazon_url=AMAZON_URL.format(asin=item.asin) if item.asin else None,
                    )
                )
                break

    # Signal 2: same ASIN priced wildly differently inside the same manifest
    # (the cheap lines are almost certainly misclassified).
    by_asin: Dict[str, List[ManifestItem]] = defaultdict(list)
    for item in items:
        if item.asin and item.unit_retail > 0:
            by_asin[item.asin].append(item)
    already = {id(f.item) for f in findings}
    for asin, group in by_asin.items():
        prices = [i.unit_retail for i in group]
        top = max(prices)
        if len(group) < 2 or top < 50:
            continue
        for item in group:
            if item.unit_retail <= top / 5 and id(item) not in already:
                findings.append(
                    GiveawayFinding(
                        item=item,
                        tier="dudoso",
                        matched="mismo ASIN con precio dispar",
                        typical_price=top,
                        reason=(
                            f"mismo ASIN aparece a {top:.2f} EUR y esta linea "
                            f"a {item.unit_retail:.2f} EUR"
                        ),
                        amazon_url=AMAZON_URL.format(asin=asin),
                    )
                )

    findings.sort(key=lambda f: (f.tier != "seguro", f.item.unit_retail))
    return findings


def verify_giveaway_prices(
    findings: List[GiveawayFinding], timeout: int = 15, max_checks: int = 10
) -> None:
    """Best-effort live price check on amazon.es for doubtful giveaways.

    EXPERIMENTAL: Amazon blocks bots aggressively, so this often returns
    nothing — findings keep their amazon_url for manual verification. Mutates
    ``findings`` in place, filling ``verified_price`` when a price is found.
    """
    import requests

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "es-ES,es;q=0.9",
    }
    price_re = re.compile(r'"priceAmount"\s*:\s*([0-9]+(?:\.[0-9]+)?)')
    checked = 0
    for finding in findings:
        if finding.tier != "dudoso" or not finding.item.asin or checked >= max_checks:
            continue
        checked += 1
        url = AMAZON_URL.format(asin=finding.item.asin)
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            match = price_re.search(response.text)
            if match:
                finding.verified_price = float(match.group(1))
        except Exception as exc:  # noqa: BLE001 - never fail the report
            logger.debug("Amazon check failed for %s: %s", finding.item.asin, exc)


# ---------------------------------------------------------------------------
# Box / pallet density
# ---------------------------------------------------------------------------

def container_analysis(
    items: List[ManifestItem],
    attr: str,
    kind: str,
    rules: Optional[InsightRules] = None,
) -> List[ContainerStats]:
    """Group items by physical container and flag suspiciously empty ones.

    Amazon fills boxes and pallets to the top: a container with far fewer
    declared units (or value) than its siblings means undeclared content.
    """
    rules = rules or InsightRules()
    containers: Dict[str, ContainerStats] = {}
    for item in items:
        cid = getattr(item, attr) or None
        if not cid:
            continue
        stats = containers.setdefault(cid, ContainerStats(container_id=cid, kind=kind))
        stats.lines += 1
        stats.units += item.qty
        stats.retail += item.line_retail

    result = sorted(containers.values(), key=lambda c: c.units)
    if len(result) < 2:
        return result  # nothing to compare against

    median_units = statistics.median(c.units for c in result)
    median_retail = statistics.median(c.retail for c in result)
    min_abs = rules.box_min_units_abs if kind == "caja" else rules.pallet_min_units_abs

    for stats in result:
        reasons = []
        sparse_floor = max(min_abs, median_units * rules.container_sparse_fraction)
        if stats.units <= sparse_floor and stats.units < median_units:
            reasons.append(
                f"{stats.units} uds declaradas vs mediana {median_units:.0f} "
                f"-> probable contenido sin declarar"
            )
        if median_retail > 0 and stats.retail <= median_retail * rules.container_sparse_fraction:
            reasons.append(
                f"valor declarado {stats.retail:.0f} EUR vs mediana "
                f"{median_retail:.0f} EUR"
            )
        if reasons:
            stats.suspicious = True
            stats.reason = "; ".join(reasons)
        stats.retail = round(stats.retail, 2)
    return result


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def deep_analyze(
    items: List[ManifestItem],
    label: str = "manifest",
    rules: Optional[InsightRules] = None,
    verify_prices: bool = False,
) -> ManifestInsights:
    """Run the full deep analysis over parsed manifest items."""
    rules = rules or InsightRules()
    warnings: List[str] = []

    total_units = sum(i.qty for i in items)
    total_retail = round(sum(i.line_retail for i in items), 2)

    tvs = find_tvs(items, rules)
    sure_tvs = [t for t in tvs if t.confidence == "seguro"]
    tv_units = sum(t.item.qty for t in sure_tvs)
    tv_loss = round(sum(t.item.line_retail for t in sure_tvs), 2)

    giveaways = find_giveaways(items, rules)
    if verify_prices and any(g.tier == "dudoso" for g in giveaways):
        verify_giveaway_prices(giveaways)

    boxes = container_analysis(items, "box_id", "caja", rules)
    pallets = container_analysis(items, "pallet_id", "pallet", rules)

    if not any(i.box_id for i in items):
        warnings.append("El manifiesto no trae columna de caja (PkgID): sin análisis por caja.")
    if not any(i.pallet_id for i in items):
        warnings.append("El manifiesto no trae columna de pallet: sin análisis por pallet.")
    if not any(i.department for i in items):
        warnings.append("El manifiesto no trae columna DEPARTMENT.")
    no_price = sum(1 for i in items if i.unit_retail <= 0)
    if no_price:
        warnings.append(f"{no_price} líneas sin precio retail declarado.")

    top_items = sorted(items, key=lambda i: i.line_retail, reverse=True)[:10]

    return ManifestInsights(
        label=label,
        total_lines=len(items),
        total_units=total_units,
        total_retail=total_retail,
        avg_unit_retail=round(total_retail / total_units, 2) if total_units else 0.0,
        by_department=breakdown(items, "department"),
        by_category=breakdown(items, "category"),
        by_subcategory=breakdown(items, "subcategory"),
        by_condition=breakdown(items, "condition"),
        tvs=tvs,
        tv_units=tv_units,
        tv_loss_retail=tv_loss,
        effective_retail=round(total_retail - tv_loss, 2),
        giveaways=giveaways,
        boxes=boxes,
        pallets=pallets,
        suspicious_boxes=[b for b in boxes if b.suspicious],
        suspicious_pallets=[p for p in pallets if p.suspicious],
        top_items=top_items,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Report rendering (markdown, Spanish: it is a buyer-facing document)
# ---------------------------------------------------------------------------

def _table(headers: List[str], rows: List[List[str]]) -> List[str]:
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("|" + "|".join("---" for _ in headers) + "|")
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def _group_table(groups: List[GroupStats], top_n: int = 15) -> List[str]:
    rows = [
        [g.name[:45], str(g.units), f"{g.pct_units}%", f"{g.retail:,.0f}", f"{g.pct_retail}%"]
        for g in groups[:top_n]
    ]
    if len(groups) > top_n:
        rest_units = sum(g.units for g in groups[top_n:])
        rest_retail = sum(g.retail for g in groups[top_n:])
        rows.append([f"... otros {len(groups) - top_n}", str(rest_units), "", f"{rest_retail:,.0f}", ""])
    return _table(["Grupo", "Uds", "% uds", "Retail EUR", "% retail"], rows)


def render_report(insights: ManifestInsights) -> str:
    """Render a ManifestInsights as a markdown report."""
    out: List[str] = [f"# Análisis de manifiesto — {insights.label}", ""]

    out += [
        "## Resumen",
        "",
        f"- Líneas: **{insights.total_lines}** · Unidades: **{insights.total_units}**",
        f"- Retail declarado: **{insights.total_retail:,.2f} EUR** "
        f"(media {insights.avg_unit_retail:,.2f} EUR/ud)",
        f"- TVs (pérdida asumida): **{insights.tv_units} uds, "
        f"{insights.tv_loss_retail:,.2f} EUR**",
        f"- **Retail efectivo (sin TVs): {insights.effective_retail:,.2f} EUR**",
        f"- Regalados detectados: **{len([g for g in insights.giveaways if g.tier == 'seguro'])} seguros, "
        f"{len([g for g in insights.giveaways if g.tier == 'dudoso'])} dudosos**",
        f"- Cajas: {len(insights.boxes)} ({len(insights.suspicious_boxes)} sospechosas) · "
        f"Pallets: {len(insights.pallets)} ({len(insights.suspicious_pallets)} sospechosos)",
        "",
    ]

    out += ["## Por departamento", ""]
    out += _group_table(insights.by_department)
    out += ["", "## Por categoría", ""]
    out += _group_table(insights.by_category)
    out += ["", "## Por subcategoría", ""]
    out += _group_table(insights.by_subcategory)
    out += ["", "## Por condición", ""]
    out += _group_table(insights.by_condition)

    out += ["", "## Televisores (pérdida: los paneles llegan rotos)", ""]
    sure = [t for t in insights.tvs if t.confidence == "seguro"]
    maybe = [t for t in insights.tvs if t.confidence == "posible"]
    if sure:
        out += _table(
            ["Descripción", "Uds", "Retail EUR", "Detección"],
            [
                [(t.item.description or "")[:60], str(t.item.qty),
                 f"{t.item.line_retail:,.2f}", t.reason]
                for t in sure
            ],
        )
        out.append(f"\n**Pérdida total estimada: {insights.tv_loss_retail:,.2f} EUR**")
    else:
        out.append("Sin televisores detectados.")
    if maybe:
        out += ["", "Posibles TVs (revisar a mano, no descontados):", ""]
        out += [f"- {(t.item.description or '')[:80]} ({t.item.unit_retail:,.2f} EUR)" for t in maybe]

    out += ["", "## Artículos regalados (mal clasificados)", ""]
    if insights.giveaways:
        rows = []
        for g in insights.giveaways:
            verified = f"{g.verified_price:,.0f}" if g.verified_price else "-"
            rows.append([
                (g.item.description or "")[:55],
                f"{g.item.unit_retail:,.2f}",
                f"{g.typical_price:,.0f}",
                verified,
                g.tier.upper(),
                g.amazon_url or "-",
            ])
        out += _table(
            ["Descripción", "Declarado EUR", "Típico EUR", "Amazon EUR", "Nivel", "Verificar"],
            rows,
        )
        out.append("")
        out.append(
            "> Los *dudosos* requieren verificación manual (enlace Amazon). "
            "El precio 'Amazon EUR' solo aparece si la comprobación automática funcionó."
        )
    else:
        out.append("Sin regalados detectados con las reglas actuales.")

    out += ["", "## Densidad por caja (las llenan a tope: poca cosa = sorpresa dentro)", ""]
    if insights.boxes:
        out += _table(
            ["Caja", "Líneas", "Uds", "Retail EUR", "Sospechosa"],
            [
                [b.container_id[:20], str(b.lines), str(b.units), f"{b.retail:,.0f}",
                 ("🚩 " + b.reason) if b.suspicious else ""]
                for b in insights.boxes
            ],
        )
    else:
        out.append("Sin información de cajas en este manifiesto.")

    out += ["", "## Densidad por pallet", ""]
    if insights.pallets:
        out += _table(
            ["Pallet", "Líneas", "Uds", "Retail EUR", "Sospechoso"],
            [
                [p.container_id[:20], str(p.lines), str(p.units), f"{p.retail:,.0f}",
                 ("🚩 " + p.reason) if p.suspicious else ""]
                for p in insights.pallets
            ],
        )
    else:
        out.append("Sin información de pallets en este manifiesto.")

    out += ["", "## Top 10 artículos por valor", ""]
    out += _table(
        ["Descripción", "Uds", "Unitario EUR", "Línea EUR", "Condición"],
        [
            [(i.description or "")[:60], str(i.qty), f"{i.unit_retail:,.2f}",
             f"{i.line_retail:,.2f}", i.condition or "?"]
            for i in insights.top_items
        ],
    )

    if insights.warnings:
        out += ["", "## Avisos", ""]
        out += [f"- {w}" for w in insights.warnings]

    out.append("")
    return "\n".join(out)
