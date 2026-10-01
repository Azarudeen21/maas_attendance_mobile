import unittest
from datetime import datetime, timedelta, timezone

from maas_attendance_mobile.punch_rules import (
    check_fresh, check_transition, parse_request, request_hash, site_time, uuid4_string,
)

REQUEST = "ad12fa40-4a3f-4ee5-a365-ff6e4dd11014"
DEVICE = "ddbc4a14-88ce-4c94-8893-a6f6b3cbd056"


class RuleTests(unittest.TestCase):
    def payload(self, **changes):
        args = dict(request_id=REQUEST, log_type="IN", timestamp="2026-10-01T19:44:28Z",
                    device_id=DEVICE, app_version="0.2.0+1")
        args.update(changes)
        return parse_request(**args)

    def test_riyadh_conversion_uses_original_instant(self):
        _, event = self.payload()
        self.assertEqual(site_time(event, "Asia/Riyadh"), datetime(2026, 10, 1, 22, 44, 28))

    def test_equivalent_offsets_share_fingerprint(self):
        a, _ = self.payload()
        b, _ = self.payload(timestamp="2026-10-01T22:44:28+03:00")
        self.assertEqual(request_hash(a, "EMP", "user"), request_hash(b, "EMP", "user"))

    def test_hash_binds_identity_and_every_field(self):
        a, _ = self.payload()
        base = request_hash(a, "EMP", "user")
        for key, value in (("log_type", "OUT"), ("app_version", "2"), ("device_id", REQUEST)):
            self.assertNotEqual(base, request_hash(dict(a, **{key: value}), "EMP", "user"))
        self.assertNotEqual(base, request_hash(a, "OTHER", "user"))
        self.assertNotEqual(base, request_hash(a, "EMP", "other"))

    def test_ids_require_uuid4(self):
        for invalid in (None, 123, "abc", REQUEST.upper(), "00000000-0000-0000-0000-000000000000"):
            with self.assertRaises(ValueError):
                uuid4_string(invalid)

    def test_rejects_ambiguous_dates_and_bad_types(self):
        for value in (None, "2026-10-01 19:44:28", "2026-10-01T19:44:28", "2026-13-01T19:44:28Z"):
            with self.assertRaises(ValueError):
                self.payload(timestamp=value)
        for value in (None, "in", "OTHER"):
            with self.assertRaises(ValueError):
                self.payload(log_type=value)
        for value in (None, "a" * 41, "<script>"):
            with self.assertRaises(ValueError):
                self.payload(app_version=value)

    def test_online_freshness_boundaries(self):
        _, event = self.payload()
        check_fresh(event, event + timedelta(minutes=15))
        check_fresh(event, event - timedelta(minutes=2))
        for now in (event + timedelta(minutes=15, seconds=1), event - timedelta(minutes=2, seconds=1)):
            with self.assertRaises(ValueError):
                check_fresh(event, now)

    def test_state_sequence_and_repeated_punch_rejection(self):
        now = datetime(2026, 10, 1, 22, 44, 28)
        check_transition(None, "IN", now)
        with self.assertRaises(ValueError):
            check_transition(None, "OUT", now)
        for previous, requested in (("IN", "OUT"), ("OUT", "IN")):
            row = {"log_type": previous, "time": now - timedelta(seconds=1)}
            check_transition(row, requested, now)
            with self.assertRaises(ValueError):
                check_transition(row, previous, now)

    def test_out_of_order_equal_and_unknown_logs_fail_closed(self):
        now = datetime(2026, 10, 1, 22, 44, 28)
        for value in (now, now + timedelta(seconds=1)):
            with self.assertRaises(ValueError):
                check_transition({"log_type": "IN", "time": value}, "OUT", now)
        with self.assertRaises(ValueError):
            check_transition({"log_type": None, "time": now}, "IN", now)


if __name__ == "__main__":
    unittest.main()
