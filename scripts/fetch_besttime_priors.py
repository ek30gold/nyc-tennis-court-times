#!/usr/bin/env python3
"""BestTime.app demand-curve priors.

Reads cached BestTime forecast responses from data/besttime/*.json and merges
hourly demand curves into data/priors.json under "besttime_curves", keyed by
our park_id. This script is OFFLINE: it only merges what is already cached in
data/besttime/. There is no API refresh path (an earlier docstring promised one
for BESTTIME_KEY; no such code ever existed). To refresh a venue, re-fetch its
JSON into data/besttime/ by hand, then run this.

Verified 2026-09-13 (live API, agent-operated free account):
- Covered: Central Park Tennis Center, McCarren Park Tennis Courts, Crotona
  Park Tennis Center, Astoria Park Tennis Courts, USTA Billie Jean King NTC,
  Hudson River Park Tennis Courts, Inwood Hill Park Tennis Courts.
- Found but NOT forecastable (insufficient visitor volume): Riverside Park
  Tennis (2 name variants), Fort Greene, Prospect Park Tennis Center,
  Sportime Randall's Island, Sutton East, Alley Pond, Mill Pond, Cunningham,
  East River, Van Cortlandt, Crocheron, Shore Park, Alley Athletic, Pelham
  Bay, Kaiser, Brookville. Lincoln Terrace: venue not found.
- Second pass 2026-09-13 added: Highland Park, Kissena Park, Fort Washington
  Park, Manhattan Beach, Marine Park (5 hits / 12 attempts, free credits).
- day_raw is 24 hourly busyness percentages relative to the venue's weekly
  peak; index 0 = 6:00 AM local (validated against venue open hours).
"""
import json, os, glob, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SLUG_TO_PARK = {
    "central_park_tennis_center": "M010",
    "mccarren_park_tennis": "B058",
    "crotona_park_tennis": "X010",
    "astoria_park_tennis": "Q004",
    # "usta_ntc" is deliberately UNMAPPED. The USTA Billie Jean King National
    # Tennis Center is a paid private facility that merely shares a park
    # boundary with Flushing Meadows Corona Park's free public courts (Q099).
    # Its tournament/lesson traffic says nothing about public walk-up demand,
    # and compute_scores prefers a curve over the baseline - so mapping it to
    # Q099 gave the public courts the wrong venue's entire score shape.
    "hudson_river_park_tennis": "SUPP-HRP",  # concession-run; added via data/supplement.geojson
    "inwood_hill_tennis": "M042",
    "highland_park_tennis": "B047",
    "kissena_park_tennis": "Q024",
    "fort_washington_tennis": "M028",
    "manhattan_beach_tennis": "B251",
    "marine_park_tennis": "B057",
}

def curve_from(path):
    """day_raw runs 06:00 -> 05:00 the NEXT day, so indices 18-23 are hours
    0-5 of day_int+1 and must be filed there, not under day_int.

    Zero-busyness hours are NOT stored. BestTime reports 0 for hours outside a
    venue's open window (Central Park's curve is non-zero only at 06-18), so a
    stored 0 does not mean "measured and quiet" - it means "venue closed or
    unmeasured". compute_scores.curve_value() treats a MISSING hour as "no
    data" and falls back to baseline(); storing the zeros instead would send
    19:00 back down the curve path and score the city's busiest facility 0.00
    -> "walk-on", which is the exact bug that fallback exists to fix."""
    d = json.load(open(path))
    curve = {}
    for day in d["analysis"]:
        di = day["day_info"]["day_int"]  # 0=Monday .. 6=Sunday
        for i, v in enumerate(day["day_raw"]):
            if not v: continue
            hour, dayshift = (6 + i) % 24, (6 + i) // 24
            curve.setdefault((di + dayshift) % 7, {})[hour] = v
    return {"venue_name": d["venue_info"]["venue_name"],
            "venue_id": d["venue_info"]["venue_id"],
            "reviews": d["venue_info"].get("reviews"),
            # Provenance must describe the DATA, not this run. Stamping
            # date.today() onto a cached file fabricates freshness: re-running
            # in December relabelled September curves as December.
            "fetched": datetime.date.fromtimestamp(os.path.getmtime(path)).isoformat(),
            "curves": curve}

def main():
    priors_path = ROOT / "data/priors.json"
    priors = json.load(open(priors_path))
    existing = priors.get("besttime_curves") or {}
    curves, skipped = {}, []
    for path in sorted(glob.glob(str(ROOT / "data/besttime/*.json"))):
        slug = os.path.basename(path)[:-5]
        park_id = SLUG_TO_PARK.get(slug)
        if not park_id:
            skipped.append(slug); continue
        curves[park_id] = curve_from(path)
    if not curves:
        raise SystemExit("besttime: no curves parsed - refusing to write "
                         "(priors.json is hand-curated and cannot be rebuilt)")
    if len(curves) < len(existing):
        # A missing cache file or an unmapped slug used to silently erase a
        # venue, because this assigned the whole key instead of merging.
        lost = sorted(set(existing) - set(curves))
        raise SystemExit(f"besttime: would drop {lost} vs the committed priors - "
                         f"refusing to write. Restore the cache file, or remove "
                         f"the entry deliberately.")
    merged = dict(existing); merged.update(curves)
    priors["besttime_curves"] = merged
    newest = max((c.get("fetched") or "") for c in merged.values()) or "unknown"
    priors["_besttime_note"] = ("Hourly busyness 0-100 vs venue weekly peak, by day "
        "(0=Mon) and hour. Source: BestTime.app free tier; per-venue fetch dates are "
        f"on each entry (newest {newest}), derived from the cache file mtime. "
        "Treat as demand prior, not wait measurement.")
    with open(priors_path, "w") as f:
        json.dump(priors, f, indent=1)
    print(f"merged {len(curves)} venue curves for parks: {sorted(curves)}")
    if skipped: print(f"skipped (no park_id mapping): {skipped}")

if __name__ == "__main__":
    main()
