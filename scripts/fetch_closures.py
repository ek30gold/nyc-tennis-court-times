#!/usr/bin/env python3
"""Detect courts closed by construction via the NYC Capital Project Tracker
(4hcv-tc5r, updated daily). Writes data/closures.json.

A facility counts as CLOSED only when an active capital project at its park_id
mentions tennis AND construction has actually started (percent complete > 0).
Removing a facility from the map is the most destructive thing this pipeline
does, so "construction" as a phase label is not enough on its own: the tracker
lists projects in the construction phase at 0% for months before any fence goes
up. Those become planned-work notes instead.
"""
import json, re, urllib.request, urllib.parse, datetime
from pathlib import Path

BASE = "https://data.cityofnewyork.us/resource/4hcv-tc5r.json"
ROOT = Path(__file__).resolve().parent.parent
LIMIT = 500

def court_level(title, summary=""):
    """Does this project plausibly take tennis courts out of service?
    Inspects title AND summary, matching the SoQL $where below - they used to
    disagree, so a project whose only tennis mention was in the summary was
    fetched and then silently discarded."""
    t = f"{title or ''} {summary or ''}".lower()
    # Buildings, not playing surfaces. Word-bounded: an unanchored "house"
    # matches NYCHA "... Houses" and "... Playhouse".
    if re.search(r"\broof\b|\bhouse\b", t): return False
    if "court" in t: return True
    if "tennis" in t and ("reconstruction" in t or "pavement" in t): return True
    return False  # paths and other work: courts stay open

def pct(v):
    """constructionpercentcomplete arrives as a STRING ("0.0"). -> float or None."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def main():
    q = ("?$select=parkid,title,summary,currentphase,constructionpercentcomplete,lastupdated"
         "&$where=" + urllib.parse.quote(
             "(upper(title) like '%TENNIS%' OR upper(summary) like '%TENNIS%')"
             # SoQL is three-valued: `NULL != 'COMPLETED'` is NULL, not true, so
             # the bare inequality silently dropped every unset-phase project.
             " AND (currentphase IS NULL OR upper(currentphase) != 'COMPLETED')")
         + "&$order=parkid" + f"&$limit={LIMIT}")
    with urllib.request.urlopen(BASE + q, timeout=30) as r:
        rows = json.load(r)
    if len(rows) >= LIMIT:
        raise SystemExit(f"closures: hit $limit={LIMIT}; results truncated - paginate before trusting this")
    closed, planned = {}, {}
    for row in rows:
        pid, title = row.get("parkid"), row.get("title")
        if not pid or not court_level(title, row.get("summary")):
            continue
        phase, p = (row.get("currentphase") or ""), pct(row.get("constructionpercentcomplete"))
        entry = {"title": title, "phase": row.get("currentphase"),
                 "construction_pct": row.get("constructionpercentcomplete")}
        # Close only on evidence of actual work, not on the phase label alone.
        if phase.lower() == "construction" and p is not None and p > 0:
            closed.setdefault(pid, []).append(entry)
        else:
            planned.setdefault(pid, []).append(entry)
    out = {"fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "closed_park_ids": closed, "planned_park_ids": planned}
    with open(ROOT / "data/closures.json", "w") as f:
        json.dump(out, f, indent=1)
    print(f"closed={sorted(closed)} planned={sorted(planned)} (from {len(rows)} tennis projects)")

if __name__ == "__main__":
    main()
