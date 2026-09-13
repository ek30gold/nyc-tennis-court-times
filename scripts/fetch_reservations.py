#!/usr/bin/env python3
"""PHASE 2 STUB - reservation grid scraper.

Verified 2026-09-13 (via real browser session):
- 6 active grids: Central Park=/tennisreservation/availability/12,
  Riverside 119=/2, Riverside 96 clay=/3, Mill Pond=/4, Alley Pond=/7, McCarren=/11
- Public, server-rendered court x hour table: Booked / Not Available / Reserve this time
- nycgovparks.org is behind AWS WAF: plain HTTP gets 405 + human-verification
  challenge. Requires Playwright/headless Chrome; reCAPTCHA Enterprise present.
- No same-day reservations -> tonight's booked density forecasts tomorrow's
  walk-up pressure at the site's walk-on courts.

TODO: Playwright script, 1-2 runs/day, parse table -> reservation_density.json
"""
SITES = {"central_park": 12, "riverside_119": 2, "riverside_96_clay": 3,
         "mill_pond": 4, "alley_pond": 7, "mccarren": 11}
raise SystemExit("Phase 2 stub - see docstring for verified scraping notes")
