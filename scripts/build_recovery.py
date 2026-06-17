# -*- coding: utf-8 -*-
"""Persiste el macro-estudio de recuperación de Reusalia a data/recovery.json.

La recuperación = ingresos reales (sum final_price de ventas) / retail B-Stock
estimado del ítem. Es la base para recomendar la puja: no usamos reglas fijas
(12%/15%), sino cuánto recupera de verdad cada departamento/categoría.

Se ejecuta UNA vez (o periódicamente) contra la BD; el monitor luego lee el
JSON sin necesidad de levantar el backend ni la BD.

Uso:
    python scripts/build_recovery.py
    python scripts/build_recovery.py --env "C:\\ruta\\.env" --out data/recovery.json
"""
import argparse
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd
import psycopg2

DEFAULT_ENV = r"C:\Users\guill\CursorProjects\_ARCHIVADO_reusalia-backend_usar_carpeta_Claude\.env"
DEFAULT_OUT = "data/recovery.json"
# Por debajo de esta muestra, la recuperación del grupo no es fiable: el lector
# cae al valor global en su lugar.
MIN_SAMPLE = 30


def _db_url(env_path: str) -> str:
    with open(env_path, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(f"DATABASE_URL no encontrada en {env_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Construye data/recovery.json")
    parser.add_argument("--env", default=DEFAULT_ENV)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--min-sample", type=int, default=MIN_SAMPLE)
    args = parser.parse_args()

    if not os.path.exists(args.env):
        print(f"ENV no existe: {args.env}", file=sys.stderr)
        return 2

    url = _db_url(args.env)
    conn = psycopg2.connect(url)
    conn.set_session(readonly=True, autocommit=True)
    base = pd.read_sql(
        """
        SELECT p.lpn, p.id_a2z, p.amazon_category cat, p.amazon_department dept,
               p.purchase_price pp,
               s.revenue
        FROM physical_item p
        LEFT JOIN (
            SELECT lpn, SUM(final_price) revenue FROM sale GROUP BY lpn
        ) s ON s.lpn = p.lpn
        """,
        conn,
    )
    trucks = pd.read_sql("SELECT id, valor_bstock FROM truckloads", conn)
    conn.close()

    base["pp"] = pd.to_numeric(base["pp"], errors="coerce")
    base["revenue"] = pd.to_numeric(base["revenue"], errors="coerce")
    # retail B-Stock estimado por ítem: valor_bstock del camión repartido por
    # peso del purchase_price (mismo método que el recomendador).
    vb = trucks.set_index("id")["valor_bstock"]
    sumpp = base.groupby("id_a2z")["pp"].sum()
    base["est_retail"] = base["id_a2z"].map(vb) * base["pp"] / base["id_a2z"].map(sumpp)
    valid = base[base["est_retail"] > 0].copy()

    def recovery_by(key: str) -> dict:
        out = {}
        for name, g in valid.groupby(key):
            if not isinstance(name, str) or not name.strip():
                continue
            retail = g["est_retail"].sum()
            if retail <= 0:
                continue
            rec = float(g["revenue"].fillna(0).sum() / retail)
            out[name.strip()] = {"recovery": round(rec, 4), "n": int(len(g))}
        return out

    global_rec = float(
        valid["revenue"].fillna(0).sum() / valid["est_retail"].sum()
    )

    payload = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "min_sample": args.min_sample,
        "global": round(global_rec, 4),
        "by_department": recovery_by("dept"),
        "by_category": recovery_by("cat"),
    }

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False)

    print(f"OK recuperación global {global_rec*100:.1f}% -> {args.out}")
    print(
        f"  {len(payload['by_department'])} departamentos, "
        f"{len(payload['by_category'])} categorías"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
