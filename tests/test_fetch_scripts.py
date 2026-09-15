#!/usr/bin/env python3
"""End-to-end smoke tests for the scripts/fetch_*.py data-ingestion scripts
(stdlib unittest only, matching tests/test_scoring.py's style).

Run from the repo root: python3 -m unittest discover tests

Every script is ROOT-anchored (Path(__file__).resolve().parent.parent), so
every test here monkeypatches the target module's ROOT (and, for the two
modules that precompute paths at import time - fetch_permits' PERMITS/COURTS
and fetch_reservations' OUT - those constants too) to a tempfile
TemporaryDirectory seeded with only the inputs that test needs. No test
touches the real data/ or web/ directories, and ALL network access is
stubbed at urllib.request.urlopen - nothing here depends on egress.
"""
import contextlib, datetime, io, json, os, re, sys, tempfile, unittest, zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_courts            # noqa: E402
import fetch_amenities          # noqa: E402
import fetch_transit            # noqa: E402
import fetch_quality            # noqa: E402
import fetch_closures           # noqa: E402
import fetch_besttime_priors    # noqa: E402
import fetch_permits            # noqa: E402
import fetch_reservations       # noqa: E402


# ----------------------------------------------------------------------
# Shared network-stubbing helpers
# ----------------------------------------------------------------------

class FakeResponse:
    """Stands in for the object urllib.request.urlopen(...) returns: usable
    both as a context manager (`with urlopen(url) as r: json.load(r)`) and
    as a plain call (`urlopen(url).read()`)."""
    def __init__(self, data):
        self._data = data if isinstance(data, (bytes, bytearray)) else data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def rows(payload):
    return json.dumps(payload).encode()


def make_fake_urlopen(url_to_payload):
    """url_to_payload: {substring-of-url: bytes-payload}. Raises if a call
    doesn't match any registered substring, so an unstubbed network call
    fails loudly instead of silently reaching out."""
    def _fake(url, *a, **kw):
        for key, payload in url_to_payload.items():
            if key in url:
                return FakeResponse(payload)
        raise AssertionError(f"unstubbed urlopen call: {url}")
    return _fake


def capture_stdout():
    return contextlib.redirect_stdout(io.StringIO())


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f)


def mp_square(lon, lat, eps=0.0005):
    """A trivial single-ring MultiPolygon around (lon, lat), matching
    fetch_courts.centroid()'s expected shape."""
    ring = [[lon - eps, lat - eps], [lon + eps, lat - eps],
            [lon + eps, lat + eps], [lon - eps, lat + eps], [lon - eps, lat - eps]]
    return {"coordinates": [[ring]]}


def courts_geojson(park_specs):
    """park_specs: list of (park_id, lon, lat, name). Minimal valid
    courts.geojson as fetch_amenities/fetch_transit/fetch_quality read it."""
    return {"type": "FeatureCollection", "_generated_at": "2026-09-14T00:00:00+00:00",
            "features": [{"type": "Feature",
                          "geometry": {"type": "Point", "coordinates": [lon, lat]},
                          "properties": {"park_id": pid, "name": name, "borough": "M",
                                         "court_count": 4, "surfaces": ["Hard"],
                                         "lighted": False, "pickleball": False}}
                         for pid, lon, lat, name in park_specs]}


# ----------------------------------------------------------------------
# Group 1a: fetch_courts.py - the regression class + its guard rails
# ----------------------------------------------------------------------

