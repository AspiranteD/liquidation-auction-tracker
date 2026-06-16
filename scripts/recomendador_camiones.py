# -*- coding: utf-8 -*-
"""Recomendador de camiones B-Stock = estudio de ventas (recuperación por categoría)
+ subastas activas + calculadora de coste aterrizado existente.

Para cada subasta activa estima, a partir del histórico real de Reusalia:
  - recuperación esperada (ingresos / retail B-Stock) según su mezcla de categorías
  - sell-through y rotación esperadas
y calcula la PUJA MÁXIMA (con IVA, fee, recargo y transporte) para un múltiplo de caja objetivo.
Solo lectura sobre la BD.
"""
import os, sys, json
import numpy as np, pandas as pd, psycopg2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from liquidation_tracker.calculator import BidCalculator  # reutiliza el coste aterrizado

ENV = r"C:\Users\guill\CursorProjects\_ARCHIVADO_reusalia-backend_usar_carpeta_Claude\.env"
MULT_SAFE = 3.0     # múltiplo de caja objetivo (coste aterrizado = recuperación / MULT)
url=[l.split("=",1)[1].strip().strip('"').strip("'") for l in open(ENV,encoding="utf-8") if l.startswith("DATABASE_URL=")][0]
conn=psycopg2.connect(url); conn.set_session(readonly=True,autocommit=True)

# ---------- 1) BASELINES: recuperación / sell-through / rotación por categoría y departamento ----------
base=pd.read_sql("""
SELECT p.lpn,p.id_a2z,p.amazon_category cat,p.amazon_department dept,p.purchase_price pp,
       p.purchase_date,s.revenue,s.first_sale
FROM physical_item p
LEFT JOIN (SELECT lpn,SUM(final_price) revenue,MIN(sale_date) first_sale FROM sale GROUP BY lpn) s ON s.lpn=p.lpn
""",conn)
trucks=pd.read_sql("SELECT id,valor_bstock FROM truckloads",conn); conn.close()
base["pp"]=pd.to_numeric(base["pp"],errors="coerce"); base["revenue"]=pd.to_numeric(base["revenue"],errors="coerce")
base["sold"]=base["revenue"].notna()
base["days"]=(pd.to_datetime(base["first_sale"])-pd.to_datetime(base["purchase_date"])).dt.days
base["held"]=(pd.Timestamp("2026-06-12")-pd.to_datetime(base["purchase_date"])).dt.days
# retail B-Stock estimado por ítem = valor_bstock del camión repartido por peso de purchase_price
vb=trucks.set_index("id")["valor_bstock"]; sumpp=base.groupby("id_a2z")["pp"].sum()
base["est_retail"]=base["id_a2z"].map(vb)*base["pp"]/base["id_a2z"].map(sumpp)

def baseline(key):
    g=base[base["est_retail"]>0].groupby(key)
    rec=g.apply(lambda d: d["revenue"].fillna(0).sum()/d["est_retail"].sum()).rename("recovery")
    n=g.size().rename("n_items")
    st=base[base["held"]>365].groupby(key)["sold"].mean().rename("sell_through")
    rot=base[base["sold"]].groupby(key)["days"].median().rename("rotation")
    return pd.concat([rec,n,st,rot],axis=1)

bcat=baseline("cat"); bdept=baseline("dept")
GLOBAL_REC=base.loc[base["est_retail"]>0,"revenue"].fillna(0).sum()/base.loc[base["est_retail"]>0,"est_retail"].sum()
print(f"Recuperación global (ingresos/retail B-Stock) = {GLOBAL_REC*100:.1f}%  [referencia: media real por camión ~27,8%]")

# diccionario de alias B-Stock -> categoría/depto de mi estudio
ALIAS={"hot beverage makers":"Hot Beverage Makers","floorcare":"Floorcare","housewares":"Housewares",
 "office supplies":"Office Supplies","printing hardware":"Printing Hardware","power tools":"Power Tools",
 "headphones":"Headphones","wireless":"Wireless Phones","camera":"Cameras","games":"Games & Puzzles",
 "lighting":"Lighting","cookware":"Cookware","kitchen":"Cookware","car seats":"Car Seats & Accessories",
 "furniture":"Furniture","bedding":"Bedding","toys":"Toys","personal care":"Shaving & Hair Removal Appliances",
 "beauty":"Hair Care Appliances","auto goods":"Spare & Repair Parts Car & Truck","sporting goods":"Exercise & Fitness",
 "home improvement":"Hardware","lawn and garden":"Gardening Equipment & Storage","pet products":"Pet Supplies",
 "learning & exploration":"Learning & Exploration","plumbing":"Plumbing and Bath"}
catset={c.lower():c for c in bcat.index if isinstance(c,str)}
deptset={c.lower():c for c in bdept.index if isinstance(c,str)}

