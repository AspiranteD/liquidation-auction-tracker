"""Renderiza el estudio de camiones propios como PDF ejecutivo.

Diseño data-dense (sistema generado con ui-ux-pro-max): portada tipo
dashboard con 4 KPIs, ranking con semáforo y hallazgos top; después una
página de detalle por camión con hallazgos.
"""
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from liquidation_tracker.reports import _new_pdf, _pdf_table, _safe  # noqa: E402

# Paleta (ui-ux-pro-max: Data-Dense Dashboard, azul + ámbar)
PRIMARY = (30, 64, 175)      # #1E40AF
SECONDARY = (59, 130, 246)   # #3B82F6
ACCENT = (245, 158, 11)      # #F59E0B
BG = (248, 250, 252)         # #F8FAFC
TEXT = (30, 58, 138)         # #1E3A8A
GREEN = (22, 163, 74)        # interés alto
AMBER = (245, 158, 11)
GREY = (148, 163, 184)

OUT = "data/reports/pdf/estudio_camiones_propios.pdf"


def interest(t):
    return (
        t["valor_oculto_seguro"]
        + 0.3 * t["valor_oculto_dudoso"]
        + 150 * sum(p["faltan"] for p in t["pallets_cajas_faltan"])
        + 80 * len(t["cajas_sospechosas"])
    )


