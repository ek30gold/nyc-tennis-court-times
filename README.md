# NYC Tennis Wait Times

A private map of NYC public tennis courts with predicted wait times. No crowdsourcing, no cameras: a prediction model over public data, calibrated over time by a personal wait log.

## What's live

- 99 facilities (97 from NYC Open Data + 2 manually verified concession sites: Hudson River Park, Queensboro Oval). Facilities are removed only while a capital project is genuinely under construction (>0% complete), so the live count varies
- Hourly score refresh (Actions cron) + auto-deploy to Pages
- Date/time picker: forecast any future moment client-side (priors + BestTime curves + Open-Meteo 16-day forecast; labeled when beyond the weather horizon; reservation signal applies to tomorrow only)
- Season flip: Central Park, Prospect Park Tennis Center and Queensboro Oval turn gray "indoor (paid)" inside their curated bubble windows
- Popups: court count, wait band, surface, night lights, pickleball note, planned-work notes, season info, nearest restroom + drinking fountain + subway stop, park upkeep grade (Parks inspection ratings 2024+), Google/Apple Maps links, "Report actual wait" feedback link (prefilled GitHub issue)
- Capital Project Tracker removes courts under active construction and notes planned work. A project must be in the construction phase **and** report >0% complete before its facility is removed - the tracker lists projects as "construction" at 0% for months.
- League permit blocks shrink walk-up supply (up to 1.75x demand): nightly agent-side capture of the Parks unavailable-fields API for tonight + tomorrow, applied per hour
- Phase 2: nightly reservation-grid capture feeds a next-day demand modifier on the 6 reservable sites. The Parks WAF blocks GitHub Actions IPs, so the capture runs agent-side through a cloud browser nightly at 9pm ET; CI keeps a manual-dispatch copy. Graceful skip when capture fails

## Verified data sources (checked hands-on 2026-09-13)

| Source | What it gives | Access | URL |
|---|---|---|---|
| NYC Open Data - Athletic Facilities | 624 tennis court records: park ID, borough, surface, lights, geometry. Fresh (updated ~July 2026) | Open REST API, no key | https://data.cityofnewyork.us/d/qnem-b8re |
| NYC Parks tennis reservation grids | Court-by-court Booked/Available per hour, 6 sites, no login. No same-day bookings allowed, so tonight's grid forecasts tomorrow's walk-up pressure | Server-rendered HTML behind AWS WAF - needs headless browser | https://www.nycgovparks.org/tennisreservation |
| NYC Parks permit availability | League permit blocks per court/date, updated ~daily | Spreadsheet downloads, same WAF | https://www.nycgovparks.org/permits/field-and-court/map |
| Open-Meteo | Free hourly weather, no key. Rain = court closures + post-rain surge | Open REST API | https://open-meteo.com |
| Reddit (lore mining) | Per-court crowd reputation, one-off batch seed | Free API tier (~100 QPM, non-commercial) | https://www.reddit.com/dev/api |
| BestTime.app | Hourly demand curves, venue types include PARK and TENNIS | Free test account (100 credits); Basic $29/mo min - NOT needed | https://besttime.app |

BestTime coverage verified live 2026-09-13. Covered (curves baked into priors.json): Central Park Tennis Center, McCarren Park, Crotona Park Tennis Center, Astoria Park, Hudson River Park, Inwood Hill Park, plus Highland, Kissena, Fort Washington, Manhattan Beach and Marine Park. **USTA Billie Jean King NTC is deliberately excluded**: it is a paid private facility that merely shares a park boundary with Flushing Meadows' free public courts, and its curve was previously mapped onto them. Found but not forecastable (insufficient visitor volume): Riverside, Fort Greene, Prospect Park Tennis Center, Sportime Randall's Island, Sutton East, Alley Pond, Mill Pond, Cunningham, East River, Van Cortlandt. Rule of thumb: only marquee venues with ~100+ visitors/day and a Google-scale listing qualify.

Camera path: ruled out. NYC DOT's traffic cameras all face roadways; zero are within range of any major tennis facility.

## Model

Per-facility expected-wait band (walk-on / short / 30-60 / 1h+ / indoor / closed), refreshed on the hourly cron.