class TestFetchCourts(unittest.TestCase):
    def _court_rows(self, specs):
        """specs: list of (gispropnum_or_None, lon, lat)."""
        out = []
        for pid, lon, lat in specs:
            out.append({"gispropnum": pid, "borough": "M", "surface_type": "Hard",
                        "field_lighted": "true", "pickleball": False,
                        "multipolygon": mp_square(lon, lat)})
        return out

    def _names_payload(self, mapping):
        return [{"gispropnum": k, "name311": v} for k, v in mapping.items()]

    def test_main_end_to_end_writes_valid_geojson(self):
        specs = [("P1", -73.97, 40.78), ("P1", -73.97, 40.78), ("P2", -73.90, 40.70)]
        fake = make_fake_urlopen({
            "enfh-gkve": rows(self._names_payload({"P1": "Park One", "P2": None})),
            "qnem-b8re": rows(self._court_rows(specs)),
        })
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "data").mkdir()
            with patch.object(fetch_courts, "ROOT", Path(td)), \
                 patch("urllib.request.urlopen", side_effect=fake), \
                 capture_stdout():
                fetch_courts.main()
            out = json.load(open(Path(td) / "data" / "courts.geojson"))
            self.assertEqual(out["type"], "FeatureCollection")
            self.assertIn("_generated_at", out)
            self.assertEqual(len(out["features"]), 2)
            by_id = {f["properties"]["park_id"]: f for f in out["features"]}
            self.assertEqual(by_id["P1"]["properties"]["court_count"], 2)
            self.assertEqual(by_id["P1"]["properties"]["name"], "Park One")
            # names.get(park) or park: a real-but-null name311 falls back to the id
            self.assertEqual(by_id["P2"]["properties"]["name"], "P2")

    def test_idless_rows_are_dropped_not_bucketed_unknown(self):
        specs = [("P1", -73.97, 40.78), (None, -73.5, 40.5), (None, -73.4, 40.4)]
        fake = make_fake_urlopen({
            "enfh-gkve": rows(self._names_payload({})),
            "qnem-b8re": rows(self._court_rows(specs)),
        })
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "data").mkdir()
            with patch.object(fetch_courts, "ROOT", Path(td)), \
                 patch("urllib.request.urlopen", side_effect=fake), \
                 capture_stdout() as out_s:
                fetch_courts.main()
            out = json.load(open(Path(td) / "data" / "courts.geojson"))
            ids = {f["properties"]["park_id"] for f in out["features"]}
            self.assertEqual(ids, {"P1"})
            self.assertNotIn("UNKNOWN", ids)
            self.assertIn("dropped 2 court records with no gispropnum", out_s.getvalue())

    def test_shrink_guard_refuses_write_without_override(self):
        # Existing file has 10 facilities; the new fetch only yields 3 (<90%).
        existing = courts_geojson([(f"E{i}", -73.9 + i * 0.01, 40.7, f"E{i}") for i in range(10)])
        specs = [("N1", -73.97, 40.78), ("N2", -73.96, 40.77), ("N3", -73.95, 40.76)]
        fake = make_fake_urlopen({
            "enfh-gkve": rows(self._names_payload({})),
            "qnem-b8re": rows(self._court_rows(specs)),
        })
        with tempfile.TemporaryDirectory() as td:
            write_json(Path(td) / "data" / "courts.geojson", existing)
            env = dict(os.environ)
            env.pop("COURTS_ALLOW_SHRINK", None)
            with patch.object(fetch_courts, "ROOT", Path(td)), \
                 patch("urllib.request.urlopen", side_effect=fake), \
                 patch.dict(os.environ, env, clear=True), \
                 capture_stdout():
                with self.assertRaises(SystemExit):
                    fetch_courts.main()
            # the stale existing file must be untouched
            still = json.load(open(Path(td) / "data" / "courts.geojson"))
            self.assertEqual(len(still["features"]), 10)

    def test_shrink_guard_override_env_allows_write(self):
        existing = courts_geojson([(f"E{i}", -73.9 + i * 0.01, 40.7, f"E{i}") for i in range(10)])
        specs = [("N1", -73.97, 40.78), ("N2", -73.96, 40.77), ("N3", -73.95, 40.76)]
        fake = make_fake_urlopen({
            "enfh-gkve": rows(self._names_payload({})),
            "qnem-b8re": rows(self._court_rows(specs)),
        })
        with tempfile.TemporaryDirectory() as td:
            write_json(Path(td) / "data" / "courts.geojson", existing)
            with patch.object(fetch_courts, "ROOT", Path(td)), \
                 patch("urllib.request.urlopen", side_effect=fake), \
                 patch.dict(os.environ, {"COURTS_ALLOW_SHRINK": "1"}), \
                 capture_stdout():
                fetch_courts.main()
            new = json.load(open(Path(td) / "data" / "courts.geojson"))
            self.assertEqual(len(new["features"]), 3)


# ----------------------------------------------------------------------
# Group 1b: fetch_amenities.py - THE regression this whole layer exists for
# ----------------------------------------------------------------------

