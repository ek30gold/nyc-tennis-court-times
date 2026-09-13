#!/usr/bin/env python3
"""Compute per-facility expected-wait scores.

v1 signals: baseline demand curve x per-facility lore prior x weather modifier.
Phase 2 adds: booked-slot density from reservation grids, permit-blocked supply.
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

def band(score):
    if score < 0.35: return "walk-on"
    if score < 0.65: return "short"
    if score < 1.1: return "30-60 min"
    return "1h+"

def main():
    courts = json.load(open("data/courts.geojson"))
    priors = json.load(open("data/priors.json"))
    wx = json.load(urllib.request.urlopen(OPEN_METEO))
    current_precip = wx["current"]["precipitation"] or 0
    recent_mm = sum(v or 0 for v in wx["hourly"]["precipitation"][-6:])
    now = datetime.datetime.now()
    wmod = weather_modifier(current_precip, recent_mm)
    base = baseline(now.hour, now.weekday())
    bt = priors.get("besttime_curves", {})
    scores = []
    for feat in courts["features"]:
        p = feat["properties"]
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
        scores.append({"park_id": p["park_id"], "name": p.get("name", p["park_id"]), "lat": feat["geometry"]["coordinates"][1],
            "lon": feat["geometry"]["coordinates"][0], "court_count": p["court_count"],
            "band": band(s), "score": round(s, 2)})
    json.dump({"generated_at": now.isoformat(timespec="seconds"),
        "weather": {"precip_now": current_precip, "precip_last_6h_mm": round(recent_mm, 1)},
        "scores": scores}, open("web/scores.json", "w"), indent=1)
    print(f"scored {len(scores)} facilities (weather modifier {wmod})")

if __name__ == "__main__":
    main()
