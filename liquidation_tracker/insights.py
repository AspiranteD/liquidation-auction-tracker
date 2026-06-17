"""Deep manifest analysis: where the value (and the loss) really is.

Goes beyond analyzer.py's aggregate stats to answer buyer questions:

- Units/value per department, category and subcategory.
- TVs: a panel in a liquidation truckload is effectively always broken, so its
  declared retail is a loss. A line is a TV ONLY when the manifest's own
  taxonomy says so (category "Televisions" or subcategory "TVs <size>"). We do
  NOT guess from the description: projectors, monitors and TV accessories live
  in other categories and are NOT losses.
- Giveaways ("regalados"): premium products (iPhones, MacBooks, lenses...)
  declared at absurd prices because they were misclassified. Detection finds
  *suspects* (premium keyword + absurd price, minus accessories), then RESOLVES
  the real price (Reusalia DB / cache / Amazon) to confirm or discard each one.
  No more "dudosos": either it is verified as a giveaway, or it is dropped.
- Box/pallet density: Amazon fills boxes and pallets to the top. A pallet of
  boxes always carries 6 (validated on 217 historical pallets: max=6, mode=6);
  fewer means whole boxes travel undeclared = gifted. We estimate the value of
  the missing boxes with a clear range.
"""
from __future__ import annotations

import json
import logging
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .models import ManifestItem

logger = logging.getLogger(__name__)

# Per-department box statistics built from the historical corpus by
# scripts/build_baselines.py (24k+ real boxes). Lets the empty-box alarm be
# category-aware: a Motor box normally carries 38-106 items (alarm under
# ~23) while a Furniture box normally carries 7.
BASELINES_PATH = "data/baselines.json"
_baselines_cache: Optional[Dict] = None


def load_baselines(path: str = BASELINES_PATH) -> Dict:
    global _baselines_cache
    if _baselines_cache is None:
        try:
            with open(path, encoding="utf-8") as fh:
                _baselines_cache = json.load(fh)
        except (OSError, ValueError):
            _baselines_cache = {}
    return _baselines_cache


# ---------------------------------------------------------------------------
# Tunable rules
# ---------------------------------------------------------------------------

@dataclass
class InsightRules:
    """Thresholds for the deep analysis. Defaults calibrated on real
    manifests (Amazon EU truckloads: boxes carry ~12-60 units each)."""

    # Giveaways. A suspect is a premium product declared cheap. Once we have a
    # reference price (verified or typical), the line is a giveaway iff its
    # declared price is below ``giveaway_confirm_fraction`` of that reference.
    giveaway_confirm_fraction: float = 0.40
    # When the price could NOT be verified, only an EXTREME discount (below this
    # fraction of the conservative typical price) is trusted as a sure giveaway;
    # the middle band is reported separately as "sin verificar".
    giveaway_sure_fraction: float = 0.10

    # Boxes travel full to the top. Few declared units means hidden content
    # UNLESS the declared items are bulky enough to fill the box themselves
    # (weight is the volume proxy; declared value is never a criterion).
    box_sparse_fraction: float = 0.35
    box_min_units_abs: int = 4
    # A sparse box is excused when its total weight reaches this fraction of
    # the median box weight, or its items average this many kg each.
    box_bulky_weight_fraction: float = 0.6
    bulky_avg_item_kg: float = 5.0

    # Amazon stacks this many boxes per "pallet de cajas". Validated on 217
    # historical box-pallets: distribution {4:11, 5:55, 6:148}, max=mode=6.
    # Fewer declared boxes => whole boxes travel undeclared (gifted).
    expected_boxes_per_pallet: int = 6

    # A single-package pallet whose items average at least this weight (kg)
    # is a "pallet de objetos grandes": few units there is normal.
    large_object_weight_kg: float = 15.0
    # ...but a single-package pallet with MANY light items matches the
    # profile of exactly one Amazon box -> treat as a box-pallet with five
    # boxes missing.
    single_box_min_units: int = 20
    single_box_max_avg_kg: float = 2.5


# Premium products and the typical *minimum* market price of the cheapest
# real variant (EUR). Used as a conservative fallback ONLY when the real price
# cannot be resolved; the resolver's verified price always wins.
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
    r"dji\s?mic": 89.0,   # before the brand pattern: mics are cheaper
    r"\bdji\b": 150.0,
    # Camera bodies and lenses ("objetivos"). "alpha" only with Sony context:
    # bare "Alpha" matches gaming headsets ("HyperX Cloud Alpha").
    r"\b(?:eos|nikon\s?z|lumix\s?(?:s|gh))\b": 300.0,
    r"sony\b.*\balpha\b|\ba[67]\s?(?:iii|iv|r)\b": 300.0,
    r"\b(?:sigma|tamron)\b.*\bmm\b": 200.0,
    r"\bobjetivo\b.*\bmm\b": 150.0,
    r"\b(?:canon|nikon|sony)\b.*\b(?:[0-9]{2,3}\s?mm|f/[0-9.]+)": 200.0,
}

# Bare-brand patterns (action cams, drones, vacuums) sit in a sea of cheap
# branded accessories. For these, ANY accessory word in the line — wherever
# it sits — means it's a GoPro mount / DJI case, not the device itself.
_BRAND_ONLY = {r"\bdyson\b", r"\bgopro\b", r"dji\s?mic", r"\bdji\b"}

