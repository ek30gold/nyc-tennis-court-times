#!/usr/bin/env python3
"""Nearest subway station per facility -> data/transit.json.
Source: MTA static subway GTFS (stops.txt, no key needed)."""
import csv, io, json, math, urllib.request, zipfile

GTFS = "https://rrgtfsfeeds.s3.amazonaws.com/gtfs_subway.zip"

def hav(a, b):
    R=6371000; p1,l1=map(math.radians,a); p2,l2=map(math.radians,b)
    h=math.sin((p2-p1)/2)**2+math.cos(p1)*math.cos(p2)*math.sin((l2-l1)/2)**2
    return 2*R*math.asin(math.sqrt(h))

def main():
    z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(GTFS).read()))
    rows = csv.DictReader(io.TextIOWrapper(z.open("stops.txt")))
    stations = {}
    for r in rows:  # location_type=1 rows are whole station complexes
        if r.get("location_type") == "1":
            stations[r["stop_name"]] = (float(r["stop_lat"]), float(r["stop_lon"]))
    stops = [(lat, lon, name) for name, (lat, lon) in stations.items()]
    courts = json.load(open("data/courts.geojson"))
    out = {}
    for f in courts["features"]:
        lon, lat = f["geometry"]["coordinates"]
        s = min(stops, key=lambda q: hav((lat, lon), q[:2]))
        d = hav((lat, lon), s[:2])
        if d <= 1000:
            out[f["properties"]["park_id"]] = {"station": s[2], "dist_m": round(d)}
    json.dump(out, open("data/transit.json", "w"), indent=1)
    print(f"nearest subway stop for {len(out)}/{len(courts['features'])} facilities ({len(stops)} stations)")

if __name__ == "__main__":
    main()
