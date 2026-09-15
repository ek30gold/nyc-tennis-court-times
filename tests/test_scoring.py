#!/usr/bin/env python3
"""Regression tests for scripts/compute_scores.py (stdlib unittest only).

Run from the repo root: python3 -m unittest discover tests
Importing the module is side-effect free - main() stays behind __main__.
"""
import datetime, json, math, os, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import compute_scores as cs  # noqa: E402

NYC = cs.NYC
PRIORS = json.load(open(os.path.join(ROOT, "data", "priors.json")))
M010 = {"park_id": "M010", "court_count": 23, "lighted": False}


def dt(y, m, d, h):
    return datetime.datetime(y, m, d, h, tzinfo=NYC)


class TestBaseline(unittest.TestCase):
    """Anti-drift guard: the JS client ports baseline() verbatim."""

    def test_canonical_table(self):
        cases = [
            (5, 2, 0.05), (21, 2, 0.05), (23, 6, 0.05),
            (18, 2, 0.25 + 0.55 * math.exp(-((18 - 18.5) / 2.5) ** 2)),
            (10, 5, 0.25 + 0.65 * math.exp(-((10 - 10) / 2.5) ** 2)),
            (10, 0, 0.25 + 0.55 * math.exp(-((10 - 18.5) / 2.5) ** 2)),
        ]
        for hour, wd, want in cases:
            with self.subTest(hour=hour, weekday=wd):
                self.assertAlmostEqual(cs.baseline(hour, wd), want, places=9)

    def test_monday_is_zero_weekend_is_five_and_six(self):
        # Monday=0 indexing: 0-4 use the evening peak, 5-6 the morning peak.
        self.assertGreater(cs.baseline(18, 0), cs.baseline(10, 0))
        self.assertGreater(cs.baseline(10, 5), cs.baseline(18, 5))
        self.assertGreater(cs.baseline(10, 6), cs.baseline(18, 6))
        self.assertEqual(dt(2026, 9, 14, 12).weekday(), 0)  # 2026-09-14 is a Monday


class TestBand(unittest.TestCase):
    def test_thresholds(self):
        self.assertEqual(cs.band(0.0), "walk-on")
        self.assertEqual(cs.band(0.34), "walk-on")
        self.assertEqual(cs.band(0.35), "short")
        self.assertEqual(cs.band(0.64), "short")
        self.assertEqual(cs.band(0.65), "30-60 min")
        self.assertEqual(cs.band(1.09), "30-60 min")
        self.assertEqual(cs.band(1.1), "1h+")

    def test_non_finite_is_not_the_worst_band(self):
        for bad in (float("nan"), float("inf"), float("-inf"), None, "x"):
            with self.subTest(score=bad):
                self.assertNotEqual(cs.band(bad), "1h+")
                self.assertEqual(cs.band(bad), "unknown")
        # "unknown" is the shared vocabulary item web/index.html renders grey
        # and ranks last; it must never be the alarming band.
        self.assertEqual(cs.band(float("nan")), "unknown")


class TestCurveGap(unittest.TestCase):
    """Bug 1: BestTime curves are sparse; a missing hour is no data, not no demand."""

    def test_m010_curve_really_is_sparse(self):
        bt = PRIORS["besttime_curves"]
        self.assertIsNotNone(cs.curve_value(bt, "M010", 2, 12))
        self.assertIsNone(cs.curve_value(bt, "M010", 2, 19))

    def test_m010_19h_is_not_walk_on(self):
        bt = PRIORS["besttime_curves"]
        r = cs.score_facility(M010, dt(2026, 9, 16, 19), PRIORS, bt=bt, sunset_h=None)
        self.assertGreater(r["score"], 0.0)
        self.assertNotEqual(r["band"], "walk-on")
        self.assertEqual(r["coverage"], "lore")   # fell back: not "curve" for this hour

    def test_covered_hour_reports_curve(self):
        bt = PRIORS["besttime_curves"]
        r = cs.score_facility(M010, dt(2026, 9, 16, 12), PRIORS, bt=bt, sunset_h=None)
        self.assertEqual(r["coverage"], "curve")

    def test_unknown_facility_reports_default(self):
        p = {"park_id": "ZZZ9", "court_count": 4}
        r = cs.score_facility(p, dt(2026, 9, 16, 19), PRIORS, bt={}, sunset_h=None)
        self.assertEqual(r["coverage"], "default")
        self.assertGreater(r["score"], 0.0)


