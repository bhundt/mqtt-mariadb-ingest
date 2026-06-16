from __future__ import annotations

import logging
import re
import time
from datetime import datetime

import pymysql

from .config import AppConfig
from .models import SensorReading


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")


def quote_identifier(identifier: str) -> str:
    if not _IDENTIFIER_RE.match(identifier):
        raise ValueError(f"Unsafe SQL identifier: {identifier!r}")
    return f"`{identifier}`"


class MariaDbClient:
    def __init__(self, config: AppConfig):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.sensor_table = quote_identifier(config.db_sensor_table)
        self.alarms_table = quote_identifier(config.db_alarms_table)

    def connect(self, retries: int = 5, delay: float = 10.0):
        for attempt in range(retries):
            try:
                return pymysql.connect(
                    host=self.config.mariadb_host,
                    port=self.config.mariadb_port,
                    user=self.config.mariadb_user,
                    password=self.config.mariadb_password,
                    database=self.config.mariadb_database,
                    autocommit=False,
                    cursorclass=pymysql.cursors.Cursor,
                )
            except pymysql.OperationalError:
                if attempt == retries - 1:
                    raise
                time.sleep(delay)

    def prepare_tables(self) -> None:
        conn = self.connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self.sensor_table} (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                        room VARCHAR(255),
                        device_address VARCHAR(255),
                        device_type VARCHAR(255),
                        battery_level DOUBLE,
                        temperature DOUBLE,
                        humidity DOUBLE,
                        rssi DOUBLE NULL
                    )
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {self.alarms_table} (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                        room VARCHAR(255),
                        metric VARCHAR(255),
                        alarm_state VARCHAR(255)
                    )
                    """
                )
                self._ensure_sensor_column(cursor, "rssi", "DOUBLE NULL")
            conn.commit()
        finally:
            conn.close()

    def _ensure_sensor_column(self, cursor, column_name: str, column_definition: str) -> None:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = %s
              AND COLUMN_NAME = %s
            """,
            (self.config.mariadb_database, self.config.db_sensor_table, column_name),
        )
        result = cursor.fetchone()
        if result and result[0] > 0:
            return
        cursor.execute(
            f"ALTER TABLE {self.sensor_table} ADD COLUMN {quote_identifier(column_name)} {column_definition}"
        )

    def insert_readings(self, readings: list[SensorReading], timestamp: datetime) -> None:
        if not readings:
            return

        conn = self.connect()
        try:
            with conn.cursor() as cursor:
                cursor.executemany(
                    f"""
                    INSERT INTO {self.sensor_table}
                        (timestamp, room, device_address, device_type,
                         battery_level, temperature, humidity, rssi)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            timestamp,
                            reading.room,
                            reading.address,
                            reading.device_type,
                            reading.battery_level,
                            reading.temperature,
                            reading.humidity,
                            reading.rssi,
                        )
                        for reading in readings
                    ],
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_latest_alarm_state(self, room: str, metric: str) -> str | None:
        conn = self.connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT alarm_state
                    FROM {self.alarms_table}
                    WHERE room = %s AND metric = %s
                    ORDER BY timestamp DESC, id DESC
                    LIMIT 1
                    """,
                    (room, metric),
                )
                result = cursor.fetchone()
                return result[0] if result else None
        finally:
            conn.close()

    def set_alarm_state(self, room: str, metric: str, alarm_state: str) -> None:
        conn = self.connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {self.alarms_table}
                        (timestamp, room, metric, alarm_state)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (datetime.now(), room, metric, alarm_state),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_latest_sensor_timestamp(self) -> datetime | None:
        conn = self.connect()
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"SELECT MAX(timestamp) FROM {self.sensor_table}")
                result = cursor.fetchone()
                return result[0] if result else None
        finally:
            conn.close()