class TestFetchAmenities(unittest.TestCase):
    def test_main_end_to_end_metadata_does_not_crash_count(self):
        """Seed a known number of facilities; assert the written file has
        that many facility entries PLUS the metadata key, and the printed
        count excludes the metadata key. This is the exact shape of bug
        fixed in 7fc52d1 (counting `out.values()` after `_generated_at` was
        added indexed the timestamp string as if it were a facility dict)."""
        park_specs = [("P1", -73.97, 40.78, "One"), ("P2", -73.96, 40.77, "Two"),
                      ("P3", -73.95, 40.76, "Three")]
        courts = courts_geojson(park_specs)
        rest_rows = [{"latitude": "40.7801", "longitude": "-73.9701",
                     "facility_name": "Near P1", "status": "Operational"}]
        fount_rows = [{"geolocation": {"coordinates": [-73.9601, 40.7701]}, "status": "Operational"}]
        fake = make_fake_urlopen({
            "i7jb-7jku": rows(rest_rows),
            "qnv7-p7a2": rows(fount_rows),
        })
        with tempfile.TemporaryDirectory() as td:
            write_json(Path(td) / "data" / "courts.geojson", courts)
            with patch.object(fetch_amenities, "ROOT", Path(td)), \
                 patch("urllib.request.urlopen", side_effect=fake), \
                 capture_stdout() as out_s:
                fetch_amenities.main()  # must not raise TypeError on the metadata entry
            out = json.load(open(Path(td) / "data" / "amenities.json"))
            self.assertIn("_generated_at", out)
            facility_keys = [k for k in out if not k.startswith("_")]
            self.assertEqual(len(facility_keys), 3)
            self.assertEqual(set(facility_keys), {"P1", "P2", "P3"})
            # every real entry is a dict with restroom/fountain, never the metadata string
            for k in facility_keys:
                self.assertIsInstance(out[k], dict)
                self.assertIn("restroom", out[k])
                self.assertIn("fountain", out[k])
            self.assertIsInstance(out["_generated_at"], str)
            printed = out_s.getvalue()
            # "amenities for X/3 facilities" - X counts real hits, never includes metadata
            m = re.search(r"amenities for (\d+)/(\d+) facilities", printed)
            self.assertIsNotNone(m)
            self.assertEqual(int(m.group(2)), 3)
            self.assertLessEqual(int(m.group(1)), 3)

    def test_regression_actually_catches_the_reverted_bug(self):
        """Build a SCRATCH copy of fetch_amenities.py with the fix reverted
        (count after adding _generated_at, as it was before commit 7fc52d1)
        and confirm running it raises TypeError - i.e. this test class would
        have failed CI on the pre-fix code."""
        src = (ROOT / "scripts" / "fetch_amenities.py").read_text()
        fixed_block = (
            "    got = sum(1 for v in out.values() if v[\"restroom\"] or v[\"fountain\"])\n"
            "    facility_count = len(out)\n"
            "    out[\"_generated_at\"] = _now()   # \"_\"-prefixed: compute_scores does amenities.get(park_id)\n"
        )
        buggy_block = (
            "    out[\"_generated_at\"] = _now()   # \"_\"-prefixed: compute_scores does amenities.get(park_id)\n"
            "    got = sum(1 for v in out.values() if v[\"restroom\"] or v[\"fountain\"])\n"
            "    facility_count = len(out)\n"
        )
        self.assertIn(fixed_block, src, "fetch_amenities.py no longer matches the expected fixed shape")
        reverted_src = src.replace(fixed_block, buggy_block)
        self.assertNotEqual(reverted_src, src)

        with tempfile.TemporaryDirectory() as td:
            scratch = Path(td) / "scratch_fetch_amenities.py"
            scratch.write_text(reverted_src)
            data_dir = Path(td) / "data"
            data_dir.mkdir()
            write_json(data_dir / "courts.geojson",
                       courts_geojson([("P1", -73.97, 40.78, "One")]))
            fake = make_fake_urlopen({
                "i7jb-7jku": rows([]),
                "qnv7-p7a2": rows([]),
            })
            ns = {"__name__": "scratch_fetch_amenities", "__file__": str(scratch)}
            code = compile(reverted_src, str(scratch), "exec")
            with patch("urllib.request.urlopen", side_effect=fake):
                exec(code, ns)
                # ROOT in the scratch module is computed from __file__'s
                # grandparent, which is td here (scratch has no scripts/
                # parent) - point it at our seeded data dir directly.
                ns["ROOT"] = Path(td)
                with self.assertRaises(TypeError):
                    ns["main"]()


# ----------------------------------------------------------------------
# Group 1c: fetch_transit.py
# ----------------------------------------------------------------------

def build_gtfs_zip(stops):
    """stops: list of (lat, lon, name, location_type)."""
    buf = io.StringIO()
    buf.write("stop_id,stop_lat,stop_lon,stop_name,location_type\n")
    for i, (lat, lon, name, loc_type) in enumerate(stops):
        buf.write(f"S{i},{lat},{lon},{name},{loc_type}\n")
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        z.writestr("stops.txt", buf.getvalue())
    return zbuf.getvalue()


class TestFetchTransit(unittest.TestCase):
    def test_main_end_to_end_and_distance_filter(self):
        # A parent station (location_type=1) 100m from P1, a non-parent
        # station (location_type=0, must be ignored) even closer, and no
        # station within MAX_M of P2.
        gtfs = build_gtfs_zip([
            (40.7809, -73.9700, "Near P1 Express", "1"),
            (40.78001, -73.97001, "Platform-only stop", "0"),
        ])
        courts = courts_geojson([("P1", -73.97, 40.78, "One"), ("P2", -74.20, 40.50, "Two")])
        fake = make_fake_urlopen({"rrgtfsfeeds": gtfs})
        with tempfile.TemporaryDirectory() as td:
            write_json(Path(td) / "data" / "courts.geojson", courts)
            with patch.object(fetch_transit, "ROOT", Path(td)), \
                 patch("urllib.request.urlopen", side_effect=fake), \
                 capture_stdout():
                fetch_transit.main()
            out = json.load(open(Path(td) / "data" / "transit.json"))
            self.assertIn("_generated_at", out)
            self.assertIn("P1", out)
            self.assertEqual(out["P1"]["station"], "Near P1 Express")
            # P2 is far from every station -> excluded entirely (no null/placeholder)
            self.assertNotIn("P2", out)


# ----------------------------------------------------------------------
# Group 1d: fetch_quality.py
# ----------------------------------------------------------------------

