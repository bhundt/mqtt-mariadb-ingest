from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .models import RoomConfig
from .parser import normalize_device_id


def load_dotenv(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


@dataclass(frozen=True)
class AppConfig:
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str | None
    mqtt_password: str | None
    mqtt_topic: str
    mqtt_client_id: str
    mariadb_host: str
    mariadb_port: int
    mariadb_user: str
    mariadb_password: str
    mariadb_database: str
    ntfy_base_url: str
    ntfy_topic: str
    send_notifications: bool
    room_config_path: str
    write_interval_seconds: int
    reading_max_age_seconds: int
    freshness_threshold_seconds: int
    battery_lower_limit: float
    db_sensor_table: str
    db_alarms_table: str
    db_create_tables: bool
    log_level: str
    rooms: tuple[RoomConfig, ...]


def load_rooms(path: str) -> tuple[RoomConfig, ...]:
    data = json.loads(Path(path).read_text())
    rooms = []
    for room in data["rooms"]:
        rooms.append(
            RoomConfig(
                name=room["name"],
                mqtt_ids=tuple(normalize_device_id(item) for item in room["mqtt_ids"]),
                device_type=room["device_type"],
                temp_lower_limit=float(room["temp_lower_limit"]),
                humidity_upper_limit=float(room["humidity_upper_limit"]),
            )
        )
    return tuple(rooms)


def load_config() -> AppConfig:
    load_dotenv()
    room_config_path = os.environ.get("ROOM_CONFIG_PATH", "config/rooms.json")
    rooms = load_rooms(room_config_path)

    return AppConfig(
        mqtt_host=os.environ.get("MQTT_HOST", "192.168.178.100"),
        mqtt_port=_env_int("MQTT_PORT", 1883),
        mqtt_username=os.environ.get("MQTT_USERNAME") or None,
        mqtt_password=os.environ.get("MQTT_PASSWORD") or None,
        mqtt_topic=os.environ.get("MQTT_TOPIC", "home/OpenMQTTGateway_ESP32C3_DKC02/BTtoMQTT/#"),
        mqtt_client_id=os.environ.get("MQTT_CLIENT_ID", "mqtt-mariadb-ingest"),
        mariadb_host=os.environ.get("MARIADB_HOST", "192.168.178.100"),
        mariadb_port=_env_int("MARIADB_PORT", 3306),
        mariadb_user=os.environ.get("MARIADB_USER", "dbuser"),
        mariadb_password=os.environ.get("MARIADB_PASSWORD", ""),
        mariadb_database=os.environ.get("MARIADB_DATABASE", "database"),
        ntfy_base_url=os.environ.get("NTFY_BASE_URL", "https://ntfy.sh"),
        ntfy_topic=os.environ.get("NTFY_TOPIC", ""),
        send_notifications=_env_bool("SEND_NOTIFICATIONS", False),
        room_config_path=room_config_path,
        write_interval_seconds=_env_int("WRITE_INTERVAL_SECONDS", 300),
        reading_max_age_seconds=_env_int("READING_MAX_AGE_SECONDS", 300),
        freshness_threshold_seconds=_env_int("FRESHNESS_THRESHOLD_SECONDS", 900),
        battery_lower_limit=float(os.environ.get("BATTERY_LOWER_LIMIT", "20")),
        db_sensor_table=os.environ.get("DB_SENSOR_TABLE", "sensor_data_mqtt_shadow"),
        db_alarms_table=os.environ.get("DB_ALARMS_TABLE", "alarms_mqtt_shadow"),
        db_create_tables=_env_bool("DB_CREATE_TABLES", True),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        rooms=rooms,
    )

