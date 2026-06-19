from __future__ import annotations

import logging
import signal
import time
from datetime import datetime, timedelta
from threading import Event

import paho.mqtt.client as mqtt

from .alerts import AlarmEvaluator, MissingDataNotifier
from .cache import ReadingCache
from .config import AppConfig, load_config
from .db import MariaDbClient
from .models import NormalizedPayload, RoomConfig, SensorReading
from .notifications import NtfyNotifier
from .parser import PayloadParseError, parse_openmqttgateway_payload


class IngestionService:
    def __init__(self, config: AppConfig):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.stop_event = Event()
        self.rooms_by_id = self._build_room_lookup(config.rooms)
        self.cache = ReadingCache([room.name for room in config.rooms])
        self.db = MariaDbClient(config)
        self.notifier = NtfyNotifier(
            config.ntfy_base_url,
            config.ntfy_topic,
            config.send_notifications,
        )
        self.alarm_evaluator = AlarmEvaluator(
            config.rooms,
            config.battery_lower_limit,
            self.db,
            self.notifier,
        )
        self.missing_data_notifier = MissingDataNotifier(
            config.notification_threshold_seconds,
            self.notifier,
        )
        self._freshness_problem_since: datetime | None = None
        self._freshness_notified = False
        self.mqtt_client = self._build_mqtt_client()

    @staticmethod
    def _build_room_lookup(rooms: tuple[RoomConfig, ...]) -> dict[str, RoomConfig]:
        lookup: dict[str, RoomConfig] = {}
        for room in rooms:
            for device_id in room.mqtt_ids:
                lookup[device_id] = room
        return lookup

    def _build_mqtt_client(self):
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.config.mqtt_client_id,
        )
        if self.config.mqtt_username is not None:
            client.username_pw_set(self.config.mqtt_username, self.config.mqtt_password)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect
        return client

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties) -> None:
        if getattr(reason_code, "is_failure", False):
            self.logger.error("MQTT connection failed: %s", reason_code)
            return

        self.logger.info("Connected to MQTT broker")
        client.subscribe(self.config.mqtt_topic)
        self.logger.info("Subscribed to %s", self.config.mqtt_topic)

    def _on_disconnect(self, _client, _userdata, _flags, reason_code, _properties) -> None:
        self.logger.warning("Disconnected from MQTT broker: %s", reason_code)

    def _on_message(self, _client, _userdata, message) -> None:
        try:
            payload = parse_openmqttgateway_payload(message.payload)
            reading = self._payload_to_reading(payload, datetime.now())
        except PayloadParseError as exc:
            self.logger.debug("Ignored MQTT message on %s: %s", message.topic, exc)
            return

        if reading is None:
            self.logger.debug("Ignored unknown device on %s", message.topic)
            return

        self.cache.update(reading)
        self.logger.info(
            "Cached %s: %.1f C, %.0f%% humidity, %.0f%% battery, RSSI %s",
            reading.room,
            reading.temperature,
            reading.humidity,
            reading.battery_level,
            "n/a" if reading.rssi is None else f"{reading.rssi:g}",
        )

    def _payload_to_reading(
        self,
        payload: NormalizedPayload,
        received_at: datetime,
    ) -> SensorReading | None:
        room = self.rooms_by_id.get(payload.address)
        if room is None:
            return None

        return SensorReading(
            room=room.name,
            address=payload.address,
            device_type=room.device_type or payload.device_type,
            battery_level=payload.battery_level,
            temperature=payload.temperature,
            humidity=payload.humidity,
            rssi=payload.rssi,
            received_at=received_at,
        )

    def run(self) -> None:
        if self.config.db_create_tables:
            self.db.prepare_tables()
            self.logger.info(
                "Ensured MariaDB tables exist: %s, %s",
                self.config.db_sensor_table,
                self.config.db_alarms_table,
            )

        self.mqtt_client.connect(self.config.mqtt_host, self.config.mqtt_port)
        self.mqtt_client.loop_start()

        next_flush = time.monotonic() + self.config.write_interval_seconds
        try:
            while not self.stop_event.is_set():
                wait_seconds = max(0.0, next_flush - time.monotonic())
                if self.stop_event.wait(wait_seconds):
                    break
                try:
                    self.flush_once()
                except Exception as exc:
                    self.logger.exception("Ingestion flush failed")
                    self.notifier.send(
                        f"MQTT MariaDB ingestion flush failed: {exc}",
                        title="MQTT MariaDB Ingest",
                        is_warning=True,
                    )
                next_flush += self.config.write_interval_seconds
        finally:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()

    def flush_once(self) -> None:
        now = datetime.now()
        db_now = self.db.get_current_timestamp()
        readings, missing_rooms = self.cache.snapshot(
            self.config.reading_max_age_seconds,
            now,
        )

        if missing_rooms:
            self.logger.warning(
                "Data incomplete: only %d devices found. Missing data for %s",
                len(readings),
                missing_rooms,
            )

        missing_message = self.missing_data_notifier.evaluate(
            missing_rooms,
            len(readings),
            now,
        )
        if missing_message:
            self.logger.error(missing_message)

        if readings:
            self.db.insert_readings(readings, db_now)
            self.logger.info(
                "Inserted %d readings into %s",
                len(readings),
                self.config.db_sensor_table,
            )
            self.alarm_evaluator.evaluate(readings)

        if len(missing_rooms) < len(self.config.rooms):
            self._check_freshness(db_now)

    def _check_freshness(self, now: datetime) -> None:
        latest_timestamp = self.db.get_latest_sensor_timestamp()
        if latest_timestamp is None:
            self._notify_freshness_problem_after_delay(
                now,
                f"No data found in {self.config.db_sensor_table} table.",
            )
            return

        if isinstance(latest_timestamp, str):
            latest_dt = datetime.fromisoformat(latest_timestamp)
        else:
            latest_dt = latest_timestamp

        threshold = timedelta(seconds=self.config.notification_threshold_seconds)
        if now - latest_dt > threshold:
            self._freshness_problem_since = latest_dt + threshold
            message = (
                "No temperature and humidity data found within the last "
                f"{int(self.config.notification_threshold_seconds / 60)} minute(s)."
            )
            self._send_freshness_problem_once(message)
            return

        if self._freshness_notified:
            message = "Sensor database fresh again."
            self.notifier.send(message)
            self.logger.info(message)
        self._freshness_problem_since = None
        self._freshness_notified = False

    def _notify_freshness_problem_after_delay(self, now: datetime, message: str) -> None:
        if self._freshness_problem_since is None:
            self._freshness_problem_since = now
        if now - self._freshness_problem_since < timedelta(
            seconds=self.config.notification_threshold_seconds
        ):
            return
        self._send_freshness_problem_once(message)

    def _send_freshness_problem_once(self, message: str) -> None:
        if self._freshness_notified:
            return
        self._freshness_notified = True
        self.notifier.send(message)
        self.logger.error(message)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    config = load_config()
    configure_logging(config.log_level)
    service = IngestionService(config)

    def stop(_signum, _frame) -> None:
        logging.getLogger(__name__).info("Shutdown requested")
        service.stop_event.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    service.run()
