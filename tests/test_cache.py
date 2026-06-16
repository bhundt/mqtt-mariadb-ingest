import sys
from datetime import datetime, timedelta
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mqtt_mariadb_ingest.cache import ReadingCache  # noqa: E402
from mqtt_mariadb_ingest.models import SensorReading  # noqa: E402


class CacheTests(unittest.TestCase):
    def test_snapshot_keeps_order_and_marks_stale_missing(self):
        now = datetime(2026, 6, 16, 12, 0, 0)
        cache = ReadingCache(["Living Room", "Bedroom"])
        cache.update(
            SensorReading(
                room="Bedroom",
                address="CD:34:30:37:22:11",
                device_type="Meter Plus",
                battery_level=62,
                temperature=21.3,
                humidity=53,
                rssi=-74,
                received_at=now,
            )
        )
        cache.update(
            SensorReading(
                room="Living Room",
                address="C1:34:30:37:35:3D",
                device_type="Meter Plus",
                battery_level=63,
                temperature=22.2,
                humidity=48,
                rssi=-72,
                received_at=now - timedelta(seconds=301),
            )
        )

        readings, missing = cache.snapshot(max_age_seconds=300, now=now)

        self.assertEqual([reading.room for reading in readings], ["Bedroom"])
        self.assertEqual(missing, ["Living Room"])


if __name__ == "__main__":
    unittest.main()
