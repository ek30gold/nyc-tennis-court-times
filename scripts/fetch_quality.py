#!/usr/bin/env python3
"""Park upkeep hint from Parks inspection ratings (yg3y-7juh) -> data/quality.json.
Uses PIP inspections from 2024 on; needs >=3 inspections for a label."""
import json, urllib.request, urllib.parse, collections, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIMIT = 50000

def main():
    q = ("?$select=prop_id,overall_condition&$where=" + urllib.parse.quote(
        "inspection_year >= '2024' AND overall_condition in('A','U')")
        # Without $order an over-cap result set is truncated AND nondeterministic.
        + "&$order=:id" + f"&$limit={LIMIT}")
    with urllib.request.urlopen(
        "https://data.cityofnewyork.us/resource/yg3y-7juh.json" + q, timeout=60) as r:
        rows = json.load(r)
    if len(rows) >= LIMIT:
        raise SystemExit(f"quality: hit $limit={LIMIT}; ratios would come from a "
                         f"biased sample - paginate before trusting this")
    agg = collections.defaultdict(lambda: [0, 0])
    skipped = 0
    for row in rows:
        # Socrata omits null fields entirely, so [] would KeyError and kill the
        # whole hourly chain on a single bad row.
        pid, cond = row.get("prop_id"), row.get("overall_condition")
        if not pid or not cond:
            skipped += 1; continue
        agg[pid.split("-")[0]][0 if cond == "A" else 1] += 1
    keep = {f["properties"]["park_id"] for f in json.load(open(ROOT / "data/courts.geojson"))["features"]}
    out = {}
    for park, (a, u) in agg.items():
        if park not in keep: continue
        n = a + u
        if n < 3: continue
        pct = a / n
        label = "strong" if pct >= 0.9 else "mixed" if pct >= 0.7 else "poor"
        out[park] = {"label": label, "pct_acceptable": round(pct * 100), "inspections": n}
    out["_generated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with open(ROOT / "data/quality.json", "w") as f:
        json.dump(out, f, indent=1)
    print(f"quality labels for {len(out)-1} mapped facilities "
          f"(from {len(rows)} inspections, 2024+; {skipped} rows skipped for null fields)")

if __name__ == "__main__":
    main()
