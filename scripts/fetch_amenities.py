#!/usr/bin/env python3
"""Nearest public restroom + drinking fountain per facility -> data/amenities.json.
Sources: Public Restrooms (i7jb-7jku), NYC Parks Drinking Fountains (qnv7-p7a2)."""
import json, math, urllib.request

def load(url):
    with urllib.request.urlopen(url) as r: return json.load(r)

def hav(a, b):
    R=6371000; p1,l1=map(math.radians,a); p2,l2=map(math.radians,b)
    h=math.sin((p2-p1)/2)**2+math.cos(p1)*math.cos(p2)*math.sin((l2-l1)/2)**2
    return 2*R*math.asin(math.sqrt(h))

def main():
    rest = load("https://data.cityofnewyork.us/resource/i7jb-7jku.json?$where=status='Operational'&$limit=2000")
    fount = load("https://data.cityofnewyork.us/resource/qnv7-p7a2.json?$limit=5000")
    rest=[(float(x["latitude"]),float(x["longitude"]),x.get("facility_name","Restroom")) for x in rest if x.get("latitude")]
    fp=[]
    for x in fount:
        g=(x.get("geolocation") or x.get("the_geom") or {})
        if g.get("coordinates"): fp.append((g["coordinates"][1],g["coordinates"][0],x.get("park_name") or x.get("location") or "Drinking fountain"))
    courts=json.load(open("data/courts.geojson"))
    out={}
    for f in courts["features"]:
        lon,lat=f["geometry"]["coordinates"]; pid=f["properties"]["park_id"]
        def nearest(pool):
            if not pool: return None
            p=min(pool,key=lambda q:hav((lat,lon),q[:2]))
            d=hav((lat,lon),p[:2])
            return {"name":p[2],"dist_m":round(d)} if d<=800 else None
        out[pid]={"restroom":nearest(rest),"fountain":nearest(fp)}
    json.dump(out,open("data/amenities.json","w"),indent=1)
    got=sum(1 for v in out.values() if v["restroom"] or v["fountain"])
    print(f"amenities for {got}/{len(out)} facilities (restrooms {len(rest)}, fountains {len(fp)})")

if __name__ == "__main__":
    main()
