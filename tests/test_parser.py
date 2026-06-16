import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mqtt_mariadb_ingest.parser import (  # noqa: E402
    PayloadParseError,
    normalize_device_id,
    parse_openmqttgateway_payload,
)


class ParserTests(unittest.TestCase):
    def test_parses_meter_plus_payload(self):
        payload = (
            '{"id":"C1:34:30:37:35:3D","rssi":-72,"brand":"SwitchBot",'
            '"model":"Meter (Plus)","model_id":"THX1/W230150X","type":"THB",'
            '"tempc":22.2,"tempf":71.96,"hum":48,"batt":63}'
        )

        reading = parse_openmqttgateway_payload(payload)

        self.assertEqual(reading.address, "C1:34:30:37:35:3D")
        self.assertEqual(reading.device_type, "Meter Plus")
        self.assertEqual(reading.temperature, 22.2)
        self.assertEqual(reading.humidity, 48)
        self.assertEqual(reading.battery_level, 63)
        self.assertEqual(reading.rssi, -72)

    def test_parses_outdoor_payload_with_mac_field(self):
        payload = (
            '{"id":"CA:5F:45:46:46:24","rssi":-69,"brand":"SwitchBot",'
            '"model":"Outdoor Meter","model_id":"W340001X","type":"THB",'
            '"tempc":22.9,"tempf":73.22,"hum":44,"batt":79,'
            '"mac":"CA:5F:45:46:46:24"}'
        )

        reading = parse_openmqttgateway_payload(payload)

        self.assertEqual(reading.address, "CA:5F:45:46:46:24")
        self.assertEqual(reading.device_type, "Outdoor Meter")

    def test_normalizes_topic_suffix_id(self):
        self.assertEqual(normalize_device_id("C1343037353D"), "C1:34:30:37:35:3D")

    def test_rejects_missing_temperature(self):
        with self.assertRaises(PayloadParseError):
            parse_openmqttgateway_payload('{"id":"C1:34:30:37:35:3D","model":"Meter (Plus)"}')

    def test_rssi_is_optional(self):
        payload = (
            '{"id":"C1:34:30:37:35:3D","brand":"SwitchBot",'
            '"model":"Meter (Plus)","tempc":22.2,"hum":48,"batt":63}'
        )

        reading = parse_openmqttgateway_payload(payload)

        self.assertIsNone(reading.rssi)


if __name__ == "__main__":
    unittest.main()
