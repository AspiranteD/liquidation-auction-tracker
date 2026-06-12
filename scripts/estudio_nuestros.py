"""Estudio agregado de los camiones propios (carpeta MEGA + BBDD).

Analiza cada manifiesto con la lógica completa y vuelca un JSON con el
resumen por camión y los hallazgos (regalados, cajas/pallets sospechosos,
TVs) listo para renderizar el PDF del estudio.
"""
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from liquidation_tracker import analyzer, insights  # noqa: E402

OUT = "data/estudio_nuestros.json"
FOLDERS = [
    r"C:\Users\guill\Documents\MEGA\Historico camiones\Nuestros manifest",
    "data/nuestros",
]


def main():
    seen = set()
    trucks = []
    for folder in FOLDERS:
        for path in sorted(glob.glob(os.path.join(folder, "*.csv"))):
            name = os.path.basename(path)
            if not re.match(r"^A2Z\d+\.csv$", name):
                continue  # excluye Operativa_Camion_* y similares
            truck_id = name[:-4]
            if truck_id in seen:
                continue
            seen.add(truck_id)
            try:
                items = analyzer.parse_manifest(path)
                if not items:
                    continue
                r = insights.deep_analyze(items, label=truck_id)
            except Exception as exc:  # noqa: BLE001
                print(f"{truck_id}: ERROR {exc}", flush=True)
                continue

            trucks.append({
                "id": truck_id,
                "lineas": r.total_lines,
                "unidades": r.total_units,
                "retail": r.total_retail,
                "efectivo": r.effective_retail,
                "tv_uds": r.tv_units,
                "tv_eur": r.tv_loss_retail,
                "regalados_seguros": [
                    {
                        "desc": (g.item.description or "")[:70],
                        "declarado": g.item.unit_retail,
                        "estimado": g.estimated_value,
                        "asin": g.item.asin,
                        "caja": g.item.box_id,
                        "pallet": g.item.pallet_id,
                    }
                    for g in r.giveaways if g.tier == "seguro"
                ],
                "regalados_dudosos": len(
                    [g for g in r.giveaways if g.tier == "dudoso"]
                ),
                "valor_oculto_seguro": r.giveaway_value_sure,
                "valor_oculto_dudoso": r.giveaway_value_doubt,
                "cajas": len(r.boxes),
                "cajas_sospechosas": [
                    {"id": b.container_id, "objetos": b.units,
                     "peso": b.weight_kg, "motivo": b.reason[:140]}
                    for b in r.suspicious_boxes
                ],
                "pallets_cajas_faltan": [
                    {"id": p.pallet_id, "declaradas": p.box_count,
                     "faltan": p.missing_boxes}
                    for p in r.suspicious_pallets
                ],
            })
            print(f"{truck_id}: ok", flush=True)

    trucks.sort(
        key=lambda t: (
            t["valor_oculto_seguro"]
            + t["valor_oculto_dudoso"]
            + 200 * len(t["pallets_cajas_faltan"])
            + 100 * len(t["cajas_sospechosas"])
        ),
        reverse=True,
    )
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(trucks, fh, indent=1, ensure_ascii=False)
    print(f"OK {len(trucks)} camiones -> {OUT}")


if __name__ == "__main__":
    main()