# Patterns that name a standalone DEVICE (console, phone, computer, GPU).
# A peripheral that merely lists them as compatible platforms ("auriculares
# gaming PC/PS5/Xbox") must not match.
DEVICE_PATTERNS = {
    r"iphone\s?(?:1[1-9]|se|pro|plus|max)",
    r"\bmacbook\b",
    r"\bimac\b",
    r"\bmac\s?mini\b",
    r"\bipad\b",
    r"galaxy\s?(?:s2[0-9]|z\s?(?:fold|flip)|note)",
    r"galaxy\s?tab\s?s",
    r"\bpixel\s?[6-9]",
    r"\bps5\b|playstation\s?5",
    r"xbox\s+series\s?[xs]",
    r"nintendo\s+switch",
    r"\brtx\s?[2-5]0[5-9]0",
}

# If the line IS one of these peripherals, a device mention is just the
# compatibility list, never the product itself.
PERIPHERAL_WORDS = [
    "auricular", "headset", "headphone", "earbud", "kopfhörer", "kopfhoerer",
    "casque", "cascos", "altavoz", "speaker",
    "micrófono", "microfono", "microphone", "mikrofon",
    "mando", "controller", "gamepad", "joystick", "volante",
    "teclado", "keyboard", "tastatur", "clavier", "tastiera",
    "silla", "chair", "monitor", "ssd", "tarjeta", "memoria",
    "base de carga", "charging station", "ventilador", "cooling",
]

