#!/usr/bin/env python3
"""Permit-blocked tennis courts -> data/permits.json. DOCUMENTATION ONLY:
nycgovparks.org is WAF-walled for non-browser clients, so the real capture
runs agent-side in the cloud browser on the nightly wake (same job that scrapes
reservation grids). Endpoint used:
  GET /api/athletic-fields/unavailable-fields?datetime=YYYY-MM-DDTHH:00
  -> {"dusk": "HH:MM", "l": ["M144-ZN02-TENNIS-3", ...]}
IDs embed the park id (prefix before first '-') and sport; filter TENNIS and
count distinct ids per park per hour for the rest of today and all of tomorrow.
"""
