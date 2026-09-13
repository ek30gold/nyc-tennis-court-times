# NYC Tennis Wait Times

A private map of NYC public tennis courts with predicted wait times. No crowdsourcing, no cameras: a prediction model over public data, calibrated over time by a personal wait log.

## Verified data sources (checked hands-on 2026-09-13)

| Source | What it gives | Access | URL |
|---|---|---|---|
| NYC Open Data - Athletic Facilities | 624 tennis court records: park ID, borough, surface, lights, geometry. Fresh (updated ~July 2026) | Open REST API, no key | https://data.cityofnewyork.us/d/qnem-b8re |
| NYC Parks tennis reservation grids | Court-by-court Booked/Available per hour, 6 sites, no login. No same-day bookings allowed, so tonight's grid forecasts tomorrow's walk-up pressure | Server-rendered HTML behind AWS WAF - needs headless browser | https://www.nycgovparks.org/tennisreservation |
| NYC Parks permit availability | League permit blocks per court/date, updated ~daily | Spreadsheet downloads, same WAF | https://www.nycgovparks.org/permits/field-and-court/map |
| Open-Meteo | Free hourly weather, no key. Rain = court closures + post-rain surge | Open REST API | https://open-meteo.com |
| Reddit (lore mining) | Per-court crowd reputation, one-off batch seed | Free API tier (~100 QPM, non-commercial) | https://www.reddit.com/dev/api |
| BestTime.app | Hourly demand curves, venue types include PARK and TENNIS | Free test account (100 credits); Basic $29/mo min - NOT needed | https://besttime.app |

BestTime coverage verified live 2026-09-13. Covered (curves baked into priors.json): Central Park Tennis Center, McCarren Park, Crotona Park Tennis Center, Astoria Park, USTA Billie Jean King NTC, Hudson River Park, Inwood Hill Park. Found but not forecastable (insufficient visitor volume): Riverside, Fort Greene, Prospect Park Tennis Center, Sportime Randall's Island, Sutton East, Alley Pond, Mill Pond, Cunningham, East River, Van Cortlandt. Rule of thumb: only marquee venues with ~100+ visitors/day and a Google-scale listing qualify.

Camera path: ruled out. NYC DOT's traffic cameras all face roadways; zero are within range of any major tennis facility.

## Model

Per-facility expected-wait band (walk-on / short / 30-60 / 1h+), refreshed hourly:

```
score = baseline_prior(hour, weekday, season, per-court lore)
      + demand proxy (booked-slot density at nearest reservable site, next day)
      - supply (permit-blocked courts)
      + weather modifier (current rain, rain in last 6h, first-dry-hour surge)
```

v1.5 adds a one-tap personal wait log that Bayesian-updates the priors for the courts actually visited.

## Phases

- Phase 1 (this scaffold): court layer + weather + seeded priors -> scores.json -> static map. No scraping risk.
- Phase 2: headless-browser scrapers for reservation grids + permit spreadsheets.
- Phase 3 (v1.5): personal wait log on Supabase free tier.

## Architecture

GitHub Actions cron -> Python jobs -> commit `web/scores.json` -> static front-end (MapLibre GL) on any static host. No backend in v1. Note: GitHub Pages on a PRIVATE repo needs a paid GitHub plan; Cloudflare Pages/Vercel free tiers host private-repo static sites fine.
