#!/usr/bin/env python3
"""Nearest public restroom + drinking fountain per facility -> data/amenities.json.
Sources: Public Restrooms (i7jb-7jku), NYC Parks Drinking Fountains (qnv7-p7a2)."""
import json, math, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REST_LIMIT, FOUNT_LIMIT = 2000, 5000

def load(url, limit=None, what=""):
    with urllib.request.urlopen(url, timeout=30) as r: rows = json.load(r)
    # Socrata truncates silently at $limit; without $order the retained subset
    # is also arbitrary. Fail loudly rather than quietly using a biased sample.
    if limit is not None and len(rows) >= limit:
        raise SystemExit(f"{what}: hit $limit={limit}; results truncated - paginate before trusting this")
    return rows

def _now():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

def hav(a, b):
    R=6371000; p1,l1=map(math.radians,a); p2,l2=map(math.radians,b)
    h=math.sin((p2-p1)/2)**2+math.cos(p1)*math.cos(p2)*math.sin((l2-l1)/2)**2
    return 2*R*math.asin(math.sqrt(h))

def fountain_usable(x):
    """Mirror the restroom Operational filter where the dataset exposes status."""
    st = (x.get("status") or x.get("fountain_status") or "").strip().lower()
    return st in ("", "operational", "active", "in service")

def main():
    rest = load("https://data.cityofnewyork.us/resource/i7jb-7jku.json"
                f"?$where=status='Operational'&$order=:id&$limit={REST_LIMIT}", REST_LIMIT, "restrooms")
    # Fountains were previously unfiltered while restrooms were filtered to
    # Operational on the line above - a broken or winterized fountain was
    # reported to the user as available water.
    fount = load("https://data.cityofnewyork.us/resource/qnv7-p7a2.json"
                 f"?$order=:id&$limit={FOUNT_LIMIT}", FOUNT_LIMIT, "fountains")
    # Guard BOTH coords: the latitude-only guard let a null longitude raise KeyError.
    rest=[(float(x["latitude"]),float(x["longitude"]),x.get("facility_name") or "Restroom")
          for x in rest if x.get("latitude") and x.get("longitude")]
    fp=[]
    for x in fount:
        if not fountain_usable(x): continue
        g=(x.get("geolocation") or x.get("the_geom") or {})
        # No name field in this dataset actually carries a usable label - the old
        # `park_name or location` chain never once resolved (all 98 committed
        # fountains read the literal fallback). Don't pretend to extract one.
        if g.get("coordinates"): fp.append((g["coordinates"][1],g["coordinates"][0],"Drinking fountain"))
    courts=json.load(open(ROOT / "data/courts.geojson"))
    out={}
    for f in courts["features"]:
        lon,lat=f["geometry"]["coordinates"]; pid=f["properties"]["park_id"]
        def nearest(pool):
            if not pool: return None
            p=min(pool,key=lambda q:hav((lat,lon),q[:2]))
            d=hav((lat,lon),p[:2])
            return {"name":p[2],"dist_m":round(d)} if d<=800 else None
        out[pid]={"restroom":nearest(rest),"fountain":nearest(fp)}
    out["_generated_at"] = _now()   # "_"-prefixed: compute_scores does amenities.get(park_id)
    with open(ROOT / "data/amenities.json","w") as f: json.dump(out,f,indent=1)
    got=sum(1 for v in out.values() if v["restroom"] or v["fountain"])
    print(f"amenities for {got}/{len(out)} facilities (restrooms {len(rest)}, fountains {len(fp)})")

if __name__ == "__main__":
    main()
