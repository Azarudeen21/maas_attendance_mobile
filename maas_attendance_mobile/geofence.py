"""Server geofence rules. Phone coordinates are claims, not proof of presence."""

import json
import math
from datetime import datetime, timedelta, timezone


def number(value, minimum, maximum, label):
    if isinstance(value, bool):
        raise ValueError(f"Invalid {label}.")
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"Invalid {label}.") from None
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"Invalid {label}.")
    return value


def workplace_values(row):
    return {
        "latitude": number(row.get("latitude"), -90, 90, "workplace latitude"),
        "longitude": number(row.get("longitude"), -180, 180, "workplace longitude"),
        "radius_m": number(row.get("radius_m"), 10, 10000, "workplace radius"),
        "max_accuracy_m": number(row.get("max_accuracy_m"), 1, 200, "maximum accuracy"),
    }


def parse_location(value):
    if isinstance(value, str):
        if len(value) > 2000:
            raise ValueError("Invalid location payload.")
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            raise ValueError("Invalid location payload.") from None
    fields = {"latitude", "longitude", "accuracy_m", "timestamp", "precise", "is_mocked"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("A complete GPS location is required.")
    stamp = value["timestamp"]
    if not isinstance(stamp, str) or len(stamp) > 40:
        raise ValueError("Invalid GPS timestamp.")
    try:
        event = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if event.tzinfo is None:
            raise ValueError()
        event = event.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ValueError("GPS timestamp requires a timezone offset.") from None
    if type(value["precise"]) is not bool or type(value["is_mocked"]) is not bool:
        raise ValueError("Invalid GPS permission or simulation flag.")
    return {
        "latitude": number(value["latitude"], -90, 90, "latitude"),
        "longitude": number(value["longitude"], -180, 180, "longitude"),
        "accuracy_m": number(value["accuracy_m"], 0, 100000, "GPS accuracy"),
        "timestamp": event.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "precise": value["precise"], "is_mocked": value["is_mocked"],
    }, event


def distance_m(latitude, longitude, centre_latitude, centre_longitude):
    a, b = math.radians(latitude), math.radians(centre_latitude)
    lat = b - a
    lon = math.radians(centre_longitude - longitude)
    h = math.sin(lat / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(lon / 2) ** 2
    return 6371008.8 * 2 * math.asin(math.sqrt(min(1, max(0, h))))


def evaluate(location, sampled_at, workplaces, now, punch_at):
    if not location["precise"]:
        raise ValueError("Precise location permission is required.")
    if location["is_mocked"]:
        raise ValueError("Simulated location is not accepted for a GPS punch.")
    if sampled_at < now - timedelta(seconds=30) or sampled_at > now + timedelta(seconds=5):
        raise ValueError("Capture a fresh GPS location before punching.")
    if abs((sampled_at - punch_at).total_seconds()) > 30:
        raise ValueError("GPS capture must match the punch time.")
    if not workplaces:
        raise ValueError("No enabled workplace is configured for your company. Contact HR.")
    matches = []
    for row in workplaces:
        centre = workplace_values(row)
        distance = distance_m(location["latitude"], location["longitude"], centre["latitude"], centre["longitude"])
        # Require the reported uncertainty circle to fit inside the boundary.
        if 0 < location["accuracy_m"] <= centre["max_accuracy_m"] and distance + location["accuracy_m"] <= centre["radius_m"]:
            matches.append({"workplace": row["name"], "distance_m": round(distance, 3), "accuracy_m": location["accuracy_m"]})
    if not matches:
        raise ValueError("GPS is outside the workplace boundary or its accuracy is insufficient.")
    return min(matches, key=lambda item: (item["distance_m"], item["workplace"]))
