#!/usr/bin/env python3
"""Compute per-facility expected-wait scores.

v1 signals: baseline demand curve x per-facility lore prior x weather modifier.
Phase 2 adds: booked-slot density from reservation grids (tonight's grid for
tomorrow -> next-day demand modifier on the 6 reservable sites).
Output: web/scores.json consumed by the static map.
"""
import json, math, urllib.request, datetime

OPEN_METEO = ("https://api.open-meteo.com/v1/forecast?latitude=40.78&longitude=-73.97"
              "&current=precipitation,temperature_2m"
              "&past_hours=6&hourly=precipitation")

def baseline(hour, weekday):
    """Typical NYC public-court demand curve. Weekday evenings and weekend
    mornings are peak; overnight near zero (most courts close at dusk)."""
    if hour < 6 or hour >= 21: return 0.05
    evening_peak = math.exp(-((hour - 18.5) / 2.5) ** 2)
    morning_peak = math.exp(-((hour - 10) / 2.5) ** 2)
    base = 0.25 + (0.55 * evening_peak if weekday < 5 else 0.65 * morning_peak)
    return min(base, 1.0)

def weather_modifier(current_precip, recent_precip_mm):
    if current_precip > 0: return 0.0          # courts closed: no wait, no play
    if recent_precip_mm > 1.0: return 1.5      # post-rain reopen surge
    return 1.0

def popup_lines(p, planned, seasons, amenities, transit, quality):
    lines = []
    surf = ", ".join(p.get("surfaces", [])) or "Surface unknown"
    lines.append(surf + (" - lit for night play" if p.get("lighted") else ""))
    if p.get("pickleball"): lines.append("Pickleball courts on site too")
    if p.get("note"): lines.append(p["note"])
    if p["park_id"] in planned:
        lines.append("Planned work: " + planned[p["park_id"]][0]["title"])
    sn = (seasons.get(p["park_id"]) or {}).get("season_note")
    if sn: lines.append("Season: " + sn)
    am = amenities.get(p["park_id"], {})
    if am.get("restroom"): lines.append(f"Restroom ~{am['restroom']['dist_m']} m: {am['restroom']['name']}")
    if am.get("fountain"): lines.append(f"Water ~{am['fountain']['dist_m']} m")
    tr = transit.get(p["park_id"])
    if tr: lines.append(f"Subway ~{tr['dist_m']} m: {tr['station']}")
    qu = quality.get(p["park_id"])
    if qu: lines.append(f"Park upkeep: {qu['label']} ({qu['pct_acceptable']}% of {qu['inspections']} inspections passed, 2024+)")
    return lines

def band(score):
    if score < 0.35: return "walk-on"
    if score < 0.65: return "short"
    if score < 1.1: return "30-60 min"
    return "1h+"

def main():
    courts = json.load(open("data/courts.geojson"))
    priors = json.load(open("data/priors.json"))
    # Phase 2: reservation-grid density -> per-park demand modifier.
    # Tonight's booked density forecasts tomorrow's pressure; stale (>36h) or
    # unavailable data means no adjustment (graceful degradation).
    res_mod = {}
    res_applies_to = None
    res_captured_at = None
    try:
        res = json.load(open("data/reservation_density.json"))
        age_h = (datetime.datetime.now(datetime.timezone.utc) -
                 datetime.datetime.fromisoformat(res["captured_at"])).total_seconds() / 3600
        if res.get("status") == "ok" and age_h < 36:
            from zoneinfo import ZoneInfo
            cap = datetime.datetime.fromisoformat(res["captured_at"])
            res_captured_at = res["captured_at"]
            res_applies_to = (cap.astimezone(ZoneInfo("America/New_York")).date()
                              + datetime.timedelta(days=1)).isoformat()
            for site in res["sites"].values():
                if site.get("density") is not None:
                    for pid in site["park_ids"]:
                        res_mod[pid] = max(res_mod.get(pid, 0), 0.7 + 0.6 * site["density"])
            print(f"reservation modifier on {sorted(res_mod)} (captured {age_h:.1f}h ago)")
    except FileNotFoundError:
        pass
    closed, planned, amenities, seasons = {}, {}, {}, {}
    try:
        cl = json.load(open("data/closures.json"))
        closed, planned = cl.get("closed_park_ids", {}), cl.get("planned_park_ids", {})
    except FileNotFoundError: pass
    try: amenities = json.load(open("data/amenities.json"))
    except FileNotFoundError: pass
    try: seasons = json.load(open("data/seasons.json"))
    except FileNotFoundError: pass
    try: transit = json.load(open("data/transit.json"))
    except FileNotFoundError: transit = {}
    try: quality = json.load(open("data/quality.json"))
    except FileNotFoundError: quality = {}
    wx = json.load(urllib.request.urlopen(OPEN_METEO))
    current_precip = wx["current"]["precipitation"] or 0
    recent_mm = sum(v or 0 for v in wx["hourly"]["precipitation"][-6:])
    now = datetime.datetime.now()
    wmod = weather_modifier(current_precip, recent_mm)
    base = baseline(now.hour, now.weekday())
    bt = priors.get("besttime_curves", {})
    scores = []
    removed = []
    for feat in courts["features"]:
        p = feat["properties"]
        if p["park_id"] in closed:
            removed.append({"park_id": p["park_id"], "name": p.get("name", p["park_id"]),
                            "reason": closed[p["park_id"]][0]["title"]})
            continue
        btcurve = bt.get(p["park_id"], {}).get("curves", {})
        dayc = btcurve.get(str(now.weekday())) or btcurve.get(now.weekday())
        if dayc:
            # BestTime curve gives within-venue timing shape; lore multiplier
            # keeps cross-venue scale (45% of Central Parks peak >> 45% of a
            # small courts peak in absolute bodies).
            raw = dayc.get(str(now.hour), dayc.get(now.hour, 0))
            mult = priors["facilities"].get(p["park_id"], {}).get("mult", priors["default"])
            s = mult * (raw / 100.0) * wmod
        else:
            prior = priors["facilities"].get(p["park_id"], {}).get("mult", priors["default"])
            s = base * prior * wmod
        s *= res_mod.get(p["park_id"], 1.0)
        scores.append({"park_id": p["park_id"], "name": p.get("name", p["park_id"]), "lat": feat["geometry"]["coordinates"][1],
            "lon": feat["geometry"]["coordinates"][0], "court_count": p["court_count"],
            "popup_lines": popup_lines(p, planned, seasons, amenities, transit, quality),
            "band": band(s), "score": round(s, 2)})
    # client-side recompute model for the date/time picker
    model = {
        "generated_at": now.isoformat(timespec="seconds"),
        "default_prior": priors["default"],
        "facilities": {p["park_id"]: {
            "mult": priors["facilities"].get(p["park_id"], {}).get("mult", priors["default"]),
            "curve": (bt.get(p["park_id"], {}).get("curves") or None)}
            for feat in courts["features"] for p in [feat["properties"]]
            if p["park_id"] not in closed},
        "reservation": {"applies_to": res_applies_to, "captured_at": res_captured_at,
                        "modifiers": res_mod}}
    json.dump(model, open("web/model.json", "w"))
    json.dump({"generated_at": now.isoformat(timespec="seconds"),
        "weather": {"precip_now": current_precip, "precip_last_6h_mm": round(recent_mm, 1)},
        "closures": removed,
        "scores": scores}, open("web/scores.json", "w"), indent=1)
    print(f"scored {len(scores)} facilities (weather modifier {wmod}, removed {len(removed)} closed)")

if __name__ == "__main__":
    main()