class TestRain(unittest.TestCase):
    """Bug 2: weather_modifier 0.0 used to paint the whole map green."""

    def test_rain_closes_outdoor(self):
        wmod = cs.weather_modifier(0.4, 0.0)
        self.assertEqual(wmod, 0.0)
        r = cs.score_facility(M010, dt(2026, 9, 16, 12), PRIORS,
                              bt=PRIORS["besttime_curves"], wmod=wmod,
                              current_precip=0.4, sunset_h=None)
        self.assertEqual(r["band"], "closed")

    def test_rain_keeps_indoor_indoor(self):
        seasons = {"M010": {"indoor_window": {"start": "11-01", "end": "03-31"}}}
        r = cs.score_facility(M010, dt(2027, 1, 10, 12), PRIORS,
                              bt=PRIORS["besttime_curves"], wmod=0.0,
                              current_precip=0.4, seasons=seasons, sunset_h=None)
        self.assertEqual(r["band"], "indoor")


class TestRecentPrecip(unittest.TestCase):
    """Bug 3: [-6:] read six days into the FORECAST, not the past 6 hours."""

    def _wx(self):
        times = [f"2026-09-14T{h:02d}:00" for h in range(0, 24)]
        # past hours (< 12:00) carry rain; the future is dry except a decoy spike
        precip = [1.0] * 12 + [0.0] * 12
        precip[-1] = 99.0
        return {"current": {"time": "2026-09-14T12:15", "precipitation": 0.0},
                "hourly": {"time": times, "precipitation": precip}}

    def test_window_is_backward_looking(self):
        self.assertEqual(cs.recent_precip_mm(self._wx()), 6.0)   # 06:00-11:00

    def test_ignores_future_spike(self):
        self.assertLess(cs.recent_precip_mm(self._wx()), 99.0)

    def test_short_or_missing_array_is_safe(self):
        self.assertEqual(cs.recent_precip_mm({}), 0.0)
        self.assertEqual(cs.recent_precip_mm({"current": {"time": "2026-09-14T12:00"},
                                              "hourly": {}}), 0.0)
        short = {"current": {"time": "2026-09-14T02:00"},
                 "hourly": {"time": ["2026-09-14T00:00", "2026-09-14T01:00"],
                            "precipitation": [0.5, None]}}
        self.assertEqual(cs.recent_precip_mm(short), 0.5)