class TestFetchQuality(unittest.TestCase):
    def test_main_end_to_end_labels_and_metadata(self):
        courts = courts_geojson([("P1", -73.97, 40.78, "One"), ("P2", -73.96, 40.77, "Two")])
        # P1: 3 'A' inspections -> strong (100%). P2: only 2 inspections -> excluded (<3).
        # A null-field row must be skipped, not KeyError. A prop_id for an
        # unmapped park must be dropped silently.
        insp = [{"prop_id": "P1-A", "overall_condition": "A"},
                {"prop_id": "P1-B", "overall_condition": "A"},
                {"prop_id": "P1-C", "overall_condition": "A"},
                {"prop_id": "P2-A", "overall_condition": "A"},
                {"prop_id": "P2-B", "overall_condition": "U"},
                {"prop_id": None, "overall_condition": "A"},
                {"prop_id": "ZZZ-A", "overall_condition": None},
                {"prop_id": "OTHER-A", "overall_condition": "A"}]
        fake = make_fake_urlopen({"yg3y-7juh": rows(insp)})
        with tempfile.TemporaryDirectory() as td:
            write_json(Path(td) / "data" / "courts.geojson", courts)
            with patch.object(fetch_quality, "ROOT", Path(td)), \
                 patch("urllib.request.urlopen", side_effect=fake), \
                 capture_stdout() as out_s:
                fetch_quality.main()
            out = json.load(open(Path(td) / "data" / "quality.json"))
            self.assertIn("_generated_at", out)
            facility_keys = [k for k in out if not k.startswith("_")]
            self.assertEqual(facility_keys, ["P1"])
            self.assertEqual(out["P1"]["label"], "strong")
            self.assertEqual(out["P1"]["inspections"], 3)
            self.assertIn("2 rows skipped for null fields", out_s.getvalue())

    def test_limit_truncation_raises(self):
        with patch.object(fetch_quality, "LIMIT", 2):
            fake = make_fake_urlopen({"yg3y-7juh": rows(
                [{"prop_id": "P1", "overall_condition": "A"}] * 2)})
            with tempfile.TemporaryDirectory() as td:
                write_json(Path(td) / "data" / "courts.geojson", courts_geojson([]))
                with patch.object(fetch_quality, "ROOT", Path(td)), \
                     patch("urllib.request.urlopen", side_effect=fake):
                    with self.assertRaises(SystemExit):
                        fetch_quality.main()


# ----------------------------------------------------------------------
# Group 1e: fetch_closures.py - court_level(), \bhouse\b, and the Dyker
# Beach "construction at 0%" bug
# ----------------------------------------------------------------------

class TestFetchClosuresPureFunctions(unittest.TestCase):
    def test_court_level_inspects_title_and_summary(self):
        self.assertTrue(fetch_closures.court_level("Tennis Court Reconstruction", ""))
        self.assertTrue(fetch_closures.court_level("Playground Upgrade", "new tennis courts"))
        self.assertTrue(fetch_closures.court_level("", "Tennis pavement reconstruction"))
        self.assertFalse(fetch_closures.court_level("Path Repaving", "no racquet sports mentioned"))

    def test_house_word_boundary(self):
        # \bhouse\b must NOT match "Houses": a NYCHA-style title that also
        # legitimately mentions courts/tennis must still be flagged.
        self.assertTrue(fetch_closures.court_level("Whitman Houses Tennis Court Reconstruction", ""))
        # "Playhouse" embeds "house" but with no court/tennis word boundary
        # match either way, and no court/tennis mention -> not flagged.
        self.assertFalse(fetch_closures.court_level("New Playhouse", ""))
        # a genuine standalone "house" DOES exclude, even overriding a
        # "court" mention elsewhere in the title (e.g. "Court House").
        self.assertFalse(fetch_closures.court_level("Court House Renovation", ""))

    def test_roof_excluded(self):
        self.assertFalse(fetch_closures.court_level("Court Roof Replacement", ""))

    def test_pct_parses_string_floats(self):
        self.assertEqual(fetch_closures.pct("0.0"), 0.0)
        self.assertEqual(fetch_closures.pct("45.5"), 45.5)
        self.assertIsNone(fetch_closures.pct(None))
        self.assertIsNone(fetch_closures.pct("n/a"))


