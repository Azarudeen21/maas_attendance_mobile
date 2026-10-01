"""Pure validation rules for the online test checkpoint."""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo


def uuid4_string(value):
    if not isinstance(value, str):
        raise ValueError("A UUID v4 is required.")
    try:
        parsed = UUID(value)
    except ValueError:
        raise ValueError("A UUID v4 is required.") from None
    if parsed.version != 4 or str(parsed) != value:
        raise ValueError("A canonical lowercase UUID v4 is required.")
    return value


def parse_request(request_id, log_type, timestamp, device_id, app_version):
    uuid4_string(request_id)
    uuid4_string(device_id)
    if log_type not in ("IN", "OUT"):
        raise ValueError("Log type must be IN or OUT.")
    if not isinstance(app_version, str) or not re.fullmatch(r"[0-9A-Za-z.+_-]{1,40}", app_version):
        raise ValueError("A valid app version is required.")
    # Require an explicit offset; never interpret an ambiguous local phone time.
    if not isinstance(timestamp, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", timestamp
    ):
        raise ValueError("Timestamp must be ISO 8601 with a timezone offset.")
    event = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(timezone.utc)
    return {
        "request_id": request_id, "log_type": log_type,
        "timestamp_utc": event.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "device_id": device_id, "app_version": app_version,
    }, event


def request_hash(payload, employee, user):
    value = dict(payload, employee=employee, user=user)
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def check_fresh(event, now):
    if event < now - timedelta(minutes=15) or event > now + timedelta(minutes=2):
        raise ValueError("Online punches must be recent. Check the phone clock and retry.")


def site_time(event, timezone_name):
    # Match HRMS Employee Checkin's second-resolution, naive site-local time.
    return event.astimezone(ZoneInfo(timezone_name)).replace(tzinfo=None, microsecond=0)


def check_transition(latest, log_type, event_local):
    if not latest:
        if log_type != "IN":
            raise ValueError("Check in before checking out.")
        return
    if latest["log_type"] not in ("IN", "OUT"):
        raise ValueError("The latest checkin has no IN/OUT type. Ask HR to review it.")
    if log_type == latest["log_type"]:
        raise ValueError("Already checked in." if log_type == "IN" else "Already checked out.")
    previous = latest["time"]
    if isinstance(previous, str):
        previous = datetime.fromisoformat(previous)
    if event_local <= previous:
        raise ValueError("The punch time must be later than the previous checkin.")
