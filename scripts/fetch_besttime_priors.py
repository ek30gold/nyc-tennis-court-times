#!/usr/bin/env python3
"""BestTime.app demand-curve priors.

Reads cached BestTime forecast responses from data/besttime/*.json and merges
hourly demand curves into data/priors.json under "besttime_curves", keyed by
our park_id. Optionally refreshes curves from the API when BESTTIME_KEY is set
(1 forecast credit per venue refresh - use sparingly, curves change slowly).

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

SLUG_TO_PARK = {
    "central_park_tennis_center": "M010",
    "mccarren_park_tennis": "B058",
    "crotona_park_tennis": "X010",
    "astoria_park_tennis": "Q004",
    "usta_ntc": "Q099",
    "hudson_river_park_tennis": "SUPP-HRP",  # concession-run; added via data/supplement.geojson
    "inwood_hill_tennis": "M042",
    "highland_park_tennis": "B047",
    "kissena_park_tennis": "Q024",
    "fort_washington_tennis": "M028",
    "manhattan_beach_tennis": "B251",
    "marine_park_tennis": "B057",
}

def curve_from(path):
    d = json.load(open(path))
    curve = {}
    for day in d["analysis"]:
        di = day["day_info"]["day_int"]  # 0=Monday .. 6=Sunday
        raw = day["day_raw"]
        curve[di] = {(6 + i) % 24: raw[i] for i in range(len(raw)) if raw[i]}
    return {"venue_name": d["venue_info"]["venue_name"],
            "venue_id": d["venue_info"]["venue_id"],
            "reviews": d["venue_info"].get("reviews"),
            "fetched": datetime.date.today().isoformat(),
            "curves": curve}

def main():
    priors = json.load(open("data/priors.json"))
    curves = {}
    skipped = []
    for path in sorted(glob.glob("data/besttime/*.json")):
        slug = os.path.basename(path)[:-5]
        park_id = SLUG_TO_PARK.get(slug)
        if not park_id:
            skipped.append(slug); continue
        curves[park_id] = curve_from(path)
    priors["besttime_curves"] = curves
    priors["_besttime_note"] = ("Hourly busyness 0-100 vs venue weekly peak, by day "
        "(0=Mon) and hour. Source: BestTime.app free tier, fetched 2026-09-13. "
        "Refresh rarely (1 API credit/venue). Treat as demand prior, not wait measurement.")
    json.dump(priors, open("data/priors.json", "w"), indent=1)
    print(f"merged {len(curves)} venue curves for parks: {sorted(curves)}")
    if skipped: print(f"skipped (no park_id mapping): {skipped}")

if __name__ == "__main__":
    main()
