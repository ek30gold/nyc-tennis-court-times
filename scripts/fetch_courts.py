#!/usr/bin/env python3
"""Fetch NYC tennis courts from NYC Open Data Athletic Facilities (qnem-b8re)
and group per-court records into per-facility GeoJSON.

Verified 2026-09-13: tennis=true returns 624 records; fields include
gispropnum (park ID), borough, surface_type, field_lighted, featurestatus,
multipolygon geometry.
"""
import json, urllib.request

BASE = "https://data.cityofnewyork.us/resource/qnem-b8re.json"
WHERE = "tennis=true AND featurestatus='Active'"
FIELDS = "gispropnum,borough,surface_type,field_lighted,dimensions,zipcode,multipolygon"
LIMIT = 5000

def centroid(coords):
    # coords: MultiPolygon -> flatten to (lon, lat) pairs
    pts = [p for poly in coords for ring in poly for p in ring]
    lon = sum(p[0] for p in pts) / len(pts)
    lat = sum(p[1] for p in pts) / len(pts)
    return [round(lon, 6), round(lat, 6)]

def park_names():
    url = "https://data.cityofnewyork.us/resource/enfh-gkve.json?$select=gispropnum,name311&$limit=5000"
    with urllib.request.urlopen(url) as r:
        return {row["gispropnum"]: row.get("name311") for row in json.load(r)}

def main():
    names = park_names()
    url = f"{BASE}?$where={urllib.parse.quote(WHERE)}&$select={FIELDS}&$limit={LIMIT}"
    with urllib.request.urlopen(url) as r:
        rows = json.load(r)
    facilities = {}
    for row in rows:
        park = row.get("gispropnum", "UNKNOWN")
        f = facilities.setdefault(park, {
            "borough": row.get("borough"), "courts": 0, "surfaces": set(),
            "lighted": False, "coords": []})
        f["courts"] += 1
        if row.get("surface_type"): f["surfaces"].add(row["surface_type"])
        f["lighted"] = f["lighted"] or row.get("field_lighted", False)
        mp = row.get("multipolygon", {}).get("coordinates")
        if mp: f["coords"].append(centroid(mp))
    features = []
    for park, f in facilities.items():
        if not f["coords"]: continue
        lon = sum(c[0] for c in f["coords"]) / len(f["coords"])
        lat = sum(c[1] for c in f["coords"]) / len(f["coords"])
        features.append({"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lon,6), round(lat,6)]},
            "properties": {"park_id": park, "name": names.get(park, park), "borough": f["borough"],
                "court_count": f["courts"], "surfaces": sorted(f["surfaces"]),
                "lighted": f["lighted"]}})
    out = {"type": "FeatureCollection", "features": features}
    with open("data/courts.geojson", "w") as fh:
        json.dump(out, fh)
    print(f"{len(rows)} court records -> {len(features)} facilities")

if __name__ == "__main__":
    import urllib.parse
    main()