The model is **multiplicative** - every term scales the one before it, nothing is subtracted:

```
score = base                      # BestTime curve for this hour, else baseline(hour, weekday)
      x lore multiplier           # per-facility; 1.0 for most facilities (see Coverage)
      x weather modifier          # 0.0 while raining (-> band "closed"), 1.5 after >1mm in the last 6h
      x reservation modifier      # reservable sites only, and only on the day the capture describes
      x supply modifier           # permit-blocked courts, up to 1.75x (full blackout -> band "closed")
      x temperature modifier      # 0.55-1.0 ladder, outdoor courts only
      x school-closure boost      # 1.2x, 09:00-16:00 on school-closed dates
      x demand index              # booking density at nearby reservable sites
```

### What the numbers do and don't mean

**The bands are a heuristic ranking, not a measured wait time.** No ground-truth
wait has ever been recorded, so nothing here has been validated against reality:

- Every constant - the band cut-offs (0.35 / 0.65 / 1.1), the temperature ladder,
  the 1.75x supply cap, the 0.6 transfer coefficients, the Gaussian peak centres -
  is an **uncalibrated estimate with no empirical source**. There is no mapping
  anywhere from a score to a number of minutes; the label "30-60 min" is a guess.
- `data/history/` is not yet an archive (nothing writes to it), so the terms
  documented as "pending history calibration" cannot currently be calibrated.
- Treat a band as "probably busier than that one", not as "expect 45 minutes".

### Coverage - how much signal a given facility actually has

Each entry in `web/scores.json` carries a `coverage` field so a well-supported
prediction is distinguishable from a guess, and the map renders `default`
facilities muted:

| `coverage` | facilities | what drives the score |
|---|---|---|
| `curve` | up to 11 | a real BestTime demand curve covering that hour |
| `lore` | 2-4 | a hand-seeded per-facility multiplier (Reddit lore mine) |
| `default` | ~86 | the shared baseline curve only - hour, weather and permits |

Counts shift by hour: curves are sparse (Central Park's covers 06:00-18:00
only), and a facility whose curve does not cover the current hour falls back to
the baseline and reports `lore` or `default` for that hour rather than `curve`.
Two facilities (Central Park, McCarren) have both a curve and a lore multiplier.

Most facilities are `default`: they differ from each other **only** by hour,
weather and permit blocks. A companion `demand_source` field reports whether the
reservation-demand term came from a nearby site (`near`), a citywide mean
(`citywide`), or was not applied (`none`) - the nearest reservable court is a
median ~6.8km away, so `citywide` carries little information.

`generated_at` is timezone-aware; the map shows the data's real age and warns
when it is stale (scheduled runs are frequently dropped by GitHub, so "hourly"
is a target, not a guarantee).

**Not implemented:** the "first-dry-hour surge" described in earlier versions of
this README does not exist in the code. Rain sets the modifier to 0.0 and the
band to `closed`; the post-rain surge is a flat 1.5x for six hours after >1mm.

v1.5 begins with the "Report actual wait" popup link (prefilled GitHub issues); reports feed manual multiplier updates in priors.json.

## Phases

- Phase 1 (this scaffold): court layer + weather + seeded priors -> scores.json -> static map. No scraping risk.
- Tests: `python3 -m unittest discover tests` (stdlib only, no dependencies). CI runs them on every push and PR.
- Phase 2: headless-browser scrapers for reservation grids + permit spreadsheets.
- Phase 3 (v1.5): personal wait log on Supabase free tier.

## Architecture

GitHub Actions cron -> Python jobs -> commit `web/scores.json` -> static front-end (MapLibre GL) on any static host. Two schedules: `refresh-scores` (hourly; closures + scoring) and `refresh-slow-data` (daily; courts, amenities, transit, quality - these change monthly at best). Two schedules: `refresh-scores` (hourly; closures + scoring) and `refresh-slow-data` (daily; courts, amenities, transit, quality - these change monthly at best). No backend in v1. Note: GitHub Pages on a PRIVATE repo needs a paid GitHub plan; Cloudflare Pages/Vercel free tiers host private-repo static sites fine.
