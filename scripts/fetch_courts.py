#!/usr/bin/env python3
"""Fetch NYC tennis courts from NYC Open Data Athletic Facilities (qnem-b8re)
and group per-court records into per-facility GeoJSON.

Verified 2026-09-13: tennis=true returns 624 records; fields include
gispropnum (park ID), borough, surface_type, field_lighted, featurestatus,
multipolygon geometry.

courts.geojson is the join root for amenities/transit/quality/scores, so this
script refuses to shrink it by more than SHRINK_TOL without an explicit
override (COURTS_ALLOW_SHRINK=1) - a partial Socrata 200 would otherwise
propagate through every derived file in the same run.
"""
import json, os, urllib.parse, urllib.request, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://data.cityofnewyork.us/resource/qnem-b8re.json"
WHERE = "tennis=true AND featurestatus='Active'"
FIELDS = "gispropnum,borough,surface_type,field_lighted,pickleball,multipolygon"
LIMIT = 5000
NAMES_LIMIT = 5000
SHRINK_TOL = 0.10   # refuse a >10% drop in facility count vs the existing file

TRUE_ISH = (True, "true", "True", "TRUE", "yes", "Yes", "Y", "y", 1, "1")

def truthy(v):
    """Socrata booleans arrive as real bools on some columns and strings on
    others; treat both the same way."""
    return v in TRUE_ISH

def fetch(url, limit, what):
    with urllib.request.urlopen(url) as r:
        rows = json.load(r)
    if len(rows) >= limit:
        print(f"WARNING: {what} returned {len(rows)} rows = $limit {limit}; "
              f"result may be truncated - raise the limit")
    return rows

def centroid(coords):
    # coords: MultiPolygon -> flatten to (lon, lat) pairs
    pts = [p for poly in coords for ring in poly for p in ring]
    lon = sum(p[0] for p in pts) / len(pts)
    lat = sum(p[1] for p in pts) / len(pts)
    return [round(lon, 6), round(lat, 6)]

def park_names():
    url = ("https://data.cityofnewyork.us/resource/enfh-gkve.json"
           f"?$select=gispropnum,name311&$order=:id&$limit={NAMES_LIMIT}")
    return {row["gispropnum"]: row.get("name311")
            for row in fetch(url, NAMES_LIMIT, "park names (enfh-gkve)")
            if row.get("gispropnum")}

def existing_feature_count():
    try:
        with open(ROOT / "data" / "courts.geojson") as fh:
            return len(json.load(fh).get("features", []))
    except (FileNotFoundError, ValueError, KeyError):
        return 0

def main():
    names = park_names()
    url = (f"{BASE}?$where={urllib.parse.quote(WHERE)}&$select={FIELDS}"
           f"&$order=:id&$limit={LIMIT}")
    rows = fetch(url, LIMIT, "court records (qnem-b8re)")
    facilities = {}
    no_park_id = 0
    for row in rows:
        park = row.get("gispropnum")
        if not park:
            # never bucket id-less rows under a literal "UNKNOWN" facility: the
            # mean of unrelated centroids can land anywhere (e.g. the harbor)
            no_park_id += 1
            continue
        f = facilities.setdefault(park, {
            "borough": row.get("borough"), "courts": 0, "surfaces": set(),
            "lighted": False, "pickleball": False, "coords": []})
        f["courts"] += 1
        if row.get("surface_type"): f["surfaces"].add(row["surface_type"])
        if truthy(row.get("field_lighted")): f["lighted"] = True
        if truthy(row.get("pickleball")): f["pickleball"] = True
        mp = (row.get("multipolygon") or {}).get("coordinates")
        if mp: f["coords"].append(centroid(mp))
    features = []
    no_geometry = []
    for park, f in facilities.items():
        if not f["coords"]:
            no_geometry.append(park)
            continue
        lon = sum(c[0] for c in f["coords"]) / len(f["coords"])
        lat = sum(c[1] for c in f["coords"]) / len(f["coords"])
        features.append({"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon,6), round(lat,6)]},
            "properties": {"park_id": park,
                # .get(park, park) returns None when the key exists with a null
                # name311, so fall back on falsiness, not on key absence
                "name": names.get(park) or park, "borough": f["borough"],
                "court_count": f["courts"], "surfaces": sorted(f["surfaces"]),
                "lighted": f["lighted"], "pickleball": f["pickleball"]}})
    # merge manually verified concession-run facilities the dataset misses
    try:
        supp = json.load(open(ROOT / "data" / "supplement.geojson"))
        existing = {f["properties"]["park_id"] for f in features}
        added = 0
        for feat in supp["features"]:
            if feat["properties"]["park_id"] not in existing:
                features.append(feat); added += 1
        if added: print(f"merged {added} supplement facilities")
    except FileNotFoundError:
        pass
    if no_park_id:
        print(f"dropped {no_park_id} court records with no gispropnum")
    if no_geometry:
        print(f"dropped {len(no_geometry)} facilities with no geometry: {sorted(no_geometry)}")
    prev = existing_feature_count()
    if prev and len(features) < prev * (1 - SHRINK_TOL):
        msg = (f"REFUSING TO WRITE: facility count fell {prev} -> {len(features)} "
               f"(>{SHRINK_TOL:.0%}). courts.geojson is the join root for "
               f"amenities/transit/quality/scores. Set COURTS_ALLOW_SHRINK=1 to override.")
        if os.environ.get("COURTS_ALLOW_SHRINK") != "1":
            raise SystemExit(msg)
        print("OVERRIDE (COURTS_ALLOW_SHRINK=1): " + msg)
    out = {"type": "FeatureCollection",
           "_generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "features": features}
    with open(ROOT / "data" / "courts.geojson", "w") as fh:
        json.dump(out, fh)
    print(f"{len(rows)} court records -> {len(features)} facilities (was {prev})")

if __name__ == "__main__":
    main()
