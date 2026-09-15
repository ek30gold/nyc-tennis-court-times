#!/usr/bin/env python3
"""Compute per-facility expected-wait scores.

v1 signals: baseline demand curve x per-facility lore prior x weather modifier.
Phase 2 adds: booked-slot density from reservation grids (tonight's grid for
tomorrow -> next-day demand modifier on the 6 reservable sites).
Output: web/scores.json consumed by the static map.

Numeric constants here (band thresholds, priors multipliers, temp ladder, the
1.75 supply cap, the 0.6 transfer coefficients) are UNCALIBRATED ESTIMATES -
no ground-truth wait times exist yet. They are deliberately left as-is;
retuning them without data would be guessing, not fixing.
"""
import json, math, urllib.request, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Anchor every data path to the repo root. Bare "data/..." made correctness
# depend on the invocation directory - that is how the orphan data/data/
# quality.json was created by running a script from inside data/.
ROOT = Path(__file__).resolve().parent.parent

OPEN_METEO = ("https://api.open-meteo.com/v1/forecast?latitude=40.78&longitude=-73.97"
              "&current=precipitation,temperature_2m,wind_speed_10m,wind_gusts_10m&daily=sunset&timezone=America%2FNew_York"
              "&past_hours=6&hourly=precipitation")
WX_TIMEOUT_S = 30
NYC = ZoneInfo("America/New_York")
RES_MAX_AGE_H = 36   # a capture older than this no longer describes "today"

def baseline(hour, weekday):
    """Typical NYC public-court demand curve. Weekday evenings and weekend
    mornings are peak; overnight near zero (most courts close at dusk).
    Canonical definition - web/index.html ports this verbatim; keep in sync.
    weekday is Monday=0 (Python weekday()), as everywhere else in this repo."""
    if hour < 6 or hour >= 21: return 0.05
    evening_peak = math.exp(-((hour - 18.5) / 2.5) ** 2)
    morning_peak = math.exp(-((hour - 10) / 2.5) ** 2)
    base = 0.25 + (0.55 * evening_peak if weekday < 5 else 0.65 * morning_peak)
    return min(base, 1.0)   # base maxes at 0.90 today; clamp kept to match the JS port

def weather_modifier(current_precip, recent_precip_mm):
    """Demand scaler only. 0.0 during rain means "nobody is playing" - callers
    must band outdoor courts "closed", not "walk-on" (a 0.0 score is not an
    invitation to show up)."""
    if current_precip > 0: return 0.0          # courts closed: no wait, no play
    if recent_precip_mm > 1.0: return 0.75     # recently soaked courts suppress play even after rain stops
    return 1.0

def temp_modifier(c):
    """Temperature demand scaler (estimate): cold kills walk-up demand, extreme
    heat trims it. Neutral 16-29C. Applied to outdoor courts only."""
    if c is None: return 1.0
    if c < 4: return 0.55
    if c < 10: return 0.8
    if c < 16: return 0.9
    if c < 29: return 1.0
    if c < 35: return 0.95
    return 0.8

