from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from .models import AlertEvent, RoomConfig, SensorReading


class AlarmStore(Protocol):
    def get_latest_alarm_state(self, room: str, metric: str) -> str | None:
        ...

    def set_alarm_state(self, room: str, metric: str, alarm_state: str) -> None:
        ...


class Notifier(Protocol):
    def send(self, message: str, title: str = "", is_warning: bool = False) -> None:
        ...


class AlarmEvaluator:
    def __init__(
        self,
        rooms: tuple[RoomConfig, ...],
        battery_lower_limit: float,
        store: AlarmStore,
        notifier: Notifier,
    ):
        self.rooms = {room.name: room for room in rooms}
        self.battery_lower_limit = battery_lower_limit
        self.store = store
        self.notifier = notifier

    def evaluate(self, readings: list[SensorReading]) -> list[AlertEvent]:
        events: list[AlertEvent] = []
        for reading in readings:
            room = self.rooms.get(reading.room)
            if room is None:
                continue
            events.extend(
                [
                    self._transition_event(
                        reading.room,
                        "battery",
                        reading.battery_level < self.battery_lower_limit,
                        f"Low battery level in {reading.room}: {reading.battery_level:g}%",
                        f"Battery level back to normal in {reading.room}: {reading.battery_level:g}% 🥳",
                    ),
                    self._transition_event(
                        reading.room,
                        "temperature",
                        reading.temperature < room.temp_lower_limit,
                        f"Low temperature in {reading.room}: {reading.temperature:g}°C",
                        f"Temperature back to normal in {reading.room}: {reading.temperature:g}°C 🥳",
                    ),
                    self._transition_event(
                        reading.room,
                        "humidity",
                        reading.humidity > room.humidity_upper_limit,
                        f"High humidity in {reading.room}: {reading.humidity:g}%",
                        f"Humidity back to normal in {reading.room}: {reading.humidity:g}% 🥳",
                    ),
                ]
            )
        return [event for event in events if event is not None]

    def _transition_event(
        self,
        room: str,
        metric: str,
        in_alarm: bool,
        alarm_message: str,
        recovery_message: str,
    ) -> AlertEvent | None:
        new_state = "ALARM" if in_alarm else "OK"
        old_state = self.store.get_latest_alarm_state(room, metric)

        if new_state == "ALARM" and old_state in {"OK", None}:
            self.store.set_alarm_state(room, metric, new_state)
            self.notifier.send(alarm_message, "Wohnung", True)
            return AlertEvent(room, metric, new_state, alarm_message, True)

        if new_state == "OK" and old_state == "ALARM":
            self.store.set_alarm_state(room, metric, new_state)
            self.notifier.send(recovery_message, "Wohnung", False)
            return AlertEvent(room, metric, new_state, recovery_message, False)

        return None


class MissingDataNotifier:
    def __init__(self, threshold_seconds: int, notifier: Notifier):
        self.threshold = timedelta(seconds=threshold_seconds)
        self.notifier = notifier
        self._missing_since: dict[str, datetime] = {}
        self._notified_rooms: frozenset[str] = frozenset()

    def evaluate(
        self,
        missing_rooms: list[str],
        reading_count: int,
        now: datetime,
    ) -> str | None:
        missing_set = set(missing_rooms)
        for room in missing_rooms:
            self._missing_since.setdefault(room, now)
        for room in list(self._missing_since):
            if room not in missing_set:
                del self._missing_since[room]

        if not missing_rooms:
            if self._notified_rooms:
                self._notified_rooms = frozenset()
                message = "Sensor data complete again."
                self.notifier.send(message)
                return message
            return None

        notification_rooms = frozenset(
            room
            for room in missing_rooms
            if now - self._missing_since[room] >= self.threshold
        )
        if not notification_rooms or notification_rooms == self._notified_rooms:
            return None

        self._notified_rooms = notification_rooms
        ordered_rooms = [room for room in missing_rooms if room in notification_rooms]
        message = (
            f"Data incomplete: only {reading_count} devices found. "
            f"Missing data for {ordered_rooms}"
        )
        self.notifier.send(message)
        return message