# Words that mean the line is an accessory FOR a premium product / TV, not
# the product itself. Spanish, English, German, French, Italian.
ACCESSORY_WORDS = [
    "funda", "case", "carcasa", "cover", "hülle", "huelle", "hoesje",
    "coque", "custodia",
    "protector", "cristal", "glass", "vidrio", "panzerglas", "film", "folie",
    "pelicula", "película", "screen protector",
    "cable", "kabel", "câble", "cavo", "cargador", "charger", "ladegerät",
    "ladegeraet", "ladekabel", "chargeur",
    "adaptador", "adapter", "adattatore", "dock", "hub",
    "power bank", "powerbank", "batería externa", "bateria externa",
    "selfie", "trípode", "tripode", "tripod",
    "soporte", "stand", "mount", "bracket", "halterung", "wandhalterung",
    "support mural", "staffa", "supporto",
    "ratón", "raton ", "mouse", "teclado", "keyboard", "alfombrilla",
    "mousepad", "repuesto", "recambio", "replacement",
    "convertidor", "converter", "splitter",
    "mando", "remote", "fernbedienung", "télécommande", "telecomando",
    "correa", "strap", "armband", "pulsera", "bracelet", "cinturino", "band",
    "stylus", "lápiz", "lapiz", "pencil", "punta", "puntas",
    "pen tip", "skin", "sticker", "vinilo",
    "pendrive", "pen drive", "usb-stick", "usb stick", "flash drive",
    "flash-laufwerk", "memoria usb", "memory card", "tarjeta de memoria",
    "microsd", "micro sd",
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

# TV detection: TRUST THE MANIFEST TAXONOMY, not the free-text description.
# - category "Televisions" (the B-Stock category for panels), or
# - subcategory containing the token "TVs" ("TVs 61\"-69\"", "Refurbished TVs").
# "TV Mounts", "TV Audio", "TV Stands" do NOT contain "TVs"; projectors and
# monitors live in their own categories. So accessories/projectors never match.
_TV_CATEGORY_RE = re.compile(r"^\s*televisi(?:on|ón)s?\s*$", re.IGNORECASE)
_TV_SUBCATEGORY_RE = re.compile(r"\bTVs\b", re.IGNORECASE)

AMAZON_URL = "https://www.amazon.es/dp/{asin}"

# "para Samsung Galaxy S25", "compatible con MacBook", "Kompatibel mit PC,
# Laptop, Smartphones, iPhone 15/16, MacBook"... — the premium keyword names
# what the item works WITH, not what it is. Compatibility lists run long
# (many comma-separated platforms), so allow many tokens between the
# preposition and the match.
_COMPATIBILITY_RE = re.compile(
    r"(?:\bpara|\bfor|\bcompatible[s]?(?:\s+(?:con|with))?|\bkompatibel(?:\s+mit)?|"
    r"\bfür|\bfuer|\bpour|\bper|\badatto|\bzum|\bvon|\bvoor)\s+"
    r"(?:[\w./+,&-]+[\s,]+){0,10}$",
    re.IGNORECASE,
)
# Compatibility lists can be long; look back far enough to catch the lead-in.
_COMPAT_LOOKBACK = 120


def _has_accessory_word(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in ACCESSORY_WORDS)


def _first_word_pos(text: str, words: List[str]) -> Optional[int]:
    """Position of the earliest occurrence of any word, or None."""
    lowered = text.lower()
    positions = [lowered.find(w) for w in words if w in lowered]
    return min(positions) if positions else None


def _word_before(text: str, words: List[str], match_start: int) -> bool:
    """True when a word appears BEFORE ``match_start`` in the text.

    Product titles lead with the head noun: "Funda para iPhone 16" is a
    case, while "iPhone 16 Pro 128GB de memoria" is a phone whose specs
    mention memory. Position decides which one we are looking at.
    """
    pos = _first_word_pos(text, words)
    return pos is not None and pos < match_start


def _is_compatibility_mention(desc: str, match_start: int) -> bool:
    """True when the premium keyword is preceded by a compatibility phrase."""
    prefix = desc[max(0, match_start - _COMPAT_LOOKBACK):match_start]
    return bool(_COMPATIBILITY_RE.search(prefix))


# Product names often carry their dimensions ("120x60x40 cm"): a free volume
# signal for judging whether few items can legitimately fill a box.
_DIMENSIONS_RE = re.compile(
    r"(\d{2,3})\s*[xX×]\s*(\d{1,3})\s*[xX×]\s*(\d{1,3})\s*(?:cm|CM)"
)


def _volume_liters(desc: Optional[str]) -> Optional[float]:
    match = _DIMENSIONS_RE.search(desc or "")
    if not match:
        return None
    a, b, c = (int(match.group(n)) for n in (1, 2, 3))
    return a * b * c / 1000.0


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
    confidence: str  # "seguro" (taxonomy says TV = loss)
    reason: str


@dataclass
class GiveawayFinding:
    item: ManifestItem
    tier: str             # "seguro" (confirmed/extreme) | "sin_verificar"
    matched: str          # which premium product / signal matched
    reference_price: float  # best real-price estimate per unit
    reason: str
    verified: bool = False        # True when reference_price came from DB/scrape/manifest
    reference_source: str = "típico"  # cache | db_scraped | db_sale | amazon | lote | típico
    amazon_url: Optional[str] = None

    @property
    def estimated_value(self) -> float:
        """Best estimate of the line's real value."""
        return self.reference_price * self.item.qty

    @property
    def hidden_value(self) -> float:
        """Value NOT reflected in the declared retail (the actual gift)."""
        return max(0.0, self.estimated_value - self.item.line_retail)


@dataclass
class ContainerStats:
    container_id: str
    kind: str  # "caja" | "pallet"
    lines: int = 0
    units: int = 0
    retail: float = 0.0
    weight_kg: float = 0.0
    suspicious: bool = False
    reason: str = ""
    items: List[ManifestItem] = field(default_factory=list)


@dataclass
class PalletStats:
    """A physical pallet, classified by what it carries.

    - "cajas": several Amazon boxes stacked (normally 6); many small items.
    - "objetos grandes": loose bulky items (treadmills, furniture); 1-3
      units is perfectly normal.
    - "granel": loose medium items directly on the pallet.
    """

    pallet_id: str
    pallet_type: str  # "cajas" | "granel" | "objetos grandes"
    box_count: int = 0
    lines: int = 0
    units: int = 0
    retail: float = 0.0
    avg_weight_kg: Optional[float] = None
    suspicious: bool = False
    reason: str = ""
    missing_boxes: int = 0  # box-pallets declare 6; fewer = likely gifted
    # Estimated retail of the missing (gifted) boxes: point estimate (average
    # declared box of this pallet) with a [low, high] range from the cheapest
    # and dearest declared box.
    missing_value_point: float = 0.0
    missing_value_low: float = 0.0
    missing_value_high: float = 0.0


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
    tv_loss_retail: float          # declared retail of taxonomy TVs (loss)
    effective_retail: float        # total_retail - tv_loss_retail
    giveaways: List[GiveawayFinding]
    giveaway_value_sure: float        # hidden value in confirmed findings
    giveaway_value_unverified: float  # hidden value in "sin verificar" findings
    boxes: List[ContainerStats]       # real boxes (from multi-box pallets)
    pallets: List[PalletStats]        # every pallet, classified
    suspicious_boxes: List[ContainerStats]
    suspicious_pallets: List[PalletStats]  # box-pallets missing boxes
    # Estimated retail hidden in gifted (undeclared) boxes, summed across the
    # lot, as a point estimate with a [low, high] range.
    gifted_box_value_point: float
    gifted_box_value_low: float
    gifted_box_value_high: float
    top_items: List[ManifestItem]
    warnings: List[str] = field(default_factory=list)

    @property
    def hidden_value_point(self) -> float:
        """Total value the lot hides beyond its declared retail: confirmed
        giveaway uplift + estimated gifted boxes (point estimate)."""
        return self.giveaway_value_sure + self.gifted_box_value_point

    @property
    def real_retail_point(self) -> float:
        """Declared retail + estimated hidden value (point estimate)."""
        return self.total_retail + self.hidden_value_point


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
# TVs (always broken -> loss). Detected by manifest taxonomy ONLY.
# ---------------------------------------------------------------------------

def find_tvs(
    items: List[ManifestItem], rules: Optional[InsightRules] = None
) -> List[TVFinding]:
    """Flag panels using the manifest's own taxonomy. A line is a TV iff its
    category is "Televisions" or its subcategory contains "TVs" (e.g.
    'TVs 61"-69"'). Projectors, monitors and TV accessories are NOT TVs and
    are never treated as a loss."""
    findings: List[TVFinding] = []
    for item in items:
        category = (item.category or "")
        subcategory = (item.subcategory or "")
        if _TV_CATEGORY_RE.match(category):
            findings.append(TVFinding(item=item, confidence="seguro",
                                      reason="categoría Televisions"))
        elif _TV_SUBCATEGORY_RE.search(subcategory):
            findings.append(TVFinding(item=item, confidence="seguro",
                                      reason=f"subcategoría {subcategory.strip()}"))
    return findings


# ---------------------------------------------------------------------------
# Giveaways ("regalados"): detect suspects, then VERIFY the real price.
# ---------------------------------------------------------------------------

def _giveaway_suspects(
    items: List[ManifestItem],
) -> List[Tuple[ManifestItem, str, float]]:
    """Premium-product lines declared cheap, after accessory/compat/surveillance
    exclusion. Returns (item, matched_pattern, typical_price)."""
    suspects: List[Tuple[ManifestItem, str, float]] = []
    for item in items:
        desc = item.description or ""
        if not desc or item.unit_retail < 0:
            continue
        lowered = desc.lower()
        if any(word in lowered for word in SURVEILLANCE_WORDS):
            continue  # CCTV/webcams: cheap by nature, lens specs mislead
        for pattern, typical in PREMIUM_PRODUCTS.items():
            match = re.search(pattern, desc, re.IGNORECASE)
            if not match:
                continue
            # Accessory/peripheral word BEFORE the premium match => the line is
            # the accessory ("Funda para iPhone", "Auriculares ... PS5").
            if _word_before(desc, ACCESSORY_WORDS, match.start()):
                break
            if pattern in _BRAND_ONLY and _has_accessory_word(desc):
                break
            if pattern in DEVICE_PATTERNS and _word_before(
                desc, PERIPHERAL_WORDS, match.start()
            ):
                break
            if _is_compatibility_mention(desc, match.start()):
                break  # "para iPhone 16", "compatible con MacBook"...
            suspects.append((item, pattern, typical))
            break
    return suspects


def find_giveaways(
    items: List[ManifestItem],
    rules: Optional[InsightRules] = None,
    resolver=None,
    max_verify: int = 12,
) -> List[GiveawayFinding]:
    """Detect misclassified premium products.

    Two signals:

    1. Premium keyword at an absurd declared price. Each suspect's REAL price is
       resolved via ``resolver`` (PriceResolver: DB -> cache -> Amazon). If the
       declared price is below ``giveaway_confirm_fraction`` of the verified
       price it is a confirmed ("seguro") giveaway; if it matches the real price
       it is discarded (false positive). When the price cannot be verified, only
       an extreme discount (< ``giveaway_sure_fraction`` of the conservative
       typical price) is trusted; the middle band is reported "sin_verificar".
    2. Same ASIN priced wildly differently within the manifest: the cheap line
       is self-evidently misclassified (reference = the dear line). Always
       "seguro" — the manifest itself is the proof.
    """
    rules = rules or InsightRules()
    findings: List[GiveawayFinding] = []
    already = set()

    # Spend the (rate-limited, blockable) verification budget on the suspects
    # with the most potential hidden value; the rest use the conservative
    # heuristic. Keeps free scraping focused and reduces antibot blocks.
    suspects = _giveaway_suspects(items)
    verify_ids = {
        id(item) for item, _, typical in sorted(
            suspects, key=lambda s: s[2] - s[0].unit_retail, reverse=True
        )[:max_verify]
    } if resolver else set()

    for item, pattern, typical in suspects:
        url = AMAZON_URL.format(asin=item.asin) if item.asin else None
        resolved = (
            resolver.resolve(item.asin)
            if (resolver and item.asin and id(item) in verify_ids)
            else None
        )

        if resolved is not None and resolved.found:
            ref = resolved.price
            if item.unit_retail < ref * rules.giveaway_confirm_fraction:
                findings.append(GiveawayFinding(
                    item=item, tier="seguro", matched=pattern,
                    reference_price=ref, verified=True,
                    reference_source=resolved.source,
                    reason=(f"declarado {item.unit_retail:.2f} EUR, precio real "
                            f"verificado {ref:.0f} EUR ({resolved.source})"),
                    amazon_url=url,
                ))
                already.add(id(item))
            # else: real price ~ declared price -> false positive, discard.
            continue

        # Could not verify: fall back to the conservative typical price.
        ratio = item.unit_retail / typical if typical else 0.0
        if ratio < rules.giveaway_sure_fraction:
            tier, src = "seguro", "típico (descuento extremo)"
        elif ratio < rules.giveaway_confirm_fraction:
            tier, src = "sin_verificar", "típico (sin verificar)"
        else:
            continue  # premium product at a plausible price
        findings.append(GiveawayFinding(
            item=item, tier=tier, matched=pattern,
            reference_price=typical, verified=False, reference_source=src,
            reason=(f"declarado {item.unit_retail:.2f} EUR, típico >= "
                    f"{typical:.0f} EUR ({ratio:.0%}); no se pudo verificar"
                    if tier == "sin_verificar" else
                    f"declarado {item.unit_retail:.2f} EUR, típico >= "
                    f"{typical:.0f} EUR ({ratio:.0%}): descuento extremo"),
            amazon_url=url,
        ))
        already.add(id(item))

    # Signal 2: same ASIN priced wildly differently inside the same manifest.
    by_asin: Dict[str, List[ManifestItem]] = defaultdict(list)
    for item in items:
        if item.asin and item.unit_retail > 0:
            by_asin[item.asin].append(item)
    for asin, group in by_asin.items():
        prices = [i.unit_retail for i in group]
        top = max(prices)
        if len(group) < 2 or top < 50:
            continue
        for item in group:
            if item.unit_retail <= top / 5 and id(item) not in already:
                findings.append(GiveawayFinding(
                    item=item, tier="seguro", matched="mismo ASIN con precio dispar",
                    reference_price=top, verified=True, reference_source="lote",
                    reason=(f"mismo ASIN aparece a {top:.2f} EUR y esta línea "
                            f"a {item.unit_retail:.2f} EUR"),
                    amazon_url=AMAZON_URL.format(asin=asin),
                ))
                already.add(id(item))

    findings.sort(key=lambda f: (f.tier != "seguro", -f.hidden_value))
    return findings


# ---------------------------------------------------------------------------
# Box / pallet density
# ---------------------------------------------------------------------------

def _box_is_bulky(
    box: ContainerStats, median_weight: float, rules: InsightRules
) -> bool:
    """Few declared items can still fill a box if they are big: judge by
    total weight vs the lot's boxes, average item weight, or dimensions
    parsed from the product names."""
    if box.weight_kg and median_weight:
        if box.weight_kg >= median_weight * rules.box_bulky_weight_fraction:
            return True
    if box.units and box.weight_kg / max(box.units, 1) >= rules.bulky_avg_item_kg:
        return True
    volume = sum(_volume_liters(i.description) or 0.0 for i in box.items)
    return volume >= 200.0  # two hundred liters of declared product


def _box_department(box: ContainerStats) -> str:
    """Dominant department (by units) of a box's declared content."""
    counts: Dict[str, int] = defaultdict(int)
    for item in box.items:
        counts[item.department or "?"] += item.qty
    return max(counts, key=counts.get) if counts else "?"


def analyze_containers(
    items: List[ManifestItem],
    rules: Optional[InsightRules] = None,
    baselines: Optional[Dict] = None,
) -> Tuple[List[ContainerStats], List[PalletStats]]:
    """Classify pallets and flag genuinely suspicious containers.

    Two pallet kinds behave completely differently:

    - A "pallet de cajas" stacks several Amazon boxes (normally 6). Each box
      travels FULL, so a box declaring very few units hides content. A box
      full of cheap items is NOT suspicious — value is never a criterion.
    - A pallet with a single package is loose content: "objetos grandes"
      (heavy items, 1-3 units is normal) or "granel" (medium items). Their
      unit counts vary legitimately, so they are never flagged.

    Returns ``(boxes, pallets)``: real boxes (from multi-box pallets only)
    and every pallet with its classification.
    """
    rules = rules or InsightRules()

    by_pallet: Dict[str, List[ManifestItem]] = defaultdict(list)
    for item in items:
        if item.pallet_id:
            by_pallet[item.pallet_id].append(item)

    boxes: List[ContainerStats] = []
    pallets: List[PalletStats] = []

    for pallet_id, p_items in by_pallet.items():
        box_ids = sorted({i.box_id for i in p_items if i.box_id})
        weighted = [(i.weight_kg * i.qty, i.qty) for i in p_items if i.weight_kg]
        total_w = sum(w for w, _ in weighted)
        total_q = sum(q for _, q in weighted)
        avg_weight = total_w / total_q if total_q else None
        units = sum(i.qty for i in p_items)

        if len(box_ids) >= 2:
            pallet_type = "cajas"
        elif (
            len(box_ids) == 1
            and units >= rules.single_box_min_units
            and avg_weight is not None
            and avg_weight <= rules.single_box_max_avg_kg
        ):
            # Dozens of small light items under a single PkgID is the
            # profile of exactly ONE Amazon box: the other five boxes of
            # the pallet are likely gifted.
            pallet_type = "cajas"
        elif avg_weight is not None and avg_weight >= rules.large_object_weight_kg:
            pallet_type = "objetos grandes"
        else:
            pallet_type = "granel"

        pallet = PalletStats(
            pallet_id=pallet_id,
            pallet_type=pallet_type,
            box_count=len(box_ids) if pallet_type == "cajas" else 0,
            lines=len(p_items),
            units=sum(i.qty for i in p_items),
            retail=round(sum(i.line_retail for i in p_items), 2),
            avg_weight_kg=round(avg_weight, 1) if avg_weight is not None else None,
        )

        if pallet_type == "cajas":
            missing = rules.expected_boxes_per_pallet - len(box_ids)
            # Per-box declared retail of THIS pallet -> estimate the gifted box.
            declared_box_retails = [
                sum(i.line_retail for i in p_items if i.box_id == b)
                for b in box_ids
            ]
            if not declared_box_retails:
                # single-box-pallet inferred from light items: use whole pallet
                declared_box_retails = [pallet.retail]
            if missing > 0:
                avg_box = statistics.mean(declared_box_retails)
                lo_box = min(declared_box_retails)
                hi_box = max(declared_box_retails)
                pallet.suspicious = True
                pallet.missing_boxes = missing
                pallet.missing_value_point = round(missing * avg_box, 2)
                pallet.missing_value_low = round(missing * lo_box, 2)
                pallet.missing_value_high = round(missing * hi_box, 2)
                pallet.reason = (
                    f"solo {len(box_ids)} de {rules.expected_boxes_per_pallet} "
                    f"cajas declaradas — un pallet de cajas lleva SIEMPRE "
                    f"{rules.expected_boxes_per_pallet}: {missing} caja(s) van "
                    f"probablemente REGALADAS, valor estimado "
                    f"{pallet.missing_value_point:,.0f} EUR "
                    f"(rango {pallet.missing_value_low:,.0f}–"
                    f"{pallet.missing_value_high:,.0f})"
                )
            for box_id in box_ids:
                b_items = [i for i in p_items if i.box_id == box_id]
                boxes.append(
                    ContainerStats(
                        container_id=box_id,
                        kind="caja",
                        lines=len(b_items),
                        units=sum(i.qty for i in b_items),
                        retail=round(sum(i.line_retail for i in b_items), 2),
                        weight_kg=round(
                            sum((i.weight_kg or 0.0) * i.qty for i in b_items), 1
                        ),
                        items=b_items,
                    )
                )
        pallets.append(pallet)

    # Flag sparse boxes by UNIT COUNT, judged against the HISTORICAL
    # baseline of the box's department when available (a Motor box holds
    # 38-106 items, a Furniture box 7: one generic threshold misfires both
    # ways). Bulky declared items (weight / dimensions) excuse the box.
    if baselines is None:
        baselines = load_baselines()
    if len(boxes) >= 2 or (boxes and baselines):
        median_units = statistics.median(b.units for b in boxes)
        weights = [b.weight_kg for b in boxes if b.weight_kg]
        median_weight = statistics.median(weights) if weights else 0.0

        lot_floor = max(
            rules.box_min_units_abs, median_units * rules.box_sparse_fraction
        )
        for box in boxes:
            dept = _box_department(box)
            base = baselines.get(dept) if baselines else None
            lot_sparse = box.units <= lot_floor and box.units < median_units
            if base:
                base_sparse = box.units < base["p10"]
                sparse = base_sparse and (lot_sparse or len(boxes) < 8)
                typical = f"{base['p25']:.0f}-{base['p75']:.0f}"
                norm_note = (
                    f"en {dept} lo normal es {typical} por caja "
                    f"(histórico de {base['n']} cajas) y en este lote "
                    f"~{median_units:.0f}"
                )
                weight_ref = (base.get("weight") or {}).get("p50") or median_weight
            else:
                sparse = lot_sparse
                norm_note = f"lo normal en este lote es ~{median_units:.0f} por caja"
                weight_ref = median_weight

            if not sparse:
                continue
            if _box_is_bulky(box, weight_ref, rules):
                box.reason = (
                    f"{box.units} objetos pero voluminosos "
                    f"({box.weight_kg:.0f} kg): caja llena, normal"
                )
                continue
            box.suspicious = True
            weight_note = (
                f" y solo {box.weight_kg:.0f} kg (típico ~{weight_ref:.0f} kg)"
                if weight_ref
                else ""
            )
            box.reason = (
                f"{box.units} objetos declarados; {norm_note}{weight_note}. "
                f"Las cajas van llenas a tope: probable contenido REGALADO dentro"
            )

    boxes.sort(key=lambda b: b.units)
    type_order = {"cajas": 0, "granel": 1, "objetos grandes": 2}
    pallets.sort(key=lambda p: (type_order.get(p.pallet_type, 3), p.units))
    return boxes, pallets


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def deep_analyze(
    items: List[ManifestItem],
    label: str = "manifest",
    rules: Optional[InsightRules] = None,
    resolver=None,
    baselines: Optional[Dict] = None,
    max_verify: int = 12,
) -> ManifestInsights:
    """Run the full deep analysis over parsed manifest items.

    ``resolver`` (a pricing.PriceResolver) enables real-price verification of
    giveaway suspects (the ``max_verify`` highest-value ones). Without it, only
    conservative typical-price heuristics are used (uncertain suspects are then
    reported "sin verificar")."""
    rules = rules or InsightRules()
    warnings: List[str] = []

    total_units = sum(i.qty for i in items)
    total_retail = round(sum(i.line_retail for i in items), 2)

    tvs = find_tvs(items, rules)
    tv_units = sum(t.item.qty for t in tvs)
    tv_loss = round(sum(t.item.line_retail for t in tvs), 2)

    giveaways = find_giveaways(items, rules, resolver=resolver, max_verify=max_verify)

    boxes, pallets = analyze_containers(items, rules, baselines)
    suspicious_pallets = [p for p in pallets if p.suspicious]

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
        giveaway_value_sure=round(
            sum(g.hidden_value for g in giveaways if g.tier == "seguro"), 2
        ),
        giveaway_value_unverified=round(
            sum(g.hidden_value for g in giveaways if g.tier == "sin_verificar"), 2
        ),
        boxes=boxes,
        pallets=pallets,
        suspicious_boxes=[b for b in boxes if b.suspicious],
        suspicious_pallets=suspicious_pallets,
        gifted_box_value_point=round(
            sum(p.missing_value_point for p in suspicious_pallets), 2
        ),
        gifted_box_value_low=round(
            sum(p.missing_value_low for p in suspicious_pallets), 2
        ),
        gifted_box_value_high=round(
            sum(p.missing_value_high for p in suspicious_pallets), 2
        ),
        top_items=top_items,
        warnings=warnings,
    )