def wind_modifier(speed_kmh, gust_kmh=None):
    """Wind disrupts tennis before it stops other outdoor activity. Use the
    worse of sustained wind and gusts, degrading gracefully when absent."""
    vals = [v for v in (speed_kmh, gust_kmh) if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not vals: return 1.0
    w = max(vals)
    if w < 20: return 1.0
    if w < 30: return 0.85
    if w < 40: return 0.65
    return 0.45

def calendar_effect(calendar, day_iso, weekday, hour):
    """Return (effective weekday, daytime multiplier, label) from NYCPS dates
    and date ranges. Full public holidays use Saturday timing; school-only
    closures keep the real weekday and boost daytime walk-up demand."""
    event = (calendar.get("dates") or {}).get(day_iso)
    if not event:
        for r in calendar.get("ranges") or []:
            if r.get("start", "") <= day_iso <= r.get("end", ""):
                event = r
                break
    if not event: return weekday, 1.0, None
    kind = event.get("kind")
    eff = 5 if kind == "holiday" else weekday
    boost = 1.2 if kind == "school_closed" and weekday < 5 and 9 <= hour <= 16 else 1.0
    return eff, boost, event

def load_json(path, default=None, extract=None):
    """Best-effort load of an optional data file. A missing OR malformed file
    (truncated write, wrong shape) degrades to `default` with a warning instead
    of killing the hourly run. Essential inputs (courts, priors, weather) are
    deliberately NOT routed through here - they must fail loudly."""
    try:
        with open(ROOT / path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise TypeError(f"expected a JSON object, got {type(data).__name__}")
        return extract(data) if extract else data
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        print(f"warning: {ROOT / path} unusable ({type(e).__name__}: {e}) - continuing without it")
        return {} if default is None else default

def recent_precip_mm(wx, hours=6):
    """Precipitation over the `hours` hourly buckets strictly BEFORE the current
    hour. The hourly array is ~6 past hours + ~7 forecast days, so a naive
    [-6:] reads NEXT WEEK; match hourly["time"] against current["time"] the way
    web/index.html does (which sums hour-1 .. hour-6). Short/missing arrays
    degrade to 0."""
    hourly = wx.get("hourly") or {}
    times, vals = hourly.get("time") or [], hourly.get("precipitation") or []
    cur = (wx.get("current") or {}).get("time")
    if not cur or not times or not vals: return 0.0
    cut = cur[:13] + ":00" if len(cur) >= 13 else cur   # start of the current hour
    n = min(len(times), len(vals))
    past = [i for i in range(n) if times[i] < cut]   # ISO strings sort chronologically
    return sum(vals[i] or 0 for i in past[-hours:])

def popup_lines(p, planned, seasons, amenities, transit, quality, permits, today, demand_label=None):
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
    today_blocks = ((permits.get("days") or {}).get(today) or {})
    park_hours = {h: c.get(p["park_id"], 0) for h, c in today_blocks.items()}
    for ws, we, mx in block_windows(park_hours):
        lines.append(f"League play today {ws:02d}:00-{we:02d}:00 (up to {mx} of {p['court_count']} courts)")
    if demand_label: lines.append(demand_label)
    return lines

def block_windows(blocks_by_hour):
    """{hour: count} -> [(start_hour, end_hour_exclusive, max_courts)] over consecutive hours."""
    hours = sorted(int(h) for h, c in blocks_by_hour.items() if c)
    wins = []
    for h in hours:
        if wins and h == wins[-1][1]:
            wins[-1][1] = h + 1
        else:
            wins.append([h, h + 1, 0])
    for w in wins:
        w[2] = max(blocks_by_hour.get(str(h), 0) for h in range(w[0], w[1]))
    return wins

def permit_blocked(permits, park_id, day, hour):
    """Courts taken by league permits at that hour (0 if unknown)."""
    blocks = ((permits.get("days") or {}).get(day) or {}).get(str(hour)) or {}
    return blocks.get(park_id, 0) or 0

def supply_mod(blocked, court_count):
    """League-permit blocked courts shrink walk-up supply: up to 1.75x demand
    (cap is an uncalibrated estimate). A FULL blackout is not expressible as a
    multiplier - callers band those facilities "closed" instead."""
    if not blocked or not court_count: return 1.0
    return 1.0 + min(blocked / court_count, 0.75)

def full_blackout(blocked, court_count):
    """Every court permit-blocked -> nothing to walk on."""
    return bool(court_count) and blocked >= court_count

def in_window(dt, win):
    md = dt.strftime("%m-%d")
    s, e = win["start"], win["end"]
    return (md >= s or md <= e) if s > e else (s <= md <= e)

def band(score):
    """Thresholds are uncalibrated estimates; vocabulary is fixed and shared
    with the client: walk-on | short | 30-60 min | 1h+ | indoor | closed | unknown."""
    try:
        finite = math.isfinite(score)
    except TypeError:
        finite = False
    # NaN fails every "<" and used to fall through to "1h+": an unknown score
    # must not render as the most alarming band. "unknown" matches the client,
    # which renders it grey and ranks it last.
    if not finite: return "unknown"
    if score < 0.35: return "walk-on"
    if score < 0.65: return "short"
    if score < 1.1: return "30-60 min"
    return "1h+"

RESERVABLE = {"M010", "M071", "X344", "Q001A", "B058"}

def haversine_km(a, b):
    R = 6371.0
    p1, p2 = math.radians(a[1]), math.radians(b[1])
    dp, dl = math.radians(b[1]-a[1]), math.radians(b[0]-a[0])
    x = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*R*math.asin(math.sqrt(x))

def curve_value(bt, park_id, weekday, hour):
    """BestTime curve value (0-100) for this hour, or None when the curve does
    not cover it. Curves are stored SPARSELY (M010 only has 06-18), so a
    missing hour means "no data", never "no demand" - callers fall back to
    baseline() rather than scoring 0.0."""
    curves = (bt.get(park_id) or {}).get("curves") or {}
    dayc = curves.get(str(weekday))
    if dayc is None: dayc = curves.get(weekday)
    if not isinstance(dayc, dict): return None
    v = dayc.get(str(hour), dayc.get(hour))
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

def capture_age_h(res, now_utc):
    """Hours since the reservation grid capture; None if unparseable."""
    try:
        cap = datetime.datetime.fromisoformat(res["captured_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if cap.tzinfo is None: cap = cap.replace(tzinfo=datetime.timezone.utc)
    return (now_utc - cap).total_seconds() / 3600

# "partial" = some sites scraped, some failed. The data that IS there is real,
# and demand_index already requires >=2 reservable parks per date, so a partial
# capture is usable; only "unavailable" (no usable site at all) is rejected.
RES_OK_STATUS = {"ok", "partial"}

def reservation_fresh(res, now_utc):
    """(is_fresh, age_h). Same gate for BOTH the res modifier and the demand
    index - a month-old capture must not keep posing as today's measurement."""
    if not isinstance(res, dict) or res.get("status") not in RES_OK_STATUS: return False, None
    age = capture_age_h(res, now_utc)
    return (age is not None and age < RES_MAX_AGE_H), age

def demand_index(res, courts):
    """Same-day demand barometer: today's final booked density at the 6
    reservable sites (no same-day bookings -> final from last night's capture)
    spills onto walk-up courts. Per date, per walk-up facility: distance-
    weighted index of nearby reservable density, citywide mean as fallback.
    mult = 1 + 0.6*(index-0.65), clamped [0.7, 1.35] (calibration pending
    history archive - transfer strength is an estimate).
    "sources" records which branch each facility took ("near"/"citywide");
    "dates" stays plain numbers - the client multiplies by them directly."""
    coords = {f["properties"]["park_id"]: (f["geometry"]["coordinates"][0],
              f["geometry"]["coordinates"][1]) for f in courts["features"]}
    # per date, per reservable park: mean density across its sites
    per_date = {}
    for site in (res.get("sites") or {}).values():
        for day, dd in (site.get("daily") or {}).items():
            if dd.get("density") is None: continue
            for pid in site["park_ids"]:
                per_date.setdefault(day, {}).setdefault(pid, []).append(dd["density"])
    dates = {d: {pid: sum(v)/len(v) for pid, v in pids.items()}
             for d, pids in per_date.items() if len(pids) >= 2}
    out, src = {}, {}
    for day, dens in dates.items():
        city = sum(dens.values()) / len(dens)
        row, srow = {}, {}
        for pid, xy in coords.items():
            if pid in RESERVABLE: continue
            near = []
            for r, d in dens.items():
                if r not in coords: continue
                dist = haversine_km(xy, coords[r])
                if dist <= 10: near.append((dist, d))
            if near:
                idx = sum(d/(1+dist) for dist, d in near) / sum(1/(1+dist) for dist, _ in near)
                srow[pid] = "near"
            else:
                idx = city
                srow[pid] = "citywide"
            row[pid] = round(min(max(1 + 0.6*(idx - 0.65), 0.7), 1.35), 3)
        out[day], src[day] = row, srow
    return {"dates": out, "sources": src,
            "citywide": {d: round(sum(v.values())/len(v), 3) for d, v in dates.items()},
            "note": "Walk-up demand multiplier from measured same-day reservable bookings; estimate pending history calibration."}

def reservation_mods(res, now_utc):
    """-> (res_mod, res_forecast, applies_to, captured_at, age_h). Empty when
    the capture is missing, failed or stale. applies_to is the capture date +1
    day: tonight's grid describes TOMORROW's walk-up pressure, so the modifier
    is only valid on that one date."""
    fresh, age = reservation_fresh(res, now_utc)
    if not fresh: return {}, {}, None, None, age
    res_mod, res_forecast = {}, {}
    cap = datetime.datetime.fromisoformat(res["captured_at"])
    if cap.tzinfo is None: cap = cap.replace(tzinfo=datetime.timezone.utc)
    applies_to = (cap.astimezone(NYC).date() + datetime.timedelta(days=1)).isoformat()
    for site in (res.get("sites") or {}).values():
        for day, dd in (site.get("daily") or {}).items():
            if dd.get("density") is not None:
                mod = 0.7 + 0.6 * dd["density"]
                for pid in site["park_ids"]:
                    slot = res_forecast.setdefault(day, {})
                    slot[pid] = max(slot.get(pid, 0), round(mod, 3))
        if site.get("density") is not None:
            for pid in site["park_ids"]:
                res_mod[pid] = max(res_mod.get(pid, 0), 0.7 + 0.6 * site["density"])
    return res_mod, res_forecast, applies_to, res.get("captured_at"), age

def score_facility(p, now, priors, *, bt=None, wmod=1.0, current_precip=0.0, tmod=1.0,
                   eff_weekday=None, res_mod=None, apply_res=False, permits=None,
                   seasons=None, hol=None, school_boost=None, today_demand=None, demand_sources=None,
                   sunset_h=None, windmod=1.0):
    """-> {score, band, coverage, demand_source} for one facility at `now`.

    coverage: "curve" (BestTime supplied this hour), "lore" (facility has a
    priors entry but no usable curve value), "default" (generic prior).
    demand_source: "near" | "citywide" | "none" (term not applied)."""
    bt = bt or {}; res_mod = res_mod or {}; permits = permits or {}
    seasons = seasons or {}; today_demand = today_demand or {}; demand_sources = demand_sources or {}
    pid, hour = p["park_id"], now.hour
    day_iso = now.strftime("%Y-%m-%d")
    if eff_weekday is None: eff_weekday = now.weekday()
    fac = (priors.get("facilities") or {}).get(pid)
    mult = (fac or {}).get("mult", priors["default"])
    raw = curve_value(bt, pid, eff_weekday, hour)
    if raw is not None:
        # BestTime curve gives within-venue timing shape; lore multiplier
        # keeps cross-venue scale (45% of Central Park's peak >> 45% of a
        # small court's peak in absolute bodies).
        s, coverage = mult * (raw / 100.0) * wmod, "curve"
    else:
        # Curve absent for this hour (sparse storage) -> explicit baseline
        # fallback. Scoring the gap as 0 demand made 19:00 at Central Park
        # read "walk-on".
        s, coverage = baseline(hour, eff_weekday) * mult * wmod, ("lore" if fac else "default")
    if apply_res:
        s *= res_mod.get(pid, 1.0)
    blocked = permit_blocked(permits, pid, day_iso, hour)
    s *= supply_mod(blocked, p["court_count"])
    win = (seasons.get(pid) or {}).get("indoor_window")
    indoor_now = bool(win and in_window(now, win))
    demand_source = "none"
    if not indoor_now:
        s *= tmod
        s *= windmod
        if school_boost is None:
            school_boost = 1.2 if hol and hol.get("kind") == "school_closed" and now.weekday() < 5 and 9 <= hour <= 16 else 1.0
        s *= school_boost
        dem = today_demand.get(pid)
        if dem:
            s *= dem
            demand_source = demand_sources.get(pid, "citywide")
    b = band(s)
    if sunset_h is not None and hour >= sunset_h and not p.get("lighted"):
        b = "closed"
    if full_blackout(blocked, p["court_count"]):
        b = "closed"            # every court permitted out: nothing to walk on
    if current_precip > 0:
        b = "closed"            # raining: the 0.0 score means "no play", not "no wait"
    if indoor_now:
        b = "indoor"            # bubble/indoor sites play through rain and dusk
    return {"score": s, "band": b, "coverage": coverage, "demand_source": demand_source}

def main():
    courts = json.load(open(ROOT / "data/courts.geojson"))
    priors = json.load(open(ROOT / "data/priors.json"))
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    # Phase 2: reservation-grid density -> per-park demand modifier.
    # Tonight's booked density forecasts tomorrow's pressure; stale (>36h) or
    # unavailable data means no adjustment (graceful degradation).
    res = load_json("data/reservation_density.json")
    res_mod, res_forecast, res_applies_to, res_captured_at, res_age = reservation_mods(res, now_utc)
    if res_mod:
        print(f"reservation modifier on {sorted(res_mod)} (captured {res_age:.1f}h ago, applies {res_applies_to})")
    elif res:
        print(f"reservation modifier skipped (status={res.get('status')!r}, age={res_age})")
    permits = load_json("data/permits.json")
    closed, planned = load_json("data/closures.json", ({}, {}),
        lambda d: (d.get("closed_park_ids") or {}, d.get("planned_park_ids") or {}))
    amenities = load_json("data/amenities.json")
    seasons = load_json("data/seasons.json")
    transit = load_json("data/transit.json")
    quality = load_json("data/quality.json")
    calendar = load_json("data/holidays.json")
    demand = {"dates": {}, "sources": {}, "citywide": {}}
    # Same freshness gate as res_mod: a stale capture must not keep feeding
    # "today's measured demand" for a month.
    if reservation_fresh(res, now_utc)[0]:
        try:
            demand = demand_index(res, courts)
        except (KeyError, TypeError, ValueError) as e:
            print(f"warning: demand index failed ({type(e).__name__}: {e}) - skipping demand term")
    else:
        print("demand index skipped: reservation capture missing or stale")
    wx = json.load(urllib.request.urlopen(OPEN_METEO, timeout=WX_TIMEOUT_S))
    current_precip = wx["current"]["precipitation"] or 0
    sunset_h = None
    try:
        sunset_h = int(wx["daily"]["sunset"][0].split("T")[1][:2])
    except (KeyError, IndexError): pass
    recent_mm = recent_precip_mm(wx)
    now = datetime.datetime.now(NYC)
    # NYC local time, tz-aware - the map's "now" bands are NYC-time by
    # definition, and generated_at carries the offset so clients need not guess.
    wmod = weather_modifier(current_precip, recent_mm)
    today_iso = now.strftime("%Y-%m-%d")
    # Public holidays use Saturday timing; school-only closures retain the
    # weekday curve and add a daytime youth/family demand term.
    eff_weekday, school_boost, hol = calendar_effect(calendar, today_iso, now.weekday(), now.hour)
    cur_temp = wx["current"].get("temperature_2m")
    tmod = temp_modifier(cur_temp)
    windmod = wind_modifier(wx["current"].get("wind_speed_10m"), wx["current"].get("wind_gusts_10m"))
    today_demand = demand["dates"].get(today_iso, {})
    today_demand_src = (demand.get("sources") or {}).get(today_iso, {})
    citywide_today = demand["citywide"].get(today_iso)
    apply_res = bool(res_mod) and today_iso == res_applies_to   # capture date + 1 only
    if res_mod and not apply_res:
        print(f"reservation modifier held: applies to {res_applies_to}, today is {today_iso}")
    bt = priors.get("besttime_curves", {})
    scores = []
    removed = []
    for feat in courts["features"]:
        p = feat["properties"]
        if p["park_id"] in closed:
            removed.append({"park_id": p["park_id"], "name": p.get("name") or p["park_id"],
                            "reason": closed[p["park_id"]][0]["title"]})
            continue
        r = score_facility(p, now, priors, bt=bt, wmod=wmod, current_precip=current_precip,
                           tmod=tmod, eff_weekday=eff_weekday, res_mod=res_mod,
                           apply_res=apply_res, permits=permits, seasons=seasons, hol=hol,
                           school_boost=school_boost, today_demand=today_demand,
                           demand_sources=today_demand_src, sunset_h=sunset_h, windmod=windmod)
        scores.append({"park_id": p["park_id"], "name": p.get("name") or p["park_id"],
            "lat": feat["geometry"]["coordinates"][1],
            "lon": feat["geometry"]["coordinates"][0], "court_count": p["court_count"],
            "popup_lines": popup_lines(p, planned, seasons, amenities, transit, quality, permits, today_iso,
                ("Citywide demand today: %s - reservable courts %d%% booked" %
                 (("very high" if citywide_today >= 0.9 else "high" if citywide_today >= 0.7 else "normal" if citywide_today >= 0.4 else "low"),
                  round(100*citywide_today))) if (citywide_today is not None and p["park_id"] not in RESERVABLE) else None),
            "band": r["band"], "score": round(r["score"], 2),
            "coverage": r["coverage"], "demand_source": r["demand_source"]})
    # client-side recompute model for the date/time picker
    model = {
        "generated_at": now.isoformat(timespec="seconds"),
        "default_prior": priors["default"],
        "facilities": {p["park_id"]: {
            "mult": priors["facilities"].get(p["park_id"], {}).get("mult", priors["default"]),
            "curve": (bt.get(p["park_id"], {}).get("curves") or None),
            "lighted": bool(p.get("lighted"))}
            for feat in courts["features"] for p in [feat["properties"]]
            if p["park_id"] not in closed},
        "sunset_today": sunset_h,
        "season_windows": {pid: v["indoor_window"] for pid, v in seasons.items()
                           if isinstance(v, dict) and v.get("indoor_window")},
        "permits": {"days": permits.get("days", {}), "fetched_at": permits.get("fetched_at")},
        "reservation": {"applies_to": res_applies_to, "captured_at": res_captured_at,
                        "modifiers": res_mod, "forecast": res_forecast},
        "demand": demand,
        "calendar": calendar,
        "holidays": calendar.get("dates", {})}
    json.dump(model, open(ROOT / "web/model.json", "w"))
    json.dump({"generated_at": now.isoformat(timespec="seconds"),
        "weather": {"precip_now": current_precip, "precip_last_6h_mm": round(recent_mm, 1),
                    "temperature_c": cur_temp, "wind_kmh": wx["current"].get("wind_speed_10m"),
                    "gust_kmh": wx["current"].get("wind_gusts_10m")},
        "closures": removed,
        "scores": scores}, open(ROOT / "web/scores.json", "w"), indent=1)
    print(f"scored {len(scores)} facilities (weather modifier {wmod}, removed {len(removed)} closed)")

if __name__ == "__main__":
    main()
