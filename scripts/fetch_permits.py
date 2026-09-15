#!/usr/bin/env python3
"""League-permit blocks per court/hour -> data/permits.json.

THERE IS NO SCRAPER HERE, and this script does not write data/permits.json.

The upstream source (nycgovparks.org/permits/field-and-court/map) sits behind
the same AWS WAF as the reservation grids, so the capture is performed manually
/ agent-side with a real browser and the result is committed by hand. That
makes data/permits.json the least-validated input in the pipeline, even though
compute_scores.py multiplies demand by up to 1.75x from it and renders
"League play today ..." popup lines out of it.

So what this script DOES do is validate the committed file against the shape
compute_scores.py actually reads, and fail loudly when it drifts:

    python3 scripts/fetch_permits.py

Manual capture process:
  1. Open the permits map in a real browser, signed out.
  2. For each date you want covered, export the unavailable-fields response.
  3. Reshape to {"fetched_at": <utc iso>, "days": {"YYYY-MM-DD":
     {"<hour>": {"<park_id>": <blocked court count>}}}}.
  4. park_id MUST match data/courts.geojson exactly (note Q001A, not Q001).
  5. Run this validator before committing.
"""
import json, sys, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PERMITS = ROOT / "data/permits.json"
COURTS = ROOT / "data/courts.geojson"
STALE_DAYS = 2

def validate(permits, court_ids, today=None):
    """-> (errors, warnings). Mirrors how compute_scores.permit_blocked reads it:
    permits["days"][date][str(hour)][park_id] -> blocked court count."""
    errors, warnings = [], []
    today = today or datetime.date.today()
    if not isinstance(permits, dict):
        return ["permits.json is not a JSON object"], warnings
    days = permits.get("days")
    if not isinstance(days, dict) or not days:
        return ["permits.json has no 'days' object"], warnings
    unmatched = set()
    for date, hours in days.items():
        try:
            datetime.date.fromisoformat(date)
        except (TypeError, ValueError):
            errors.append(f"day key {date!r} is not an ISO date"); continue
        if not isinstance(hours, dict):
            errors.append(f"{date}: expected an hour->parks object"); continue
        for hour, parks in hours.items():
            if not (str(hour).isdigit() and 0 <= int(hour) <= 23):
                errors.append(f"{date}: hour key {hour!r} is not 0-23"); continue
            if not isinstance(parks, dict):
                errors.append(f"{date} {hour}: expected a park_id->count object"); continue
            for pid, n in parks.items():
                if not isinstance(n, int) or isinstance(n, bool) or n < 0:
                    errors.append(f"{date} {hour} {pid}: blocked count {n!r} is not a non-negative int")
                if pid not in court_ids:
                    unmatched.add(pid)
    if unmatched:
        # This is the failure that hid in plain sight: Alley Pond's facility is
        # Q001A in courts.geojson, so permit blocks filed under Q001 were
        # silently dropped by permit_blocked()'s .get(park_id, 0).
        errors.append(f"park_ids absent from courts.geojson (their blocks are "
                      f"SILENTLY IGNORED by compute_scores): {sorted(unmatched)}")
    covered = sorted(d for d in days if _is_date(d))
    if covered and covered[-1] < (today + datetime.timedelta(days=1)).isoformat():
        warnings.append(f"coverage ends {covered[-1]}; from {covered[-1]} onward the "
                        f"supply term silently returns 1.0 for every facility")
    return errors, warnings

def _is_date(d):
    try:
        datetime.date.fromisoformat(d); return True
    except (TypeError, ValueError):
        return False

def main():
    permits = json.loads(PERMITS.read_text())
    court_ids = {f["properties"]["park_id"]
                 for f in json.loads(COURTS.read_text())["features"]}
    errors, warnings = validate(permits, court_ids)
    days = sorted(d for d in (permits.get("days") or {}) if _is_date(d))
    print(f"permits.json: {len(days)} day(s) {days[:1]}..{days[-1:]}, "
          f"fetched_at={permits.get('fetched_at')}")
    for w in warnings: print("  warning:", w)
    for e in errors: print("  ERROR:", e)
    if errors:
        sys.exit(1)
    print("  ok - schema and park_id joins valid")

if __name__ == "__main__":
    main()
