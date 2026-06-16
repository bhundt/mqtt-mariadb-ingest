from __future__ import annotations

from datetime import datetime, timedelta
from threading import Lock

from .models import SensorReading


class ReadingCache:
    def __init__(self, room_names: list[str]):
        self._room_names = room_names
        self._readings: dict[str, SensorReading] = {}
        self._lock = Lock()

    def update(self, reading: SensorReading) -> None:
        with self._lock:
            self._readings[reading.room] = reading

    def snapshot(self, max_age_seconds: int, now: datetime) -> tuple[list[SensorReading], list[str]]:
        min_received_at = now - timedelta(seconds=max_age_seconds)
        with self._lock:
            fresh = [
                reading
                for reading in self._readings.values()
                if reading.received_at >= min_received_at
            ]

        fresh_by_room = {reading.room: reading for reading in fresh}
        ordered_readings = [
            fresh_by_room[room_name]
            for room_name in self._room_names
            if room_name in fresh_by_room
        ]
        missing_rooms = [
            room_name
            for room_name in self._room_names
            if room_name not in fresh_by_room
        ]
        return ordered_readings, missing_rooms

