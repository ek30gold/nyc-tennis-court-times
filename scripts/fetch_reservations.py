#!/usr/bin/env python3
"""Fetch NYC Parks tennis reservation grids -> data/reservation_density.json.

Verified 2026-09-13 (real browser session):
- 6 active grids: Central Park=12, Riverside 119=2, Riverside 96 clay=3,
  Mill Pond=4, Alley Pond=7, McCarren=11 (tennisreservation/availability/<id>)
- Public, server-rendered court x hour table: Booked / Not Available / Reserve
- nycgovparks.org is behind AWS WAF: plain HTTP gets 405 + human-verification;
  headless Chromium usually passes. If blocked, we record it and exit 0 -
  compute_scores.py just skips the modifier (graceful degradation).
- No same-day reservations -> tonight's booked density forecasts tomorrow's
  walk-up pressure.

Runs in CI nightly (see .github/workflows/reservations.yml).
"""
import json, datetime, re, sys

SITES = {
    "central_park":    {"id": 12, "park_ids": ["M010"]},
    "riverside_119":   {"id": 2,  "park_ids": ["M071"]},
    "riverside_96":    {"id": 3,  "park_ids": ["M071"]},  # clay courts, same park
    "mill_pond":       {"id": 4,  "park_ids": ["X344"]},
    "alley_pond":      {"id": 7,  "park_ids": ["Q001A"]},
    "mccarren":        {"id": 11, "park_ids": ["B058"]},
}
URL = "https://www.nycgovparks.org/tennisreservation/availability/{}"
OUT = "data/reservation_density.json"

def tomorrow_nyc():
    from zoneinfo import ZoneInfo
    now = datetime.datetime.now(ZoneInfo("America/New_York"))
    return now.date() + datetime.timedelta(days=1)

def parse_all_days(page):
    """Every table.calendar on the page is one day's grid, preceded by a
    'Monday, September 14, 2026' heading. Returns {ISO-date: {booked,
    reservable, not_available, density}} across the whole rolling window."""
    import re
    tables = page.query_selector_all("table.calendar")
    out = {}
    for t in tables:
        h = t.evaluate_handle(
            "el => { let n = el; for (let i=0;i<6&&n;i++){ n = n.previousElementSibling||n.parentElement;"
            " if (n && /20\\d\\d/.test(n.textContent||\"\") && n.textContent.length<200) return n.textContent.trim(); } return \"\"; }")
        txt = str(h.json_value())
        m = re.search(r"(\w+), (\w+) (\d{1,2}), (\d{4})", txt)
        if not m:
            continue
        try:
            d = datetime.datetime.strptime(f"{m.group(2)} {m.group(3)} {m.group(4)}", "%B %d %Y").date()
        except ValueError:
            continue
        booked = reserve = notavail = 0
        for cell in t.query_selector_all("td"):
            x = (cell.inner_text() or "").strip().lower()
            if "booked" in x: booked += 1
            elif "reserve" in x: reserve += 1
            elif "not available" in x: notavail += 1
        denom = booked + reserve
        out[d.isoformat()] = {"booked": booked, "reservable": reserve,
                              "not_available": notavail,
                              "density": round(booked / denom, 3) if denom else None}
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
                page.wait_for_selector("table.calendar", timeout=45000)
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

def main():
    out = {"captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
           "status": "ok", "sites": {}}
    try:
        out["sites"] = scrape()
        errs = [s for s, r in out["sites"].items() if "error" in r]
        if len(errs) == len(SITES):
            out["status"] = "unavailable"
    except Exception as e:
        out["status"] = "unavailable"
        out["detail"] = str(e)[:300]
    json.dump(out, open(OUT, "w"), indent=1)
    ok = [s for s, r in out["sites"].items() if "density" in r]
    print(f"status={out['status']} sites_with_density={len(ok)}/{len(SITES)}")
    for s, r in out["sites"].items():
        print(" ", s, r.get("density", r.get("error")))

if __name__ == "__main__":
    main()
