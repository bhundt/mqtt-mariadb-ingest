from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RoomConfig:
    name: str
    mqtt_ids: tuple[str, ...]
    device_type: str
    temp_lower_limit: float
    humidity_upper_limit: float


@dataclass(frozen=True)
class NormalizedPayload:
    address: str
    device_type: str
    temperature: float
    humidity: float
    battery_level: float
    rssi: float | None


@dataclass(frozen=True)
class SensorReading:
    room: str
    address: str
    device_type: str
    battery_level: float
    temperature: float
    humidity: float
    rssi: float | None
    received_at: datetime


@dataclass(frozen=True)
class AlertEvent:
    room: str
    metric: str
    alarm_state: str
    message: str
    is_warning: bool
