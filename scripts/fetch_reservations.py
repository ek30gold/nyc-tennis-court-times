#!/usr/bin/env python3
"""Fetch NYC Parks tennis reservation grids -> data/reservation_density.json.

Verified 2026-09-13 (real browser session):
- 6 active grids: Central Park=12, Riverside 119=2, Riverside 96 clay=3,
  Mill Pond=4, Alley Pond=7, McCarren=11 (tennisreservation/availability/<id>)
- Windows differ: Central Park shows a rolling ~29-day window; the other
  venues show 7 days only (site rule: bookings up to 7 days ahead).
- Public, server-rendered court x hour table: Booked / Not Available / Reserve
- nycgovparks.org is behind AWS WAF: plain HTTP gets 405 + human-verification;
  headless Chromium usually passes. If blocked, we record it and exit 0 -
  compute_scores.py just skips the modifier (graceful degradation).
- No same-day reservations -> tonight's booked density forecasts tomorrow's
  walk-up pressure.

Runs in CI nightly (see .github/workflows/reservations.yml).
"""
import json, datetime, re, os, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MONTHS = {m: i for i, m in enumerate(
    ["january","february","march","april","may","june","july",
     "august","september","october","november","december"], 1)}

SITES = {
    "central_park":    {"id": 12, "park_ids": ["M010"]},
    "riverside_119":   {"id": 2,  "park_ids": ["M071"]},
    "riverside_96":    {"id": 3,  "park_ids": ["M071"]},  # clay courts, same park
    "mill_pond":       {"id": 4,  "park_ids": ["X344"]},
    "alley_pond":      {"id": 7,  "park_ids": ["Q001A"]},
    "mccarren":        {"id": 11, "park_ids": ["B058"]},
}
URL = "https://www.nycgovparks.org/tennisreservation/availability/{}"
OUT = ROOT / "data/reservation_density.json"
# Grid cells read "7:00 a.m."; tolerate AM/A.M./am and odd spacing.
TIME_RE = re.compile(r"\d{1,2}:\d{2}\s*[ap]\.?\s*m\.?", re.I)

def tomorrow_nyc():
    from zoneinfo import ZoneInfo
    now = datetime.datetime.now(ZoneInfo("America/New_York"))
    return now.date() + datetime.timedelta(days=1)

def parse_all_days(page):
    """Universal grid parser (verified 2026-09-13, agent-side browser):
    - Central Park: table.calendar per day with a 'Monday, September 14, 2026'
      heading nearby; rolling ~29-day window.
    - All other venues: div.tab-pane with an ISO-date id wrapping a
      table.table-bordered, one tab-pane per day; 7-day booking window only.
    Returns {ISO-date: {booked, reservable, not_available, density}}."""
    out, seen_tables = {}, 0
    for t in page.query_selector_all("table"):
        txt = t.inner_text() or ""
        # Was `"a.m." not in txt`: a brittle exact match on the site's time
        # formatting. "7:00 AM" would have rejected every table on the page.
        if not TIME_RE.search(txt) or ("Booked" not in txt and "Reserve" not in txt):
            continue
        seen_tables += 1
        iso = None
        aid = t.evaluate("el => { const a = el.closest('[id^=\"20\"]'); return a ? a.id : ''; }")
        if re.fullmatch(r"20\d\d-\d\d-\d\d", aid or ""):
            iso = aid
        if not iso:
            h = t.evaluate_handle(
                "el => { let n = el; for (let i=0;i<6&&n;i++){ n = n.previousElementSibling||n.parentElement;"
                " if (n && /20\d\d/.test(n.textContent||\"\") && n.textContent.length<200) return n.textContent.trim(); } return \"\"; }")
            m = re.search(r"(\w+), (\w+) (\d{1,2}), (\d{4})", str(h.json_value()))
            if m:
                # strptime("%B") is locale-dependent: on a runner without an
                # English locale every Central Park heading silently failed.
                mon = MONTHS.get(m.group(2).lower())
                if mon:
                    try:
                        iso = datetime.date(int(m.group(4)), mon, int(m.group(3))).isoformat()
                    except ValueError:
                        pass
        if not iso:
            continue
        booked = reserve = notavail = 0
        for cell in t.query_selector_all("td"):
            x = (cell.inner_text() or "").strip().lower()
            if "booked" in x: booked += 1
            elif "reserve" in x: reserve += 1
            elif "not available" in x: notavail += 1
        # density is booked share of the WHOLE grid. The old denominator was
        # booked+reservable, so permit-blocked slots shrank it and inflated
        # apparent demand - 22 of ~50 committed values pinned at exactly 1.0,
        # where the signal carries no information and double-counts the permit
        # term compute_scores already applies separately.
        total = booked + reserve + notavail
        rec = {"booked": booked, "reservable": reserve,
               "not_available": notavail,
               "density": round(booked / total, 3) if total else None,
               "unavailable_frac": round(notavail / total, 3) if total else None}
        if iso not in out or total > sum(out[iso][k] for k in ("booked", "reservable", "not_available")):
            out[iso] = rec
    if seen_tables and len(out) < seen_tables:
        print(f"    warning: {seen_tables - len(out)} of {seen_tables} grid tables "
              f"had no resolvable date and were dropped")
    return out

