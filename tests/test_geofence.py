import unittest
from datetime import datetime, timedelta, timezone

from maas_attendance_mobile.geofence import distance_m, evaluate, parse_location, workplace_values

NOW = datetime(2026, 10, 2, 6, tzinfo=timezone.utc)
GPS = dict(latitude=24.7, longitude=46.7, accuracy_m=5, timestamp=NOW.isoformat(), precise=True, is_mocked=False)
SITE = dict(name="Office", latitude=24.7, longitude=46.7, radius_m=100, max_accuracy_m=30)


class GeofenceTests(unittest.TestCase):
    def test_location_normalized_and_json_supported(self):
        import json
        parsed, at = parse_location(json.dumps(GPS))
        self.assertEqual(at, NOW)
        self.assertEqual(parsed["timestamp"], "2026-10-02T06:00:00.000000Z")

    def test_invalid_location_is_rejected(self):
        for changes in [dict(latitude=91), dict(longitude=-181), dict(accuracy_m=-1),
                        dict(latitude=float("nan")), dict(accuracy_m=True), dict(precise="true"),
                        dict(timestamp="2026-10-02T06:00:00"), dict(is_mocked=0)]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                parse_location(dict(GPS, **changes))
        with self.assertRaises(ValueError):
            parse_location(dict(GPS, employee="OTHER"))

    def test_approved_centre_and_reproducible_selection(self):
        gps, at = parse_location(GPS)
        result = evaluate(gps, at, [dict(SITE, name="Z"), SITE], NOW, NOW)
        self.assertEqual(result["workplace"], "Office")
        self.assertEqual(result["distance_m"], 0)

    def test_no_workplace_outside_or_uncertain_rejected(self):
        for sites, changes in [([], {}), ([SITE], dict(latitude=25)),
                               ([SITE], dict(accuracy_m=31)), ([SITE], dict(accuracy_m=0)),
                               ([dict(SITE, radius_m=10)], dict(accuracy_m=11))]:
            gps, at = parse_location(dict(GPS, **changes))
            with self.assertRaises(ValueError):
                evaluate(gps, at, sites, NOW, NOW)

    def test_approximate_mocked_stale_future_and_mismatched_time_rejected(self):
        for changes in [dict(precise=False), dict(is_mocked=True),
                        dict(timestamp=(NOW-timedelta(seconds=31)).isoformat()),
                        dict(timestamp=(NOW+timedelta(seconds=6)).isoformat())]:
            gps, at = parse_location(dict(GPS, **changes))
            with self.assertRaises(ValueError):
                evaluate(gps, at, [SITE], NOW, NOW)
        gps, at = parse_location(GPS)
        with self.assertRaises(ValueError):
            evaluate(gps, at, [SITE], NOW, NOW-timedelta(minutes=2))

    def test_distance_handles_date_line_and_equator(self):
        self.assertAlmostEqual(distance_m(0, 179.999, 0, -179.999), 222.39, places=1)
        self.assertEqual(distance_m(0, 0, 0, 0), 0)
        self.assertAlmostEqual(distance_m(0, 0, 0, 180), 20015114.44, places=1)

    def test_invalid_workplace_config_rejected(self):
        for changes in [dict(radius_m=9), dict(max_accuracy_m=201), dict(latitude=91)]:
            with self.assertRaises(ValueError):
                workplace_values(dict(SITE, **changes))


if __name__ == "__main__":
    unittest.main()