def quick_read(insights: "ManifestInsights") -> List[str]:
    """Plain-language conclusions with concrete figures, for humans."""
    bullets: List[str] = []

    # Whole gifted boxes first: it is the single most valuable signal.
    if insights.suspicious_pallets:
        total_missing = sum(p.missing_boxes for p in insights.suspicious_pallets)
        detail = "; ".join(
            f"pallet {p.pallet_id}: {p.box_count} de 6"
            for p in insights.suspicious_pallets[:4]
        )
        bullets.append(
            f"🎁📦 ¡{total_missing} CAJAS ENTERAS probablemente REGALADAS! "
            f"valor estimado {insights.gifted_box_value_point:,.0f} EUR "
            f"(rango {insights.gifted_box_value_low:,.0f}–"
            f"{insights.gifted_box_value_high:,.0f}). "
            f"Los pallets de cajas llevan siempre 6 y aquí faltan ({detail})."
        )

    confirmed = [g for g in insights.giveaways if g.tier == "seguro"]
    unverified = [g for g in insights.giveaways if g.tier == "sin_verificar"]
    if confirmed:
        estimated = sum(g.estimated_value for g in confirmed)
        declared = sum(g.item.line_retail for g in confirmed)
        bullets.append(
            f"🎁 {len(confirmed)} artículos mal clasificados CONFIRMADOS: valen "
            f"~{estimated:,.0f} EUR y están declarados por {declared:,.0f} EUR "
            f"(uplift {insights.giveaway_value_sure:,.0f} EUR; pruebas con enlace "
            f"en la tabla de regalados)."
        )
    if unverified:
        bullets.append(
            f"🔎 {len(unverified)} sospechosos no se pudieron verificar online "
            f"(~{insights.giveaway_value_unverified:,.0f} EUR potenciales): "
            f"revisar a mano con el enlace."
        )

    if insights.suspicious_boxes:
        detail = "; ".join(
            f"{b.container_id} ({b.units} objetos, {b.weight_kg:.0f} kg)"
            for b in insights.suspicious_boxes[:4]
        )
        bullets.append(
            f"📦 {len(insights.suspicious_boxes)} cajas demasiado vacías para "
            f"su peso (van siempre llenas a tope): probable contenido regalado "
            f"dentro — {detail}. Contenido declarado listado abajo."
        )

    if insights.tv_units:
        bullets.append(
            f"📺 {insights.tv_units} TVs (categoría Televisions) = pérdida de "
            f"{insights.tv_loss_retail:,.0f} EUR (los paneles llegan rotos)."
        )

    by_type: Dict[str, int] = defaultdict(int)
    for pallet in insights.pallets:
        by_type[pallet.pallet_type] += 1
    if by_type:
        labels = {"cajas": "de cajas", "granel": "a granel",
                  "objetos grandes": "de objetos grandes"}
        parts = [
            f"{count} {labels.get(name, name)}" for name, count in by_type.items()
        ]
        bullets.append(
            f"🚚 Pallets: {', '.join(parts)}. En los de objetos grandes, "
            "pocas unidades es lo normal (no se marcan)."
        )

    bullets.append(
        f"✅ Retail declarado {insights.total_retail:,.0f} EUR · efectivo (sin "
        f"TVs) {insights.effective_retail:,.0f} EUR · REAL estimado con oculto "
        f"~{insights.real_retail_point:,.0f} EUR."
    )
    return bullets


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

    out += ["## Lectura rápida", ""]
    out += [f"- {bullet}" for bullet in quick_read(insights)]
    out += [""]

    confirmed = [g for g in insights.giveaways if g.tier == "seguro"]
    unverified = [g for g in insights.giveaways if g.tier == "sin_verificar"]
    out += [
        "## Resumen",
        "",
        f"- Líneas: **{insights.total_lines}** · Unidades: **{insights.total_units}**",
        f"- Retail declarado: **{insights.total_retail:,.2f} EUR** "
        f"(media {insights.avg_unit_retail:,.2f} EUR/ud)",
        f"- TVs (pérdida, categoría Televisions): **{insights.tv_units} uds, "
        f"{insights.tv_loss_retail:,.2f} EUR**",
        f"- **Retail efectivo (sin TVs): {insights.effective_retail:,.2f} EUR**",
        f"- Regalados confirmados: **{len(confirmed)}** "
        f"(uplift {insights.giveaway_value_sure:,.2f} EUR) · "
        f"sin verificar: {len(unverified)} "
        f"({insights.giveaway_value_unverified:,.2f} EUR)",
        f"- **Cajas regaladas estimadas: {insights.gifted_box_value_point:,.0f} EUR** "
        f"(rango {insights.gifted_box_value_low:,.0f}–"
        f"{insights.gifted_box_value_high:,.0f})",
        f"- **Valor REAL estimado del lote: {insights.real_retail_point:,.0f} EUR** "
        f"(declarado {insights.total_retail:,.0f} + oculto "
        f"{insights.hidden_value_point:,.0f})",
        f"- Cajas reales: {len(insights.boxes)} ({len(insights.suspicious_boxes)} demasiado vacías) · "
        f"Pallets: {len(insights.pallets)} ({len(insights.suspicious_pallets)} con cajas de menos)",
        "",
    ]

    out += ["## Por departamento", ""]
    out += _group_table(insights.by_department)
    out += ["", "## Por categoría", ""]
    out += _group_table(insights.by_category)
    out += ["", "## Por subcategoría", ""]
    out += _group_table(insights.by_subcategory)
    out += ["", "## Por condición (solo dato, NO afecta a la valoración)", ""]
    out += _group_table(insights.by_condition)

    out += ["", "## Televisores (pérdida: los paneles llegan rotos)", ""]
    if insights.tvs:
        out += _table(
            ["Descripción", "Uds", "Retail EUR", "Detección"],
            [
                [(t.item.description or "")[:60], str(t.item.qty),
                 f"{t.item.line_retail:,.2f}", t.reason]
                for t in insights.tvs
            ],
        )
        out.append(f"\n**Pérdida total estimada: {insights.tv_loss_retail:,.2f} EUR**")
    else:
        out.append("Sin televisores (categoría Televisions) en este lote.")

    out += ["", "## Cajas regaladas (lo más importante)", ""]
    if insights.suspicious_pallets:
        out += [
            f"**Valor estimado de las cajas que faltan: "
            f"{insights.gifted_box_value_point:,.0f} EUR** "
            f"(rango {insights.gifted_box_value_low:,.0f}–"
            f"{insights.gifted_box_value_high:,.0f}). Un pallet de cajas lleva "
            f"siempre 6; las que faltan viajan sin declarar.",
            "",
        ]
        out += _table(
            ["Pallet", "Cajas decl.", "Faltan", "Valor estimado EUR", "Rango EUR"],
            [
                [p.pallet_id[:20], f"{p.box_count}/6", str(p.missing_boxes),
                 f"{p.missing_value_point:,.0f}",
                 f"{p.missing_value_low:,.0f}–{p.missing_value_high:,.0f}"]
                for p in insights.suspicious_pallets
            ],
        )
    else:
        out.append("Ningún pallet de cajas incompleto.")

    out += ["", "## Artículos regalados (mal clasificados)", ""]
    if insights.giveaways:
        out += [
            f"**Valor estimado regalado confirmado: "
            f"{insights.giveaway_value_sure:,.2f} EUR** "
            f"(+{insights.giveaway_value_unverified:,.2f} EUR sin verificar). "
            "Pruebas, una línea por artículo:",
            "",
        ]
        rows = []
        for g in insights.giveaways:
            asin_link = (
                f"[{g.item.asin}]({g.amazon_url})" if g.item.asin and g.amazon_url
                else (g.item.asin or "-")
            )
            tier = "CONFIRMADO" if g.tier == "seguro" else "SIN VERIF."
            rows.append([
                (g.item.description or "")[:50],
                asin_link,
                f"{g.item.unit_retail:,.2f}",
                f"{g.reference_price:,.0f}",
                f"**{g.hidden_value:,.0f}**",
                g.reference_source,
                tier,
            ])
        out += _table(
            ["Descripción", "ASIN", "Declarado EUR", "Real est. EUR",
             "Oculto EUR", "Fuente", "Estado"],
            rows,
        )
        out.append("")
        out.append(
            "> 'Real est.' = precio verificado (BD/caché/Amazon) cuando se pudo "
            "resolver; si no, el típico mínimo conservador. 'Oculto' = real − "
            "declarado. Los SIN VERIF. requieren un vistazo manual (clic en ASIN)."
        )
    else:
        out.append("Sin regalados detectados con las reglas actuales.")

    out += ["", "## Cajas (solo las de pallets de cajas: van siempre llenas a tope)", ""]
    if insights.boxes:
        out += _table(
            ["Caja", "Objetos", "Peso kg", "Retail EUR", "¿Demasiado vacía?"],
            [
                [b.container_id[:20], str(b.units), f"{b.weight_kg:,.0f}",
                 f"{b.retail:,.0f}",
                 ("🚩 " + b.reason) if b.suspicious else (b.reason or "no")]
                for b in insights.boxes
            ],
        )
        out.append("")
        out.append(
            "> Una caja con pocos objetos solo es sospechosa si además pesa "
            "poco: pocos objetos voluminosos también llenan la caja. El valor "
            "declarado nunca es criterio."
        )
    else:
        out.append("Este lote no tiene pallets de cajas.")

    if insights.suspicious_boxes:
        out += ["", "## Contenido declarado de las cajas sospechosas", ""]
        out.append(
            "Lo que SÍ declaran (poco y ligero): el hueco restante es el "
            "contenido regalado probable. Verifica tamaños con el enlace."
        )
        for box in insights.suspicious_boxes:
            out += ["", f"### Caja {box.container_id} — {box.units} objetos, "
                        f"{box.weight_kg:,.0f} kg, {box.retail:,.0f} EUR", ""]
            out += _table(
                ["Artículo", "Uds", "Peso kg", "EUR", "Amazon"],
                [
                    [
                        (i.description or "")[:60],
                        str(i.qty),
                        f"{i.weight_kg:.1f}" if i.weight_kg else "?",
                        f"{i.line_retail:,.2f}",
                        f"[{i.asin}]({AMAZON_URL.format(asin=i.asin)})" if i.asin else "-",
                    ]
                    for i in sorted(
                        box.items, key=lambda x: x.line_retail, reverse=True
                    )[:15]
                ],
            )

    out += ["", "## Pallets (clasificados)", ""]
    if insights.pallets:
        out += _table(
            ["Pallet", "Tipo", "Cajas", "Objetos", "Retail EUR", "Peso medio kg", "Aviso"],
            [
                [p.pallet_id[:20], p.pallet_type,
                 str(p.box_count) if p.pallet_type == "cajas" else "-",
                 str(p.units), f"{p.retail:,.0f}",
                 f"{p.avg_weight_kg:.1f}" if p.avg_weight_kg is not None else "?",
                 ("🚩 " + p.reason) if p.suspicious else ""]
                for p in insights.pallets
            ],
        )
        out.append("")
        out.append(
            "> *cajas* = ~6 cajas de Amazon apiladas (muchos objetos pequeños). "
            "*objetos grandes* = artículos voluminosos sueltos: pocas unidades "
            "es lo normal y no se marca. *granel* = objetos medianos sueltos."
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
