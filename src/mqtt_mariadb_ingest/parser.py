from __future__ import annotations

import json
import re
from typing import Any

from .models import NormalizedPayload


class PayloadParseError(ValueError):
    """Raised when an MQTT payload cannot be converted to a sensor reading."""


def normalize_device_id(value: str) -> str:
    stripped = re.sub(r"[^0-9A-Fa-f]", "", value or "")
    if len(stripped) != 12:
        raise PayloadParseError(f"Invalid device id: {value!r}")
    return ":".join(stripped[i : i + 2] for i in range(0, 12, 2)).upper()


def normalize_device_type(model: str) -> str:
    normalized = (model or "").strip()
    aliases = {
        "Meter (Plus)": "Meter Plus",
        "SwitchBot Meter Plus": "Meter Plus",
    }
    return aliases.get(normalized, normalized)


def _required_number(payload: dict[str, Any], key: str) -> float:
    if key not in payload:
        raise PayloadParseError(f"Missing required field: {key}")
    try:
        return float(payload[key])
    except (TypeError, ValueError) as exc:
        raise PayloadParseError(f"Invalid numeric field {key}: {payload[key]!r}") from exc


def _optional_number(payload: dict[str, Any], key: str) -> float | None:
    if key not in payload or payload[key] is None:
        return None
    try:
        return float(payload[key])
    except (TypeError, ValueError) as exc:
        raise PayloadParseError(f"Invalid numeric field {key}: {payload[key]!r}") from exc


def parse_openmqttgateway_payload(raw_payload: bytes | str) -> NormalizedPayload:
    if isinstance(raw_payload, bytes):
        raw_payload = raw_payload.decode("utf-8")

    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise PayloadParseError(f"Invalid JSON payload: {exc}") from exc

    if not isinstance(payload, dict):
        raise PayloadParseError("MQTT payload must be a JSON object")

    raw_address = payload.get("id") or payload.get("mac")
    if not raw_address:
        raise PayloadParseError("Missing required field: id")

    model = payload.get("model")
    if not model:
        raise PayloadParseError("Missing required field: model")

    return NormalizedPayload(
        address=normalize_device_id(str(raw_address)),
        device_type=normalize_device_type(str(model)),
        temperature=_required_number(payload, "tempc"),
        humidity=_required_number(payload, "hum"),
        battery_level=_required_number(payload, "batt"),
        rssi=_optional_number(payload, "rssi"),
    )