class TestFetchClosuresMain(unittest.TestCase):
    def test_dyker_beach_bug_zero_percent_construction_is_planned_not_closed(self):
        capital_rows = [
            {"parkid": "B073", "title": "Dyker Beach Tennis Courts Reconstruction",
             "summary": "", "currentphase": "Construction",
             "constructionpercentcomplete": "0.0", "lastupdated": "2026-09-01"},
            {"parkid": "M010", "title": "Central Park Tennis Center Court Reconstruction",
             "summary": "", "currentphase": "Construction",
             "constructionpercentcomplete": "35.0", "lastupdated": "2026-09-01"},
        ]
        fake = make_fake_urlopen({"4hcv-tc5r": rows(capital_rows)})
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "data").mkdir()
            with patch.object(fetch_closures, "ROOT", Path(td)), \
                 patch("urllib.request.urlopen", side_effect=fake), \
                 capture_stdout():
                fetch_closures.main()
            out = json.load(open(Path(td) / "data" / "closures.json"))
            self.assertIn("fetched_at", out)
            self.assertIn("B073", out["planned_park_ids"])
            self.assertNotIn("B073", out["closed_park_ids"])
            self.assertIn("M010", out["closed_park_ids"])
            self.assertNotIn("M010", out["planned_park_ids"])

    def test_limit_truncation_raises(self):
        with patch.object(fetch_closures, "LIMIT", 1):
            fake = make_fake_urlopen({"4hcv-tc5r": rows(
                [{"parkid": "B073", "title": "Tennis Court", "summary": "",
                 "currentphase": "Construction", "constructionpercentcomplete": "5"}])})
            with tempfile.TemporaryDirectory() as td:
                with patch.object(fetch_closures, "ROOT", Path(td)), \
                     patch("urllib.request.urlopen", side_effect=fake):
                    with self.assertRaises(SystemExit):
                        fetch_closures.main()


# ----------------------------------------------------------------------
# Group 1f: fetch_besttime_priors.py
# ----------------------------------------------------------------------

def besttime_cache(day_int, day_raw, venue_name="Test Venue", venue_id="v1", reviews=10):
    return {"venue_info": {"venue_name": venue_name, "venue_id": venue_id, "reviews": reviews},
            "analysis": [{"day_info": {"day_int": day_int}, "day_raw": day_raw}]}


