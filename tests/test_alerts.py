import sys
from datetime import datetime
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mqtt_mariadb_ingest.alerts import AlarmEvaluator  # noqa: E402
from mqtt_mariadb_ingest.models import RoomConfig, SensorReading  # noqa: E402


class FakeStore:
    def __init__(self):
        self.states = {}
        self.writes = []

    def get_latest_alarm_state(self, room, metric):
        return self.states.get((room, metric))

    def set_alarm_state(self, room, metric, alarm_state):
        self.states[(room, metric)] = alarm_state
        self.writes.append((room, metric, alarm_state))


class FakeNotifier:
    def __init__(self):
        self.messages = []

    def send(self, message, title="", is_warning=False):
        self.messages.append((message, title, is_warning))


class AlertTests(unittest.TestCase):
    def setUp(self):
        self.room = RoomConfig(
            name="Living Room",
            mqtt_ids=("C1:34:30:37:35:3D",),
            device_type="Meter Plus",
            temp_lower_limit=18.0,
            humidity_upper_limit=60.0,
        )
        self.store = FakeStore()
        self.notifier = FakeNotifier()
        self.evaluator = AlarmEvaluator((self.room,), 20, self.store, self.notifier)

    def reading(self, battery=63, temperature=22.2, humidity=48):
        return SensorReading(
            room="Living Room",
            address="C1:34:30:37:35:3D",
            device_type="Meter Plus",
            battery_level=battery,
            temperature=temperature,
            humidity=humidity,
            rssi=-72,
            received_at=datetime(2026, 6, 16, 12, 0, 0),
        )

    def test_low_battery_alert_only_on_transition(self):
        events = self.evaluator.evaluate([self.reading(battery=10)])
        repeated = self.evaluator.evaluate([self.reading(battery=9)])

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].metric, "battery")
        self.assertEqual(repeated, [])
        self.assertEqual(len(self.notifier.messages), 1)
        self.assertEqual(
            self.notifier.messages[0],
            ("Low battery level in Living Room: 10%", "Wohnung", True),
        )

    def test_recovery_alert_after_alarm(self):
        self.store.states[("Living Room", "temperature")] = "ALARM"

        events = self.evaluator.evaluate([self.reading(temperature=20)])

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].metric, "temperature")
        self.assertEqual(events[0].alarm_state, "OK")
        self.assertFalse(events[0].is_warning)
        self.assertEqual(
            self.notifier.messages[0],
            ("Temperature back to normal in Living Room: 20°C 🥳", "Wohnung", False),
        )

    def test_battery_recovery_uses_old_celebration_emoji(self):
        self.store.states[("Living Room", "battery")] = "ALARM"

        events = self.evaluator.evaluate([self.reading(battery=63)])

        self.assertEqual(len(events), 1)
        self.assertEqual(
            self.notifier.messages[0],
            ("Battery level back to normal in Living Room: 63% 🥳", "Wohnung", False),
        )

    def test_humidity_recovery_uses_old_celebration_emoji(self):
        self.store.states[("Living Room", "humidity")] = "ALARM"

        events = self.evaluator.evaluate([self.reading(humidity=48)])

        self.assertEqual(len(events), 1)
        self.assertEqual(
            self.notifier.messages[0],
            ("Humidity back to normal in Living Room: 48% 🥳", "Wohnung", False),
        )


if __name__ == "__main__":
    unittest.main()
