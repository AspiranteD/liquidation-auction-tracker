"""Build per-department box baselines from a corpus of manifest CSVs.

Amazon boxes carry a fairly stable number of items per DEPARTMENT (Motor
boxes ~20-40 items, Electronics fewer...). This script scans every manifest
in the given folders, extracts the real boxes (multi-box pallets), assigns
each box its dominant department and writes percentile statistics to
data/baselines.json so the empty-box alarm can be category-aware.

Usage:
    python scripts/build_baselines.py "folder1" ["folder2" ...]
"""
import json
import os
import statistics
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from liquidation_tracker import analyzer, insights  # noqa: E402

OUT_PATH = "data/baselines.json"


def is_csv(path: str) -> bool:
    """Some corpus files are saved Cloudflare HTML pages, not CSV."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(200)
        return b"<!DOCTYPE" not in head and b"<html" not in head and b"," in head
    except OSError:
        return False


def main(folders):
    units_by_dept = defaultdict(list)
    weight_by_dept = defaultdict(list)
    box_counts = Counter()  # boxes per box-pallet across the corpus
    scanned = skipped = 0

    for folder in folders:
        for name in sorted(os.listdir(folder)):
            if not name.lower().endswith(".csv"):
                continue
            path = os.path.join(folder, name)
            if not is_csv(path):
                skipped += 1
                continue
            try:
                items = analyzer.parse_manifest(path)
            except Exception:  # noqa: BLE001 - corpus has junk files
                skipped += 1
                continue
            if not items:
                skipped += 1
                continue
            boxes, pallets = insights.analyze_containers(items)
            for pallet in pallets:
                if pallet.pallet_type == "cajas" and pallet.box_count >= 2:
                    box_counts[pallet.box_count] += 1
            for box in boxes:
                dept_units = Counter()
                for item in box.items:
                    dept_units[item.department or "?"] += item.qty
                dept = dept_units.most_common(1)[0][0]
                units_by_dept[dept].append(box.units)
                if box.weight_kg:
                    weight_by_dept[dept].append(box.weight_kg)
            scanned += 1
            if scanned % 250 == 0:
                print(f"  {scanned} manifiestos procesados...", flush=True)

    def stats(values):
        if len(values) < 8:
            return None
        qs = statistics.quantiles(values, n=20)  # 5% steps
        return {
            "n": len(values),
            "p10": round(qs[1], 1),
            "p25": round(qs[4], 1),
            "p50": round(statistics.median(values), 1),
            "p75": round(qs[14], 1),
        }

    baselines = {}
    all_units, all_weights = [], []
    for dept, values in units_by_dept.items():
        entry = stats(values)
        if entry:
            weights = stats(weight_by_dept.get(dept, []))
            entry["weight"] = weights
            baselines[dept] = entry
        all_units.extend(values)
        all_weights.extend(weight_by_dept.get(dept, []))
    baselines["_global"] = stats(all_units)
    baselines["_global"]["weight"] = stats(all_weights)
    baselines["_box_counts"] = dict(sorted(box_counts.items()))

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(baselines, fh, indent=2, ensure_ascii=False)
    print(f"OK: {scanned} manifiestos, {skipped} descartados, "
          f"{len(baselines) - 2} departamentos con baseline -> {OUT_PATH}")
    print("Distribucion cajas/pallet:", dict(sorted(box_counts.items())))


if __name__ == "__main__":
    main(sys.argv[1:] or ["data/manifests"])