def lookup(token):
    t=token.strip().lower()
    if not t: return None
    if t in catset: return ("cat",catset[t],"exacta")
    if t in deptset: return ("dept",deptset[t],"depto")
    if t in ALIAS:
        a=ALIAS[t]
        if a in bcat.index: return ("cat",a,"alias")
        if a in bdept.index: return ("dept",a,"alias")
    for k,c in catset.items():        # substring suelto
        if t in k or k in t: return ("cat",c,"aprox")
    return None

def metrics_for(token):
    r=lookup(token)
    if r is None: return None
    src,name,how=r; row=(bcat if src=="cat" else bdept).loc[name]
    return dict(recovery=row["recovery"],sell_through=row["sell_through"],rotation=row["rotation"],match=f"{name}({how})")

# ---------- 2) SUBASTAS ACTIVAS + SCORING ----------
conn=psycopg2.connect(url); conn.set_session(readonly=True,autocommit=True)
auc=pd.read_sql("""SELECT auction_id,title,truck_category,country,lot_type,retail_value,pieces,
                          current_bid,end_time FROM bstock_auction WHERE status='active'""",conn); conn.close()
for c in ["retail_value","current_bid"]: auc[c]=pd.to_numeric(auc[c],errors="coerce")
calc=BidCalculator()

rows=[]
for _,a in auc.iterrows():
    toks=[t for t in str(a["truck_category"] or "").split(",") if t.strip()]
    mets=[m for m in (metrics_for(t) for t in toks) if m]
    if mets:
        rec=np.mean([m["recovery"] for m in mets])
        st=np.nanmean([m["sell_through"] for m in mets if m["sell_through"]==m["sell_through"]]) if any(m["sell_through"]==m["sell_through"] for m in mets) else np.nan
        rot=np.nanmean([m["rotation"] for m in mets if m["rotation"]==m["rotation"]]) if any(m["rotation"]==m["rotation"] for m in mets) else np.nan
        match="; ".join(m["match"] for m in mets); quality="datos"
    else:
        rec,st,rot,match,quality=GLOBAL_REC,np.nan,np.nan,"(global)","global"
    retail=a["retail_value"] or 0
    exp_rev=retail*rec
    lot_key=f'{a["lot_type"]} {a["country"]}' if a["lot_type"]=="4 Pallets" else a["lot_type"]
    cb=calc.max_bid_for_retail_pct(retail, rec/MULT_SAFE, lot_key)   # coste aterrizado = recuperación/3
    max_bid=cb.bid
    headroom=max_bid-(a["current_bid"] or 0)
    rows.append(dict(auction_id=a["auction_id"],titulo=str(a["title"])[:46],cat=str(a["truck_category"])[:34],
        pais=a["country"],retail=retail,piezas=a["pieces"],puja_actual=a["current_bid"] or 0,
        recuperacion=rec*100,ingreso_esperado=exp_rev,puja_max=max_bid,margen_para_pujar=headroom,
        sell_through=(st*100 if st==st else np.nan),rotacion_d=rot,calidad=quality,match=match))
R=pd.DataFrame(rows)
R.to_csv(os.path.join(os.path.dirname(__file__),"..","recomendador_camiones.csv"),index=False)

# ---------- 3) SALIDA ----------
actionable=R[(R["puja_actual"]<=R["puja_max"])&(R["calidad"]=="datos")].sort_values("recuperacion",ascending=False)
pd.set_option("display.width",260); pd.set_option("display.max_columns",20); pd.set_option("display.float_format",lambda x:f"{x:,.0f}")
print(f"\n{len(auc)} subastas activas | con datos de categoría: {(R['calidad']=='datos').sum()} | global: {(R['calidad']=='global').sum()}")
print("\n===== TOP 12 OPORTUNIDADES (mejor recuperación, con margen para pujar) =====")
cols=["titulo","pais","retail","puja_actual","recuperacion","ingreso_esperado","puja_max","margen_para_pujar","sell_through","rotacion_d"]
print(actionable.head(12)[cols].to_string(index=False))
print("\n===== TOP 8 por BENEFICIO ESPERADO ABSOLUTO (ingreso esperado − coste a puja máx) =====")
R["beneficio_esp"]=R["ingreso_esperado"]*(1-1/MULT_SAFE)
print(R[R["calidad"]=="datos"].sort_values("beneficio_esp",ascending=False).head(8)[
    ["titulo","pais","retail","recuperacion","ingreso_esperado","puja_max","beneficio_esp"]].to_string(index=False))
print("\n===== 6 a EVITAR (peor recuperación) =====")
print(R[R["calidad"]=="datos"].sort_values("recuperacion").head(6)[["titulo","cat","retail","recuperacion","puja_max"]].to_string(index=False))
print("\nCSV completo -> recomendador_camiones.csv")