class TestFetchBesttimePriors(unittest.TestCase):
    def test_post_midnight_hours_file_under_next_day(self):
        # index 20 of day_raw: hour = (6+20)%24 = 2, dayshift = (6+20)//24 = 1
        # Sunday (day_int=6) index 20 must land on Monday (0), hour 2 - not
        # under Sunday, and not lost.
        day_raw = [0] * 24
        day_raw[20] = 55
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "cache.json"
            write_json(cache, besttime_cache(6, day_raw))
            curve = fetch_besttime_priors.curve_from(cache)
            self.assertEqual(curve["curves"][0][2], 55)
            self.assertNotIn(6, curve["curves"])

    def test_fetched_uses_file_mtime_not_today(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "cache.json"
            write_json(cache, besttime_cache(0, [1] + [0] * 23))
            old = datetime.date(2025, 1, 15)
            ts = datetime.datetime(2025, 1, 15, 12, tzinfo=datetime.timezone.utc).timestamp()
            os.utime(cache, (ts, ts))
            curve = fetch_besttime_priors.curve_from(cache)
            self.assertEqual(curve["fetched"], old.isoformat())
            self.assertNotEqual(curve["fetched"], datetime.date.today().isoformat())

    def test_main_merges_and_reports_skipped_slug(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_json(root / "data" / "priors.json",
                       {"besttime_curves": {}, "default": {}})
            bt_dir = root / "data" / "besttime"
            bt_dir.mkdir(parents=True)
            write_json(bt_dir / "central_park_tennis_center.json",
                       besttime_cache(0, [1] + [0] * 23, venue_name="Central Park Tennis Center"))
            write_json(bt_dir / "mccarren_park_tennis.json",
                       besttime_cache(1, [2] + [0] * 23, venue_name="McCarren"))
            write_json(bt_dir / "totally_unmapped_venue.json",
                       besttime_cache(0, [1] + [0] * 23))
            with patch.object(fetch_besttime_priors, "ROOT", root), capture_stdout() as out_s:
                fetch_besttime_priors.main()
            priors = json.load(open(root / "data" / "priors.json"))
            self.assertEqual(set(priors["besttime_curves"]), {"M010", "B058"})
            self.assertIn("_besttime_note", priors)
            self.assertIn("skipped (no park_id mapping): ['totally_unmapped_venue']", out_s.getvalue())

    def test_shrink_guard_refuses_write(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_json(root / "data" / "priors.json",
                       {"besttime_curves": {"M010": {"curves": {}}, "B058": {"curves": {}}}})
            bt_dir = root / "data" / "besttime"
            bt_dir.mkdir(parents=True)
            # Only one cache file provided this run -> would drop B058.
            write_json(bt_dir / "central_park_tennis_center.json",
                       besttime_cache(0, [1] + [0] * 23))
            with patch.object(fetch_besttime_priors, "ROOT", root), capture_stdout():
                with self.assertRaises(SystemExit) as cm:
                    fetch_besttime_priors.main()
            self.assertIn("would drop", str(cm.exception))
            # refused write -> priors.json unchanged
            priors = json.load(open(root / "data" / "priors.json"))
            self.assertEqual(set(priors["besttime_curves"]), {"M010", "B058"})


# ----------------------------------------------------------------------
# Group 1g: fetch_permits.py - validate() (pure) + main()
# ----------------------------------------------------------------------

class TestFetchPermitsValidate(unittest.TestCase):
    def test_valid_permits_no_errors(self):
        permits = {"fetched_at": "2026-09-14T00:00:00+00:00",
                   "days": {"2026-09-15": {"12": {"Q001A": 2}}}}
        errors, warnings = fetch_permits.validate(
            permits, {"Q001A"}, today=datetime.date(2026, 9, 14))
        self.assertEqual(errors, [])

    def test_q001_vs_q001a_mismatch_is_flagged(self):
        # The real Alley Pond bug: permits filed under "Q001" while
        # courts.geojson's facility is "Q001A" - compute_scores.get(park_id, 0)
        # silently drops those blocks, so this must be a hard error.
        permits = {"fetched_at": "2026-09-14T00:00:00+00:00",
                   "days": {"2026-09-15": {"12": {"Q001": 2}}}}
        errors, warnings = fetch_permits.validate(
            permits, {"Q001A"}, today=datetime.date(2026, 9, 14))
        self.assertTrue(any("Q001" in e and "absent from courts.geojson" in e for e in errors))

    def test_stale_coverage_warns(self):
        permits = {"fetched_at": "2026-08-01T00:00:00+00:00",
                   "days": {"2026-08-01": {"12": {"Q001A": 1}}}}
        errors, warnings = fetch_permits.validate(
            permits, {"Q001A"}, today=datetime.date(2026, 9, 14))
        self.assertEqual(errors, [])
        self.assertTrue(any("coverage ends" in w for w in warnings))

    def test_negative_or_bool_count_is_an_error(self):
        permits = {"days": {"2026-09-15": {"12": {"Q001A": -1}}}}
        errors, _ = fetch_permits.validate(permits, {"Q001A"}, today=datetime.date(2026, 9, 14))
        self.assertTrue(errors)
        permits2 = {"days": {"2026-09-15": {"12": {"Q001A": True}}}}
        errors2, _ = fetch_permits.validate(permits2, {"Q001A"}, today=datetime.date(2026, 9, 14))
        self.assertTrue(errors2)


class TestFetchPermitsMain(unittest.TestCase):
    def test_main_exits_1_on_unmatched_park_id(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_json(root / "data" / "permits.json",
                       {"fetched_at": "2026-09-14T00:00:00+00:00",
                        "days": {"2026-09-15": {"12": {"Q001": 2}}}})
            write_json(root / "data" / "courts.geojson", courts_geojson([("Q001A", -73.7, 40.7, "Alley Pond")]))
            with patch.object(fetch_permits, "ROOT", root), \
                 patch.object(fetch_permits, "PERMITS", root / "data" / "permits.json"), \
                 patch.object(fetch_permits, "COURTS", root / "data" / "courts.geojson"), \
                 capture_stdout():
                with self.assertRaises(SystemExit) as cm:
                    fetch_permits.main()
            self.assertEqual(cm.exception.code, 1)

    def test_main_ok_on_clean_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_json(root / "data" / "permits.json",
                       {"fetched_at": "2026-09-14T00:00:00+00:00",
                        "days": {"2026-09-15": {"12": {"Q001A": 2}}}})
            write_json(root / "data" / "courts.geojson", courts_geojson([("Q001A", -73.7, 40.7, "Alley Pond")]))
            with patch.object(fetch_permits, "ROOT", root), \
                 patch.object(fetch_permits, "PERMITS", root / "data" / "permits.json"), \
                 patch.object(fetch_permits, "COURTS", root / "data" / "courts.geojson"), \
                 capture_stdout() as out_s:
                fetch_permits.main()  # must not raise / exit
            self.assertIn("ok - schema and park_id joins valid", out_s.getvalue())


# ----------------------------------------------------------------------
# Group 1h: fetch_reservations.py - usable_sites(), density arithmetic
# (via a fake Playwright-shaped page, no browser), and main()'s failure path
# ----------------------------------------------------------------------

class FakeCell:
    def __init__(self, text):
        self._text = text
    def inner_text(self):
        return self._text


class FakeTable:
    def __init__(self, header_text, cell_texts, aid):
        self._header = header_text
        self._cells = [FakeCell(t) for t in cell_texts]
        self._aid = aid
    def inner_text(self):
        return self._header
    def query_selector_all(self, sel):
        assert sel == "td"
        return self._cells
    def evaluate(self, js):
        return self._aid


class FakePage:
    def __init__(self, tables):
        self._tables = tables
    def query_selector_all(self, sel):
        assert sel == "table"
        return self._tables


class TestFetchReservationsPureFunctions(unittest.TestCase):
    def test_usable_sites_checks_value_not_key_presence(self):
        # scrape() always writes the "density" key; the old bug tested key
        # presence (`"density" in r`), so a None value was still "usable".
        sites = {"a": {"density": 0.4}, "b": {"density": None}, "c": {"error": "blocked"}}
        self.assertEqual(fetch_reservations.usable_sites(sites), ["a"])

    def test_density_divides_by_whole_grid_including_not_available(self):
        header = "7:00 a.m. 8:00 a.m. Booked Reserve Not Available"
        cells = ["Booked", "Booked", "Reserve", "Not Available", "Not Available"]
        table = FakeTable(header, cells, aid="2026-09-16")
        page = FakePage([table])
        out = fetch_reservations.parse_all_days(page)
        self.assertIn("2026-09-16", out)
        rec = out["2026-09-16"]
        self.assertEqual(rec["booked"], 2)
        self.assertEqual(rec["reservable"], 1)
        self.assertEqual(rec["not_available"], 2)
        # 2/5, NOT 2/(2+1)=0.667 (the old booked+reservable-only denominator)
        self.assertAlmostEqual(rec["density"], 0.4)
        self.assertNotAlmostEqual(rec["density"], 2 / 3)


class TestFetchReservationsMain(unittest.TestCase):
    def _run(self, root, scrape_return=None, scrape_side_effect=None):
        out_path = root / "data" / "reservation_density.json"
        with patch.object(fetch_reservations, "ROOT", root), \
             patch.object(fetch_reservations, "OUT", out_path), \
             patch.object(fetch_reservations, "scrape",
                          return_value=scrape_return, side_effect=scrape_side_effect), \
             capture_stdout():
            return out_path

    def test_all_sites_unusable_leaves_existing_file_untouched_and_exits(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out_path = root / "data" / "reservation_density.json"
            sentinel = {"status": "ok", "sites": {"prior": {"density": 0.5}}}
            write_json(out_path, sentinel)
            all_none = {s: {"density": None, "error": "blocked"} for s in fetch_reservations.SITES}
            self._run(root, scrape_return=all_none)
            with patch.object(fetch_reservations, "ROOT", root), \
                 patch.object(fetch_reservations, "OUT", out_path), \
                 patch.object(fetch_reservations, "scrape", return_value=all_none), \
                 capture_stdout():
                with self.assertRaises(SystemExit) as cm:
                    fetch_reservations.main()
            self.assertEqual(cm.exception.code, 1)
            self.assertEqual(json.load(open(out_path)), sentinel)

    def test_scrape_exception_status_unavailable_no_write(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out_path = root / "data" / "reservation_density.json"
            self.assertFalse(out_path.exists())
            with patch.object(fetch_reservations, "ROOT", root), \
                 patch.object(fetch_reservations, "OUT", out_path), \
                 patch.object(fetch_reservations, "scrape", side_effect=RuntimeError("WAF block")), \
                 capture_stdout():
                with self.assertRaises(SystemExit):
                    fetch_reservations.main()
            self.assertFalse(out_path.exists())

    def test_successful_partial_capture_writes_atomically(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "data").mkdir()
            out_path = root / "data" / "reservation_density.json"
            site_ids = list(fetch_reservations.SITES)
            good = {site_ids[0]: {"density": 0.6, "park_ids": ["M010"]}}
            bad = {s: {"density": None, "error": "blocked"} for s in site_ids[1:]}
            result = {**good, **bad}
            with patch.object(fetch_reservations, "ROOT", root), \
                 patch.object(fetch_reservations, "OUT", out_path), \
                 patch.object(fetch_reservations, "scrape", return_value=result), \
                 capture_stdout():
                fetch_reservations.main()
            out = json.load(open(out_path))
            self.assertEqual(out["status"], "partial")
            self.assertIn(site_ids[0], out["sites"])


# ----------------------------------------------------------------------
# Group 2: the metadata contract - "_"-prefixed keys must be inert under
# compute_scores.py-style keyed lookups (amenities.get(park_id, {}), etc.)
# ----------------------------------------------------------------------

class TestMetadataContract(unittest.TestCase):
    def _amenities_output(self, td):
        courts = courts_geojson([("P1", -73.97, 40.78, "One")])
        write_json(Path(td) / "data" / "courts.geojson", courts)
        fake = make_fake_urlopen({"i7jb-7jku": rows([]), "qnv7-p7a2": rows([])})
        with patch.object(fetch_amenities, "ROOT", Path(td)), \
             patch("urllib.request.urlopen", side_effect=fake), capture_stdout():
            fetch_amenities.main()
        return json.load(open(Path(td) / "data" / "amenities.json"))

    def _transit_output(self, td):
        courts = courts_geojson([("P1", -73.97, 40.78, "One")])
        write_json(Path(td) / "data" / "courts.geojson", courts)
        gtfs = build_gtfs_zip([(40.7801, -73.97001, "Near P1", "1")])
        fake = make_fake_urlopen({"rrgtfsfeeds": gtfs})
        with patch.object(fetch_transit, "ROOT", Path(td)), \
             patch("urllib.request.urlopen", side_effect=fake), capture_stdout():
            fetch_transit.main()
        return json.load(open(Path(td) / "data" / "transit.json"))

    def _quality_output(self, td):
        courts = courts_geojson([("P1", -73.97, 40.78, "One")])
        write_json(Path(td) / "data" / "courts.geojson", courts)
        insp = [{"prop_id": "P1", "overall_condition": "A"}] * 3
        fake = make_fake_urlopen({"yg3y-7juh": rows(insp)})
        with patch.object(fetch_quality, "ROOT", Path(td)), \
             patch("urllib.request.urlopen", side_effect=fake), capture_stdout():
            fetch_quality.main()
        return json.load(open(Path(td) / "data" / "quality.json"))

    def test_no_park_id_collides_with_generated_at(self):
        for name, builder in [("amenities", self._amenities_output),
                              ("transit", self._transit_output),
                              ("quality", self._quality_output)]:
            with self.subTest(artifact=name):
                with tempfile.TemporaryDirectory() as td:
                    data = builder(td)
                    self.assertIn("_generated_at", data)
                    # every real park_id in this codebase starts with a
                    # borough letter (M/B/Q/X/R...), never "_"
                    for pid in data:
                        if pid != "_generated_at":
                            self.assertFalse(pid.startswith("_"))
                    # simulating compute_scores.py's amenities.get(park_id, {})
                    self.assertIsInstance(data.get("_generated_at", {}), str)
                    self.assertNotIsInstance(data.get("P1", {}), str)

    def test_iterating_values_skipping_underscore_keys_never_assumes_dict(self):
        for name, builder in [("amenities", self._amenities_output),
                              ("transit", self._transit_output),
                              ("quality", self._quality_output)]:
            with self.subTest(artifact=name):
                with tempfile.TemporaryDirectory() as td:
                    data = builder(td)
                    for k, v in data.items():
                        if k.startswith("_"):
                            continue
                        self.assertIsInstance(v, dict, f"{name}[{k!r}] should be a record dict")


# ----------------------------------------------------------------------
# Group 4: chain smoke test - courts -> amenities -> transit -> quality,
# mirroring data-refresh.yml, park_ids must join across all four outputs.
# ----------------------------------------------------------------------

class TestIngestionChain(unittest.TestCase):
    def test_courts_then_amenities_transit_quality_join(self):
        court_rows_payload = [
            {"gispropnum": "P1", "borough": "M", "surface_type": "Hard",
             "field_lighted": "true", "pickleball": False, "multipolygon": mp_square(-73.97, 40.78)},
            {"gispropnum": "P2", "borough": "B", "surface_type": "Clay",
             "field_lighted": False, "pickleball": False, "multipolygon": mp_square(-73.90, 40.70)},
        ]
        names_payload = [{"gispropnum": "P1", "name311": "Park One"},
                          {"gispropnum": "P2", "name311": "Park Two"}]
        rest_rows = [{"latitude": "40.7801", "longitude": "-73.9701",
                     "facility_name": "Near P1", "status": "Operational"}]
        fount_rows = []
        gtfs = build_gtfs_zip([(40.7801, -73.97001, "P1 Station", "1")])
        insp = [{"prop_id": "P1", "overall_condition": "A"}] * 3 + \
               [{"prop_id": "P2", "overall_condition": "A"}] * 3

        fake = make_fake_urlopen({
            "enfh-gkve": rows(names_payload),
            "qnem-b8re": rows(court_rows_payload),
            "i7jb-7jku": rows(rest_rows),
            "qnv7-p7a2": rows(fount_rows),
            "rrgtfsfeeds": gtfs,
            "yg3y-7juh": rows(insp),
        })

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "data").mkdir()
            with patch.object(fetch_courts, "ROOT", root), \
                 patch.object(fetch_amenities, "ROOT", root), \
                 patch.object(fetch_transit, "ROOT", root), \
                 patch.object(fetch_quality, "ROOT", root), \
                 patch("urllib.request.urlopen", side_effect=fake), \
                 capture_stdout():
                fetch_courts.main()
                fetch_amenities.main()
                fetch_transit.main()
                fetch_quality.main()

            courts = json.load(open(root / "data" / "courts.geojson"))
            amenities = json.load(open(root / "data" / "amenities.json"))
            transit = json.load(open(root / "data" / "transit.json"))
            quality = json.load(open(root / "data" / "quality.json"))

            court_ids = {f["properties"]["park_id"] for f in courts["features"]}
            self.assertEqual(court_ids, {"P1", "P2"})

            amenity_ids = {k for k in amenities if not k.startswith("_")}
            transit_ids = {k for k in transit if not k.startswith("_")}
            quality_ids = {k for k in quality if not k.startswith("_")}

            # amenities and quality key every facility in courts.geojson
            self.assertEqual(amenity_ids, court_ids)
            self.assertTrue(quality_ids.issubset(court_ids))
            self.assertTrue(transit_ids.issubset(court_ids))
            self.assertIn("P1", quality_ids)
            self.assertIn("P1", transit_ids)
            # all three outputs parse and every key each contributes joins
            # back to a real courts.geojson park_id
            for ids in (amenity_ids, transit_ids, quality_ids):
                self.assertTrue(ids.issubset(court_ids))
                self.assertTrue(ids)  # not vacuously empty


if __name__ == "__main__":
    unittest.main()