def scrape():
    from playwright.sync_api import sync_playwright
    target = tomorrow_nyc()
    label = target.strftime("%A, %B ") + str(target.day) + target.strftime(", %Y")
    results = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            viewport={"width": 1366, "height": 900})
        page = ctx.new_page()
        for name, meta in SITES.items():
            try:
                page.goto(URL.format(meta["id"]), wait_until="domcontentloaded", timeout=45000)
                # Only Central Park uses table.calendar; the other five render
                # div.tab-pane > table.table-bordered. Waiting on table.calendar
                # alone would time out on 5 of 6 sites.
                page.wait_for_selector("table.calendar, div.tab-pane table, table.table-bordered",
                                       timeout=45000)
                daily = parse_all_days(page)
                tom = daily.get(target.isoformat(), {})
                results[name] = {
                    "park_ids": meta["park_ids"], "grid_date": target.isoformat(),
                    "booked": tom.get("booked"), "reservable": tom.get("reservable"),
                    "not_available": tom.get("not_available"), "density": tom.get("density"),
                    "daily": daily}
            except Exception as e:
                results[name] = {"error": str(e)[:200]}
        browser.close()
    return results

def usable_sites(sites):
    """Sites that actually yielded a density. The old check was `"density" in r`,
    which tests KEY PRESENCE - scrape() always writes the key, so it reported
    6/6 even when all six values were None."""
    return [s for s, r in sites.items() if r.get("density") is not None]

def main():
    out = {"captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "status": "ok", "sites": {}}
    try:
        out["sites"] = scrape()
    except Exception as e:
        out["status"] = "unavailable"
        out["detail"] = str(e)[:300]
    ok = usable_sites(out["sites"])
    # status must be trustworthy: compute_scores gates the whole reservation and
    # demand terms on status == "ok". It used to stay "ok" unless EVERY site
    # failed, so a 5-of-6 wipeout still published as healthy.
    if not ok:
        out["status"] = "unavailable"
    elif len(ok) < len(SITES):
        out["status"] = "partial"
    print(f"status={out['status']} sites_with_density={len(ok)}/{len(SITES)}")
    for s, r in out["sites"].items():
        print(" ", s, r.get("density", r.get("error")))
    if not ok:
        # Never replace a good capture with an empty stub. The committed file
        # holds a forward-looking booking window that stays valid for days;
        # one WAF block used to erase all of it and still exit 0.
        print(f"no usable site data - leaving {OUT} untouched")
        raise SystemExit(1)
    # Atomic promote: a crash mid-write must not leave truncated JSON behind
    # (compute_scores degrades on it, but only after logging a warning).
    fd, tmp = tempfile.mkstemp(dir=str(OUT.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(out, f, indent=1)
        os.replace(tmp, OUT)
    except BaseException:
        if os.path.exists(tmp): os.unlink(tmp)
        raise

if __name__ == "__main__":
    main()
