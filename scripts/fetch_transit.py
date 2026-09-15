#!/usr/bin/env python3
"""Nearest subway station per facility -> data/transit.json.
Source: MTA static subway GTFS (stops.txt, no key needed).

Station identity is the parent-station ROW, not its stop_name: the feed has
~496 parent stations but only ~379 distinct names ("86 St" x6, "Canal St" x6,
"Fulton St" x5 ...). Keying the candidate pool by name silently discarded 117
stations and gave 14 facilities a wrong/absent nearest station (e.g. Central
Park resolved to "103 St" 692 m instead of "96 St" 288 m), so the pool is a
flat list of rows.
"""
import csv, datetime, io, json, math, urllib.request, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GTFS = "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_subway.zip"
MAX_M = 1000

def hav(a, b):
    R=6371000; p1,l1=map(math.radians,a); p2,l2=map(math.radians,b)
    h=math.sin((p2-p1)/2)**2+math.cos(p1)*math.cos(p2)*math.sin((l2-l1)/2)**2
    return 2*R*math.asin(math.sqrt(h))

def load_stations(blob):
    """Parent stations (location_type=1) as a LIST of (lat, lon, name) rows.
    Never keyed by name - many complexes share one."""
    z = zipfile.ZipFile(io.BytesIO(blob))
    stops = []
    for r in csv.DictReader(io.TextIOWrapper(z.open("stops.txt"))):
        if r.get("location_type") != "1":
            continue
        try:
            stops.append((float(r["stop_lat"]), float(r["stop_lon"]), r["stop_name"]))
        except (TypeError, ValueError, KeyError):
            continue
    return stops

def main():
    stops = load_stations(urllib.request.urlopen(GTFS).read())
    if not stops:
        raise SystemExit("no parent stations parsed from GTFS stops.txt - aborting")
    courts = json.load(open(ROOT / "data" / "courts.geojson"))
    out = {"_generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
    for f in courts["features"]:
        lon, lat = f["geometry"]["coordinates"]
        s = min(stops, key=lambda q: hav((lat, lon), q[:2]))
        d = hav((lat, lon), s[:2])
        if d <= MAX_M:
            out[f["properties"]["park_id"]] = {"station": s[2], "dist_m": round(d)}
    json.dump(out, open(ROOT / "data" / "transit.json", "w"), indent=1)
    names = len({s[2] for s in stops})
    print(f"nearest subway stop for {len(out)-1}/{len(courts['features'])} facilities "
          f"({len(stops)} parent stations, {names} distinct names)")

if __name__ == "__main__":
    main()
