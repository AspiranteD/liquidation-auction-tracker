"""Per-lot PDF reports and the scheduled digest.

Two consumers:

- ``watch`` (CLI): runs every few minutes, detects auctions whose manifest has
  not been reported yet, generates the markdown + PDF report and sends a
  compact WhatsApp summary (CallMeBot cannot attach files, so the PDF itself
  travels by email with the digest).
- ``digest`` (CLI): runs at fixed times, bundles every active lot into a
  single PDF and emails it.

State (which auctions were already reported / failed recently) lives in a
small JSON file so runs stay idempotent without touching the SQLite schema.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from fpdf import FPDF

from . import analyzer, insights
from .alerts import AlertDecision
from .calculator import BidCalculator
from .client import BStockClient
from .config import Settings
from .models import Auction

logger = logging.getLogger(__name__)

STATE_FILE = "data/watch_state.json"
RETRY_FAILED_AFTER = timedelta(hours=6)

# Beyond this many minutes to close, the current bid is still "provisional":
# liquidation auctions open with a low starting bid, so the price filter only
# becomes meaningful near close. Reports say so instead of implying a deal.
PROVISIONAL_MINUTES = 30

_FONT_REGULAR = r"C:\Windows\Fonts\arial.ttf"
_FONT_BOLD = r"C:\Windows\Fonts\arialbd.ttf"


@dataclass
class LotReport:
    auction: Auction
    insights: Optional[insights.ManifestInsights] = None
    decision: Optional[AlertDecision] = None
    csv_path: Optional[str] = None
    pdf_path: Optional[str] = None
    error: Optional[str] = None
    is_new: bool = False


# ---------------------------------------------------------------------------
# Verdict: coherent good/bad judgment per lot
# ---------------------------------------------------------------------------

def minutes_to_close(auction: Auction) -> Optional[float]:
    if auction.end_time is None:
        return None
    return (auction.end_time - datetime.now(timezone.utc)).total_seconds() / 60.0


def price_status(report: "LotReport") -> str:
    """Honest one-liner about the bid: how it stands AND whether it can be
    trusted yet (provisional far from close, reliable near it)."""
    a, d = report.auction, report.decision
    mins = minutes_to_close(a)
    pct = (
        f"{d.current_total_pct:.0%} del retail"
        if d and d.current_total_pct is not None
        else "?"
    )
    bid = f"{a.current_bid:,.0f} EUR" if a.current_bid else "sin pujas"
    ceiling = f"{d.threshold_pct:.0%}" if d else "12%"
    if mins is None:
        return f"Puja {bid} (coste {pct}, tu límite {ceiling})."
    if mins < 0:
        return f"Cerrada. Puja final {bid} (coste {pct})."
    if mins > PROVISIONAL_MINUTES:
        hours = mins / 60.0
        when = f"{mins:.0f} min" if mins < 90 else f"{hours:.1f} h"
        return (
            f"Puja {bid} (coste {pct}) — PROVISIONAL: faltan {when} y subirá. "
            f"La evaluación real es ~30 min antes de cerrar."
        )
    return (
        f"Puja {bid} (coste {pct}, tu límite {ceiling}) — fiable: "
        f"faltan {mins:.0f} min."
    )


def lot_verdict(report: "LotReport") -> Tuple[str, str, List[str]]:
    """Return (semáforo, etiqueta, notas) judging the lot coherently.

    Quality (manifest, static) decides good/meh; the price filter has
    already excluded over-limit lots upstream, so the verdict focuses on
    what's actually inside the truck.
    """
    r, d = report.insights, report.decision
    notes: List[str] = []

    if d is not None and d.over_limit:
        # Shouldn't normally reach a report, but stay coherent if it does.
        return ("🔴", "PASA — la puja ya supera tu límite", notes)

    if r is None:
        return ("⬜", "Sin manifiesto para valorar el contenido", notes)

    tv_share = (r.tv_loss_retail / r.total_retail) if r.total_retail else 0.0
    upside = r.giveaway_value_sure + r.giveaway_value_doubt
    missing_boxes = sum(p.missing_boxes for p in r.suspicious_pallets)
    has_upside = bool(
        r.giveaway_value_sure > 0 or missing_boxes or r.suspicious_boxes
    )

    if r.giveaway_value_sure > 0:
        notes.append(
            f"{r.giveaway_value_sure:,.0f} EUR en regalados SEGUROS "
            f"(+{r.giveaway_value_doubt:,.0f} por verificar)"
        )
    elif upside > 0:
        notes.append(f"{upside:,.0f} EUR en regalados por verificar")
    if missing_boxes:
        notes.append(f"{missing_boxes} cajas enteras probablemente regaladas")
    if r.suspicious_boxes:
        notes.append(f"{len(r.suspicious_boxes)} cajas demasiado vacías")
    if tv_share > 0.10:
        notes.append(
            f"{tv_share:.0%} del retail son TVs (pérdida: {r.tv_loss_retail:,.0f} EUR)"
        )

    if tv_share >= 0.30:
        return ("🔴", "FLOJO — demasiadas TVs (pérdida)", notes)
    if r.giveaway_value_sure > 0 or missing_boxes:
        return ("🟢", "INTERESA — hay valor oculto detectado", notes)
    if has_upside:
        return ("🟡", "A REVISAR — posible valor oculto (verificar)", notes)
    return ("🟡", "NORMAL — sin upside especial", notes)


# ---------------------------------------------------------------------------
# Watch state
# ---------------------------------------------------------------------------

def load_state(path: str = STATE_FILE) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(state: dict, path: str = STATE_FILE) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def _should_retry(entry: Optional[dict], now: datetime) -> bool:
    """A failed auction is retried only after a cooldown (detail page +
    manifest endpoint cost two requests per attempt)."""
    if not entry:
        return True
    if entry.get("status") == "done":
        return False
    try:
        last = datetime.fromisoformat(entry.get("last_attempt", ""))
    except ValueError:
        return True
    return now - last >= RETRY_FAILED_AFTER


# ---------------------------------------------------------------------------
# PDF rendering
# ---------------------------------------------------------------------------

def _new_pdf() -> tuple[FPDF, str]:
    """Create an FPDF with a Unicode font when available."""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=14)
    family = "helvetica"
    if os.path.exists(_FONT_REGULAR):
        try:
            pdf.add_font("ui", style="", fname=_FONT_REGULAR)
            bold = _FONT_BOLD if os.path.exists(_FONT_BOLD) else _FONT_REGULAR
            pdf.add_font("ui", style="B", fname=bold)
            family = "ui"
        except Exception as exc:  # noqa: BLE001 - fall back to core font
            logger.warning("Could not load Unicode font: %s", exc)
    return pdf, family


_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿⬀-⯿️]+\\s?"
)


def _safe(text: str, family: str) -> str:
    # Arial has no emoji glyphs; the markdown/WhatsApp versions keep them.
    text = _EMOJI_RE.sub("", text)
    if family != "helvetica":
        return text
    return text.encode("latin-1", errors="replace").decode("latin-1")


def _pdf_heading(pdf: FPDF, family: str, text: str, size: int = 13) -> None:
    pdf.set_font(family, "B", size)
    pdf.cell(0, 8, _safe(text, family), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)


def _pdf_line(pdf: FPDF, family: str, text: str, size: int = 9) -> None:
    pdf.set_font(family, "", size)
    # new_x: fpdf2 >= 2.8 leaves the cursor at the text's right edge by
    # default, which starves the next multi_cell(0, ...) of width.
    pdf.multi_cell(0, 5, _safe(text, family), new_x="LMARGIN", new_y="NEXT")


def _pdf_table(
    pdf: FPDF,
    family: str,
    headers: List[str],
    rows: List[List[str]],
    widths: List[int],
    links: Optional[List[Optional[str]]] = None,
    link_col: Optional[int] = None,
) -> None:
    """Bordered table. ``links``/``link_col`` make one column clickable
    (one URL per row, e.g. the ASIN column pointing at Amazon)."""
    pdf.set_font(family, "B", 8)
    for header, width in zip(headers, widths):
        pdf.cell(width, 6, _safe(header, family), border=1)
    pdf.ln()
    pdf.set_font(family, "", 8)
    for row_index, row in enumerate(rows):
        for col_index, (value, width) in enumerate(zip(row, widths)):
            # crude truncation so cells never overflow
            max_chars = max(4, int(width / 1.7))
            link = ""
            if links is not None and col_index == link_col:
                link = links[row_index] or ""
            if link:
                pdf.set_text_color(0, 0, 200)
            pdf.cell(
                width, 6, _safe(str(value)[:max_chars], family),
                border=1, link=link,
            )
            if link:
                pdf.set_text_color(0, 0, 0)
        pdf.ln()
    pdf.ln(2)


_VERDICT_COLOR = {
    "🟢": (22, 163, 74),
    "🟡": (245, 158, 11),
    "🔴": (220, 38, 38),
    "⬜": (148, 163, 184),
}


def _render_lot_into(
    pdf: FPDF,
    family: str,
    result: insights.ManifestInsights,
    auction: Optional[Auction],
    report: Optional["LotReport"] = None,
) -> None:
    pdf.add_page()
    title = f"Lote {result.label}"
    if auction:
        title = f"#{auction.auction_id} — {auction.lot_type or 'Lote'} ({auction.country})"
    _pdf_heading(pdf, family, title, size=14)
    if auction:
        _pdf_line(pdf, family, (auction.title or "")[:160])
        if auction.end_time:
            _pdf_line(pdf, family, f"Cierra: {auction.end_time:%d/%m/%Y %H:%M} — {auction.url}")
        pdf.ln(2)

    # Verdict banner: a glance tells you whether the lot is worth it.
    if report is not None:
        level, label, _ = lot_verdict(report)
        color = _VERDICT_COLOR.get(level, (148, 163, 184))
        pdf.set_fill_color(*color)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font(family, "B", 12)
        pdf.cell(0, 9, _safe(f"  {label}", family), fill=True,
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(30, 58, 138)
        pdf.set_font(family, "", 9)
        pdf.multi_cell(0, 5, _safe(price_status(report), family),
                       new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    _pdf_heading(pdf, family, "Lectura rápida")
    for bullet in insights.quick_read(result):
        _pdf_line(pdf, family, f"• {bullet}")
    pdf.ln(2)

    _pdf_heading(pdf, family, "Resumen")
    sure_g = sum(1 for g in result.giveaways if g.tier == "seguro")
    doubt_g = sum(1 for g in result.giveaways if g.tier == "dudoso")
    _pdf_line(
        pdf,
        family,
        f"Líneas: {result.total_lines}  ·  Unidades: {result.total_units}\n"
        f"Retail declarado: {result.total_retail:,.2f} EUR "
        f"(media {result.avg_unit_retail:,.2f} EUR/ud)\n"
        f"TVs (pérdida): {result.tv_units} uds, {result.tv_loss_retail:,.2f} EUR\n"
        f"Retail efectivo (sin TVs): {result.effective_retail:,.2f} EUR\n"
        f"Regalados: {sure_g} seguros, {doubt_g} dudosos — "
        f"valor estimado regalado: {result.giveaway_value_sure:,.0f} EUR seguros "
        f"+ {result.giveaway_value_doubt:,.0f} EUR dudosos\n"
        f"Cajas demasiado vacías: {len(result.suspicious_boxes)}/{len(result.boxes)}  ·  "
        f"Pallets con cajas de menos: "
        f"{len(result.suspicious_pallets)}/{len(result.pallets)}",
    )
    pdf.ln(2)

    _pdf_heading(pdf, family, "Por departamento")
    _pdf_table(
        pdf, family,
        ["Departamento", "Uds", "% uds", "Retail EUR", "% retail"],
        [[g.name, g.units, f"{g.pct_units}%", f"{g.retail:,.0f}", f"{g.pct_retail}%"]
         for g in result.by_department[:12]],
        [70, 20, 20, 35, 25],
    )

    _pdf_heading(pdf, family, "Por categoría (top 12)")
    _pdf_table(
        pdf, family,
        ["Categoría", "Uds", "% uds", "Retail EUR", "% retail"],
        [[g.name, g.units, f"{g.pct_units}%", f"{g.retail:,.0f}", f"{g.pct_retail}%"]
         for g in result.by_category[:12]],
        [70, 20, 20, 35, 25],
    )

    sure_tvs = [t for t in result.tvs if t.confidence == "seguro"]
    _pdf_heading(pdf, family, "Televisores (pérdida)")
    if sure_tvs:
        _pdf_table(
            pdf, family,
            ["Descripción", "Retail EUR"],
            [[(t.item.description or ""), f"{t.item.line_retail:,.2f}"] for t in sure_tvs],
            [150, 35],
        )
    else:
        _pdf_line(pdf, family, "Sin televisores detectados.")
        pdf.ln(1)

    total_hidden = result.giveaway_value_sure + result.giveaway_value_doubt
    _pdf_heading(
        pdf, family,
        f"Artículos regalados — valor estimado: {total_hidden:,.0f} EUR",
    )
    if result.giveaways:
        _pdf_line(
            pdf, family,
            f"{result.giveaway_value_sure:,.0f} EUR en seguros + "
            f"{result.giveaway_value_doubt:,.0f} EUR en dudosos. "
            "Oculto = valor real estimado - declarado. El ASIN enlaza a Amazon.",
            size=8,
        )
        _pdf_table(
            pdf, family,
            ["Descripción", "Declarado", "Est. real", "Oculto", "Nivel", "ASIN"],
            [[(g.item.description or ""), f"{g.item.unit_retail:,.2f}",
              f"{g.estimated_value:,.0f}", f"{g.hidden_value:,.0f}", g.tier,
              g.item.asin or "-"]
             for g in result.giveaways],
            [85, 20, 18, 18, 16, 28],
            links=[g.amazon_url for g in result.giveaways],
            link_col=5,
        )
    else:
        _pdf_line(pdf, family, "Sin regalados detectados.")
        pdf.ln(1)

    _pdf_heading(pdf, family, "Cajas demasiado vacías (van siempre llenas a tope)")
    if result.suspicious_boxes:
        _pdf_table(
            pdf, family,
            ["Caja", "Objetos", "Peso kg", "Retail EUR", "Motivo"],
            [[b.container_id, b.units, f"{b.weight_kg:,.0f}", f"{b.retail:,.0f}",
              b.reason]
             for b in result.suspicious_boxes],
            [28, 15, 16, 22, 107],
        )
        _pdf_line(
            pdf, family,
            "Pocos objetos solo es sospechoso si además pesan poco (pocos "
            "objetos voluminosos también llenan la caja). El valor declarado "
            "nunca es criterio.",
            size=8,
        )
        for box in result.suspicious_boxes:
            _pdf_heading(
                pdf, family,
                f"Contenido declarado de la caja {box.container_id} "
                f"({box.units} objetos, {box.weight_kg:,.0f} kg)",
                size=10,
            )
            _pdf_table(
                pdf, family,
                ["Artículo", "Uds", "Peso kg", "EUR", "ASIN"],
                [[(i.description or ""), i.qty,
                  f"{i.weight_kg:.1f}" if i.weight_kg else "?",
                  f"{i.line_retail:,.2f}", i.asin or "-"]
                 for i in sorted(
                     box.items, key=lambda x: x.line_retail, reverse=True
                 )[:15]],
                [98, 12, 18, 22, 30],
                links=[
                    (insights.AMAZON_URL.format(asin=i.asin) if i.asin else None)
                    for i in sorted(
                        box.items, key=lambda x: x.line_retail, reverse=True
                    )[:15]
                ],
                link_col=4,
            )
    else:
        _pdf_line(pdf, family, "Ninguna caja demasiado vacía.")
        pdf.ln(1)

    _pdf_heading(pdf, family, "Pallets (clasificados)")
    if result.pallets:
        _pdf_table(
            pdf, family,
            ["Pallet", "Tipo", "Cajas", "Objetos", "Retail EUR", "Peso med. kg", "Aviso"],
            [[p.pallet_id, p.pallet_type,
              str(p.box_count) if p.pallet_type == "cajas" else "-",
              p.units, f"{p.retail:,.0f}",
              f"{p.avg_weight_kg:.1f}" if p.avg_weight_kg is not None else "?",
              p.reason]
             for p in result.pallets],
            [24, 26, 13, 16, 24, 20, 65],
        )
        _pdf_line(
            pdf, family,
            "cajas = ~6 cajas de Amazon apiladas. objetos grandes = artículos "
            "voluminosos sueltos (pocas unidades es normal, no se marca). "
            "granel = objetos medianos sueltos.",
            size=8,
        )
    else:
        _pdf_line(pdf, family, "Sin información de pallets.")
        pdf.ln(1)

    _pdf_heading(pdf, family, "Top 10 artículos por valor")
    _pdf_table(
        pdf, family,
        ["Descripción", "Unitario", "Línea EUR", "Condición"],
        [[(i.description or ""), f"{i.unit_retail:,.2f}", f"{i.line_retail:,.2f}",
          i.condition or "?"] for i in result.top_items],
        [110, 25, 25, 28],
    )


def render_pdf(
    result: insights.ManifestInsights,
    path: str,
    auction: Optional[Auction] = None,
    report: Optional["LotReport"] = None,
) -> str:
    pdf, family = _new_pdf()
    _render_lot_into(pdf, family, result, auction, report=report)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    pdf.output(path)
    return path


_VERDICT_RANK = {"🟢": 0, "🟡": 1, "⬜": 2, "🔴": 3}


def build_digest_pdf(reports: List[LotReport], path: str) -> str:
    """One combined PDF: a ranked summary page plus one section per lot.

    Only key, within-limit lots carry insights; over-limit and no-manifest
    candidates are summarised but not detailed.
    """
    pdf, family = _new_pdf()
    pdf.add_page()
    _pdf_heading(
        pdf, family,
        f"Lotes clave B-Stock — {datetime.now():%d/%m/%Y %H:%M}", 15
    )

    analyzed = [r for r in reports if r.insights]
    over_limit = [
        r for r in reports if not r.insights and r.decision and r.decision.over_limit
    ]
    failed = [r for r in reports if not r.insights and r.error]

    # Rank by verdict (interesting first), then by hidden value.
    def sort_key(r: LotReport):
        level, _, _ = lot_verdict(r)
        hidden = r.insights.giveaway_value_sure + r.insights.giveaway_value_doubt
        return (_VERDICT_RANK.get(level, 9), -hidden)

    analyzed.sort(key=sort_key)

    if not analyzed and not over_limit and not failed:
        _pdf_line(pdf, family, "Ahora mismo no hay lotes clave que cumplan tus filtros.")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        pdf.output(path)
        return path

    _pdf_line(
        pdf, family,
        f"{len(analyzed)} lotes clave dentro de tu límite · "
        f"{len(over_limit)} excluidos por superar el precio · "
        f"{len(failed)} sin manifiesto.",
        size=9,
    )
    pdf.ln(1)

    if analyzed:
        _pdf_table(
            pdf, family,
            ["", "Subasta", "Tipo", "Efectivo EUR", "Regalado EUR",
             "Cajas susp.", "Cierra"],
            [[
                lot_verdict(r)[1].split(" —")[0],
                f"#{r.auction.auction_id}",
                r.auction.lot_type or "?",
                f"{r.insights.effective_retail:,.0f}",
                f"{r.insights.giveaway_value_sure + r.insights.giveaway_value_doubt:,.0f}"
                f" ({len(r.insights.giveaways)})",
                f"{len(r.insights.suspicious_boxes)}/{len(r.insights.boxes)}",
                f"{r.auction.end_time:%d/%m %H:%M}" if r.auction.end_time else "?",
            ] for r in analyzed],
            [18, 20, 28, 26, 30, 20, 24],
        )

    if over_limit:
        _pdf_heading(pdf, family, "Excluidos: la puja ya supera tu límite", 11)
        for r in over_limit:
            pct = (
                f"{r.decision.current_total_pct:.0%}"
                if r.decision.current_total_pct is not None else "?"
            )
            _pdf_line(
                pdf, family,
                f"#{r.auction.auction_id} {r.auction.lot_type or '?'} — "
                f"coste {pct} (límite {r.decision.threshold_pct:.0%})",
                size=9,
            )
    if failed:
        _pdf_heading(pdf, family, "Sin manifiesto disponible", 11)
        for r in failed:
            _pdf_line(
                pdf, family,
                f"#{r.auction.auction_id} {(r.auction.title or '')[:90]} — {r.error}",
                size=9,
            )

    for r in analyzed:
        _render_lot_into(pdf, family, r.insights, r.auction, report=r)

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    pdf.output(path)
    return path


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

def collect_reports(
    client: BStockClient,
    settings: Settings,
    only_new: bool = False,
    report_dir: str = "data/reports",
) -> List[LotReport]:
    """Scan active auctions and build reports for the KEY ones only.

    Filtering is deliberate (the user only wants the relevant lots):

    - Non-key on static grounds (wrong country/type, retail below the
      per-type minimum) are dropped outright — no manifest downloaded.
    - Key lots whose current bid already exceeds the cost ceiling are
      returned WITHOUT insights (``decision.over_limit``) so callers can
      count them as excluded but never report them as buys.
    - Key lots within the ceiling get their manifest downloaded and
      analysed. Far from close the price is provisional (initial bids are
      low) — the report says so.

    ``only_new`` skips auctions already reported ("done" in the state file)
    and applies a cooldown before retrying failed ones.
    """
    now = datetime.now()
    state = load_state()
    calculator = BidCalculator()
    pdf_dir = os.path.join(report_dir, "pdf")
    results: List[LotReport] = []

    # save_state runs even if the auction listing blows up mid-scan: losing
    # the "done" marks would resend every WhatsApp summary on the next run.
    try:
        for country in settings.countries:
            auctions = client.list_auctions(country=country)
            for auction in auctions:
                from . import alerts

                decision = alerts.evaluate(auction, settings.rules, calculator)
                if not decision.static_ok:
                    continue  # not a candidate — drop, don't even download

                report = LotReport(auction=auction, decision=decision)
                if decision.over_limit:
                    # Candidate but already too expensive: keep it so the
                    # digest can say "N excluidos por precio", no manifest.
                    results.append(report)
                    continue

                key = str(auction.auction_id)
                entry = state.get(key)
                if only_new and not _should_retry(entry, now):
                    continue  # already reported, or failed and cooling down

                try:
                    lot_id = auction.lot_id or client.fetch_lot_id(auction)
                    if not lot_id:
                        raise RuntimeError("lot_id no encontrado")
                    csv_path = os.path.join(
                        settings.manifest_dir, f"{auction.auction_id}_{lot_id}.csv"
                    )
                    if not os.path.exists(csv_path):
                        client.download_manifest(lot_id, csv_path)
                    items = analyzer.parse_manifest(csv_path)
                    if not items:
                        raise RuntimeError("manifiesto vacío")
                    label = f"{auction.auction_id}_{lot_id}"
                    result = insights.deep_analyze(items, label=label)
                    report.insights = result
                    report.csv_path = csv_path
                    # markdown + pdf alongside each other
                    md_path = os.path.join(report_dir, f"{label}.md")
                    with open(md_path, "w", encoding="utf-8") as fh:
                        fh.write(insights.render_report(result))
                    report.pdf_path = render_pdf(
                        result, os.path.join(pdf_dir, f"{label}.pdf"), auction,
                        report=report,
                    )
                    report.is_new = not entry or entry.get("status") != "done"
                    state[key] = {
                        "status": "done",
                        "last_attempt": now.isoformat(),
                        "csv": csv_path,
                        "pdf": report.pdf_path,
                    }
                except Exception as exc:  # noqa: BLE001 - keep going per auction
                    report.error = str(exc)
                    state[key] = {
                        "status": "failed",
                        "last_attempt": now.isoformat(),
                    }
                    logger.warning("No report for %s: %s", auction.auction_id, exc)
                results.append(report)
    finally:
        save_state(state)
    return results


def build_whatsapp_lot_summary(report: LotReport) -> str:
    """Compact WhatsApp text for a key, within-limit lot. Leads with the
    verdict so a glance tells you whether it's worth it (CallMeBot can't
    attach files; the PDF travels with the email digest)."""
    a, r = report.auction, report.insights
    level, label, notes = lot_verdict(report)
    max_bid = (
        f" · puja máx {report.decision.breakdown.bid:,.0f} EUR"
        if report.decision and report.decision.breakdown
        else ""
    )
    lines = [
        f"{level} {label}",
        f"Lote clave #{a.auction_id} ({a.country}) — {a.lot_type or 'Lote'}",
        f"Retail {r.total_retail:,.0f} EUR / efectivo {r.effective_retail:,.0f} EUR"
        f"{max_bid}",
        price_status(report),
    ]
    for note in notes:
        lines.append(f"  · {note}")
    for g in [g for g in r.giveaways if g.tier == "seguro"][:3]:
        lines.append(
            f"  🎁 {(g.item.description or '')[:38]} — {g.item.unit_retail:,.0f} EUR "
            f"(vale ~{g.estimated_value:,.0f})"
        )
    if a.end_time:
        lines.append(f"Cierra: {a.end_time:%d/%m %H:%M}")
    lines.append("PDF completo en el email (9/12/21h)")
    lines.append(a.url)
    return "\n".join(lines)