class TestReservationGate(unittest.TestCase):
    """Bugs 4 & 5: the res modifier is next-day-only; demand needs the same
    freshness gate the modifier has."""

    def _res(self, captured_at):
        return {"status": "ok", "captured_at": captured_at,
                "sites": {"central_park": {"park_ids": ["M010"], "density": 0.9,
                                           "daily": {"2026-09-15": {"density": 0.9}}}}}

    def test_applies_to_is_capture_date_plus_one(self):
        now = datetime.datetime(2026, 9, 14, 12, tzinfo=datetime.timezone.utc)
        mods, _fc, applies, _cap, age = cs.reservation_mods(self._res("2026-09-14T01:53:24+00:00"), now)
        self.assertEqual(applies, "2026-09-14")   # 01:53 UTC = 2026-09-13 NYC, +1 day
        self.assertIn("M010", mods)
        self.assertLess(age, cs.RES_MAX_AGE_H)

    def test_modifier_only_multiplies_on_its_day(self):
        res_mod = {"M010": 1.24}
        kw = dict(bt=PRIORS["besttime_curves"], sunset_h=None, res_mod=res_mod)
        on = cs.score_facility(M010, dt(2026, 9, 16, 12), PRIORS, apply_res=True, **kw)
        off = cs.score_facility(M010, dt(2026, 9, 16, 12), PRIORS, apply_res=False, **kw)
        self.assertAlmostEqual(on["score"], off["score"] * 1.24, places=9)
        self.assertNotAlmostEqual(on["score"], off["score"])

    def test_stale_capture_is_not_fresh(self):
        now = datetime.datetime(2026, 9, 14, 12, tzinfo=datetime.timezone.utc)
        stale = self._res("2026-08-14T01:53:24+00:00")
        self.assertFalse(cs.reservation_fresh(stale, now)[0])
        mods, _fc, applies, _cap, _age = cs.reservation_mods(stale, now)
        self.assertEqual((mods, applies), ({}, None))

    def test_failed_or_unparseable_capture_is_not_fresh(self):
        now = datetime.datetime(2026, 9, 14, 12, tzinfo=datetime.timezone.utc)
        self.assertFalse(cs.reservation_fresh({"status": "blocked",
                                               "captured_at": "2026-09-14T01:00:00+00:00"}, now)[0])
        self.assertFalse(cs.reservation_fresh({"status": "ok", "captured_at": "nonsense"}, now)[0])
        self.assertFalse(cs.reservation_fresh({}, now)[0])

    def test_stale_demand_term_is_skipped_and_reported_none(self):
        # main() gates demand on reservation_fresh; a stale capture yields an
        # empty today_demand, so no facility claims a measured demand source.
        r = cs.score_facility(M010, dt(2026, 9, 16, 12), PRIORS,
                              bt=PRIORS["besttime_curves"], today_demand={}, sunset_h=None)
        self.assertEqual(r["demand_source"], "none")

    def test_demand_source_is_reported_when_applied(self):
        r = cs.score_facility(M010, dt(2026, 9, 16, 12), PRIORS,
                              bt=PRIORS["besttime_curves"], sunset_h=None,
                              today_demand={"M010": 1.2}, demand_sources={"M010": "near"})
        self.assertEqual(r["demand_source"], "near")

    def test_demand_index_reports_branch_per_facility(self):
        courts = {"features": [
            {"properties": {"park_id": "M010"}, "geometry": {"coordinates": [-73.96, 40.79]}},
            {"properties": {"park_id": "B058"}, "geometry": {"coordinates": [-73.95, 40.72]}},
            {"properties": {"park_id": "M144"}, "geometry": {"coordinates": [-73.97, 40.72]}},
            {"properties": {"park_id": "FAR1"}, "geometry": {"coordinates": [-70.00, 41.50]}}]}
        res = {"sites": {
            "a": {"park_ids": ["M010"], "daily": {"2026-09-14": {"density": 0.9}}},
            "b": {"park_ids": ["B058"], "daily": {"2026-09-14": {"density": 0.4}}}}}
        out = cs.demand_index(res, courts)
        src = out["sources"]["2026-09-14"]
        self.assertEqual(src["M144"], "near")
        self.assertEqual(src["FAR1"], "citywide")
        self.assertNotIn("M010", out["dates"]["2026-09-14"])       # reservable sites excluded
        self.assertIsInstance(out["dates"]["2026-09-14"]["M144"], float)  # client multiplies by this


class TestSupply(unittest.TestCase):
    """Bug 7: a 1.75x multiplier cannot say "there are no courts"."""

    def _permits(self, n):
        return {"days": {"2026-09-16": {"12": {"M010": n}}}}

    def test_partial_block_still_caps_at_1_75(self):
        self.assertAlmostEqual(cs.supply_mod(23, 23), 1.75)
        self.assertAlmostEqual(cs.supply_mod(12, 23), 1.0 + 12 / 23)
        self.assertEqual(cs.supply_mod(0, 23), 1.0)
        self.assertEqual(cs.supply_mod(5, 0), 1.0)

    def test_full_blackout_is_closed(self):
        r = cs.score_facility(M010, dt(2026, 9, 16, 12), PRIORS,
                              bt=PRIORS["besttime_curves"], permits=self._permits(23),
                              sunset_h=None)
        self.assertEqual(r["band"], "closed")

    def test_partial_blackout_is_not_closed(self):
        r = cs.score_facility(M010, dt(2026, 9, 16, 12), PRIORS,
                              bt=PRIORS["besttime_curves"], permits=self._permits(11),
                              sunset_h=None)
        self.assertNotEqual(r["band"], "closed")
        self.assertTrue(cs.full_blackout(23, 23))
        self.assertFalse(cs.full_blackout(11, 23))


