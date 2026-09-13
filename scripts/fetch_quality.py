#!/usr/bin/env python3
"""Park upkeep hint from Parks inspection ratings (yg3y-7juh) -> data/quality.json.
Uses PIP inspections from 2024 on; needs >=3 inspections for a label."""
import json, urllib.request, urllib.parse, collections

def main():
    q = ("?$select=prop_id,overall_condition&$where=" + urllib.parse.quote(
        "inspection_year >= '2024' AND overall_condition in('A','U')") + "&$limit=50000")
    with urllib.request.urlopen(
        "https://data.cityofnewyork.us/resource/yg3y-7juh.json" + q) as r:
        rows = json.load(r)
    agg = collections.defaultdict(lambda: [0, 0])
    for row in rows:
        park = row["prop_id"].split("-")[0]
        agg[park][0 if row["overall_condition"] == "A" else 1] += 1
    out = {}
    for park, (a, u) in agg.items():
        n = a + u
        if n < 3: continue
        pct = a / n
        label = "strong" if pct >= 0.9 else "mixed" if pct >= 0.7 else "poor"
        out[park] = {"label": label, "pct_acceptable": round(pct * 100), "inspections": n}
    json.dump(out, open("data/quality.json", "w"), indent=1)
    print(f"quality labels for {len(out)} parks from {len(rows)} inspections (2024+)")

if __name__ == "__main__":
    main()