def kpi_card(pdf, family, x, y, w, h, value, label, color):
    pdf.set_fill_color(*color)
    pdf.rect(x, y, w, h, style="F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_xy(x, y + 4)
    pdf.set_font(family, "B", 22)
    pdf.cell(w, 10, _safe(value, family), align="C")
    pdf.set_xy(x, y + 15)
    pdf.set_font(family, "", 8.5)
    pdf.cell(w, 5, _safe(label, family), align="C")
    pdf.set_text_color(*TEXT)


def main():
    with open("data/estudio_nuestros.json", encoding="utf-8") as fh:
        trucks = json.load(fh)
    trucks.sort(key=interest, reverse=True)

    total_oculto = sum(t["valor_oculto_seguro"] for t in trucks)
    total_dudoso = sum(t["valor_oculto_dudoso"] for t in trucks)
    total_faltan = sum(p["faltan"] for t in trucks for p in t["pallets_cajas_faltan"])
    total_vacias = sum(len(t["cajas_sospechosas"]) for t in trucks)
    total_tv = sum(t["tv_eur"] for t in trucks)
    total_retail = sum(t["retail"] for t in trucks)

    pdf, family = _new_pdf()
    pdf.add_page()

    # Cabecera
    pdf.set_fill_color(*PRIMARY)
    pdf.rect(0, 0, 210, 26, style="F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_font(family, "B", 17)
    pdf.set_xy(10, 7)
    pdf.cell(0, 8, _safe("Estudio de camiones propios — regalados y cajas rarunas", family))
    pdf.set_font(family, "", 9)
    pdf.set_xy(10, 16)
    pdf.cell(0, 5, _safe(
        f"{len(trucks)} camiones · retail declarado {total_retail:,.0f} EUR · "
        f"{datetime.now():%d/%m/%Y}", family))
    pdf.set_text_color(*TEXT)

    # KPIs
    y, w, h, gap = 32, 45, 24, 3
    kpi_card(pdf, family, 10, y, w, h,
             f"{total_oculto:,.0f} EUR", "valor regalado SEGURO (min.)", GREEN)
    kpi_card(pdf, family, 10 + (w + gap), y, w, h,
             str(total_faltan), "cajas enteras sin declarar", ACCENT)
    kpi_card(pdf, family, 10 + 2 * (w + gap), y, w, h,
             str(total_vacias), "cajas demasiado vacías", SECONDARY)
    kpi_card(pdf, family, 10 + 3 * (w + gap), y, w, h,
             f"{total_tv / 1000:,.0f}k EUR", "pérdida por TVs", GREY)

    pdf.set_xy(10, y + h + 3)
    pdf.set_font(family, "", 8)
    pdf.set_text_color(100, 116, 139)
    pdf.multi_cell(0, 4, _safe(
        f"+ {total_dudoso:,.0f} EUR adicionales en regalados dudosos (verificar con el "
        "enlace Amazon). Los estimados son mínimos conservadores: el valor real "
        "suele ser bastante mayor.", family), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*TEXT)
    pdf.ln(3)

    # Ranking con semáforo
    pdf.set_font(family, "B", 12)
    pdf.cell(0, 7, _safe("Ranking por interés (todos los camiones)", family),
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(family, "B", 8)
    headers = ["", "Camión", "Retail EUR", "Regalado seg.", "Dudoso",
               "Cajas faltan", "Vacías", "TVs EUR"]
    widths = [7, 25, 27, 27, 22, 24, 18, 24]
    for hd, wd in zip(headers, widths):
        pdf.cell(wd, 6, _safe(hd, family), border=1)
    pdf.ln()
    pdf.set_font(family, "", 8)
    for t in trucks:
        score = interest(t)
        color = GREEN if score >= 1000 else (AMBER if score >= 300 else GREY)
        faltan = sum(p["faltan"] for p in t["pallets_cajas_faltan"])
        pdf.set_fill_color(*color)
        pdf.cell(7, 6, "", border=1, fill=True)
        for value, wd in zip(
            [t["id"], f"{t['retail']:,.0f}", f"{t['valor_oculto_seguro']:,.0f}",
             f"{t['valor_oculto_dudoso']:,.0f}", str(faltan),
             str(len(t["cajas_sospechosas"])), f"{t['tv_eur']:,.0f}"],
            widths[1:],
        ):
            pdf.cell(wd, 6, _safe(str(value), family), border=1)
        pdf.ln()
    pdf.set_font(family, "", 7.5)
    pdf.set_text_color(100, 116, 139)
    pdf.cell(0, 5, _safe(
        "Verde: mucho valor sin declarar detectado · Ámbar: algo · Gris: poco/nada",
        family), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*TEXT)

    # Detalle de los camiones con hallazgos
    for t in trucks:
        if interest(t) < 300:
            continue
        pdf.add_page()
        pdf.set_fill_color(*SECONDARY)
        pdf.rect(0, 0, 210, 16, style="F")
        pdf.set_text_color(255, 255, 255)
        pdf.set_font(family, "B", 13)
        pdf.set_xy(10, 4)
        pdf.cell(0, 8, _safe(
            f"{t['id']} — retail {t['retail']:,.0f} EUR · {t['unidades']} uds", family))
        pdf.set_text_color(*TEXT)
        pdf.set_y(22)

        if t["regalados_seguros"]:
            pdf.set_font(family, "B", 11)
            pdf.cell(0, 7, _safe(
                f"Regalados seguros ({t['valor_oculto_seguro']:,.0f} EUR ocultos mín.)",
                family), new_x="LMARGIN", new_y="NEXT")
            _pdf_table(
                pdf, family,
                ["Artículo", "Declarado", "Est. mín.", "Caja", "ASIN"],
                [[g["desc"], f"{g['declarado']:,.2f}", f"{g['estimado']:,.0f}",
                  g["caja"] or "?", g["asin"] or "-"]
                 for g in t["regalados_seguros"]],
                [88, 20, 18, 28, 28],
                links=[f"https://www.amazon.es/dp/{g['asin']}" if g["asin"] else None
                       for g in t["regalados_seguros"]],
                link_col=4,
            )
        if t["pallets_cajas_faltan"]:
            pdf.set_font(family, "B", 11)
            pdf.cell(0, 7, _safe("Pallets de cajas con cajas sin declarar (regaladas)",
                                 family), new_x="LMARGIN", new_y="NEXT")
            _pdf_table(
                pdf, family,
                ["Pallet", "Cajas declaradas", "Faltan"],
                [[p["id"], f"{p['declaradas']} de 6", str(p["faltan"])]
                 for p in t["pallets_cajas_faltan"]],
                [40, 40, 25],
            )
        if t["cajas_sospechosas"]:
            pdf.set_font(family, "B", 11)
            pdf.cell(0, 7, _safe("Cajas demasiado vacías", family),
                     new_x="LMARGIN", new_y="NEXT")
            _pdf_table(
                pdf, family,
                ["Caja", "Objetos", "Peso kg", "Motivo"],
                [[c["id"], str(c["objetos"]), f"{c['peso']:,.0f}", c["motivo"]]
                 for c in t["cajas_sospechosas"][:12]],
                [28, 14, 16, 130],
            )
        if t["tv_eur"]:
            pdf.set_font(family, "", 9)
            pdf.cell(0, 6, _safe(
                f"TVs: {t['tv_uds']} uds = {t['tv_eur']:,.0f} EUR de pérdida asumida.",
                family), new_x="LMARGIN", new_y="NEXT")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    pdf.output(OUT)
    print("OK ->", OUT)


if __name__ == "__main__":
    main()