class TestLoadJson(unittest.TestCase):
    """Bug 6: malformed optional data must degrade, not kill the run."""

    def setUp(self):
        self.tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp_bad.json")

    def tearDown(self):
        if os.path.exists(self.tmp): os.remove(self.tmp)

    def test_missing_file(self):
        self.assertEqual(cs.load_json(os.path.join(ROOT, "nope.json")), {})
        self.assertEqual(cs.load_json(os.path.join(ROOT, "nope.json"), ({}, {})), ({}, {}))

    def test_truncated_file(self):
        with open(self.tmp, "w") as f: f.write('{"dates": {"2026-09-14": ')
        self.assertEqual(cs.load_json(self.tmp), {})

    def test_wrong_shape(self):
        with open(self.tmp, "w") as f: f.write('[1, 2, 3]')
        self.assertEqual(cs.load_json(self.tmp), {})

    def test_extract_failure_degrades(self):
        with open(self.tmp, "w") as f: f.write('{"dates": null}')
        self.assertEqual(cs.load_json(self.tmp, {}, lambda d: d["dates"]["x"]), {})


class TestHolidays(unittest.TestCase):
    """Bug 11: a holiday entry without "kind" must not raise."""

    def test_missing_kind(self):
        r = cs.score_facility(M010, dt(2026, 9, 16, 12), PRIORS,
                              bt=PRIORS["besttime_curves"], hol={"name": "Some Day"},
                              sunset_h=None)
        self.assertIn(r["band"], {"walk-on", "short", "30-60 min", "1h+"})

    def test_school_closed_boost(self):
        kw = dict(bt=PRIORS["besttime_curves"], sunset_h=None)
        plain = cs.score_facility(M010, dt(2026, 9, 16, 12), PRIORS, **kw)
        boost = cs.score_facility(M010, dt(2026, 9, 16, 12), PRIORS,
                                  hol={"name": "x", "kind": "school_closed"}, **kw)
        self.assertAlmostEqual(boost["score"], plain["score"] * 1.2, places=9)


class TestFreeSignals(unittest.TestCase):
    def test_recent_rain_suppresses_not_inflates_demand(self):
        self.assertEqual(cs.weather_modifier(0.0, 1.1), 0.75)

    def test_wind_ladder(self):
        self.assertEqual(cs.wind_modifier(None, None), 1.0)
        self.assertEqual(cs.wind_modifier(10, 19), 1.0)
        self.assertEqual(cs.wind_modifier(21, 10), 0.85)
        self.assertEqual(cs.wind_modifier(20, 35), 0.65)
        self.assertEqual(cs.wind_modifier(40), 0.45)

    def test_calendar_ranges_and_remote_day_exclusion(self):
        cal = json.load(open(os.path.join(ROOT, "data", "holidays.json")))
        wd, boost, event = cs.calendar_effect(cal, "2027-02-16", 1, 12)
        self.assertEqual((wd, boost, event["kind"]), (1, 1.2, "school_closed"))
        wd, boost, event = cs.calendar_effect(cal, "2026-11-03", 1, 12)
        self.assertEqual((wd, boost, event), (1, 1.0, None))

    def test_holiday_uses_saturday_curve(self):
        cal = json.load(open(os.path.join(ROOT, "data", "holidays.json")))
        wd, boost, event = cs.calendar_effect(cal, "2026-11-26", 3, 10)
        self.assertEqual((wd, boost, event["kind"]), (5, 1.0, "holiday"))


class TestReservationStatus(unittest.TestCase):
    def _res(self, status):
        stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
        return {"status": status, "captured_at": stamp, "sites": {}}

    def test_partial_capture_is_still_usable(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        # A 5-of-6 scrape is real data; rejecting it would throw away the term.
        self.assertTrue(cs.reservation_fresh(self._res("partial"), now)[0])
        self.assertTrue(cs.reservation_fresh(self._res("ok"), now)[0])

    def test_unavailable_is_rejected(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        self.assertFalse(cs.reservation_fresh(self._res("unavailable"), now)[0])


class TestSunsetAndIndoor(unittest.TestCase):
    def test_unlit_after_sunset_is_closed(self):
        r = cs.score_facility(M010, dt(2026, 9, 16, 19), PRIORS,
                              bt=PRIORS["besttime_curves"], sunset_h=19)
        self.assertEqual(r["band"], "closed")

    def test_indoor_window_wins(self):
        seasons = {"M010": {"indoor_window": {"start": "11-01", "end": "03-31"}}}
        r = cs.score_facility(M010, dt(2027, 1, 10, 19), PRIORS,
                              bt=PRIORS["besttime_curves"], seasons=seasons, sunset_h=17)
        self.assertEqual(r["band"], "indoor")


if __name__ == "__main__":
    unittest.main()
