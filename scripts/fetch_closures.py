#!/usr/bin/env python3
"""Detect courts closed by construction via the NYC Capital Project Tracker
(4hcv-tc5r, updated daily). Writes data/closures.json.

A facility counts as closed when an ACTIVE (not completed) capital project at
its park_id mentions tennis in the title/summary. False positives matter here
(a removed court that is actually open) so the match is deliberately narrow:
park_id equality + tennis keyword + phase not completed/cancelled.
"""
import json, urllib.request, urllib.parse, datetime

BASE = "https://data.cityofnewyork.us/resource/4hcv-tc5r.json"

def court_level(title):
    t = (title or "").lower()
    if "roof" in t or "house" in t: return False  # buildings, not courts
    if "court" in t: return True
    if "tennis" in t and ("reconstruction" in t or "pavement" in t): return True
    return False  # paths and other work: courts stay open

def main():
    q = ("?$select=parkid,title,summary,currentphase,constructionpercentcomplete,lastupdated"
         "&$where=" + urllib.parse.quote(
             "(upper(title) like '%TENNIS%' OR upper(summary) like '%TENNIS%')"
             " AND upper(currentphase) != 'COMPLETED'")
         + "&$limit=500")
    with urllib.request.urlopen(BASE + q) as r:
        rows = json.load(r)
    closed, planned = {}, {}
    for row in rows:
        pid, title = row.get("parkid"), row.get("title")
        if not pid or not court_level(title):
            continue
        entry = {"title": title, "phase": row.get("currentphase"),
                 "construction_pct": row.get("constructionpercentcomplete")}
        if (row.get("currentphase") or "").lower() == "construction":
            closed.setdefault(pid, []).append(entry)
        else:
            planned.setdefault(pid, []).append(entry)
    out = {"fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "closed_park_ids": closed, "planned_park_ids": planned}
    json.dump(out, open("data/closures.json", "w"), indent=1)
    print(f"closed={sorted(closed)} planned={sorted(planned)}")

if __name__ == "__main__":
    main()
