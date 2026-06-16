# AGENTS.md

## Project Context

This repository is for migrating SwitchBot sensor ingestion from a Raspberry Pi BLE collector to an MQTT-based ingestion service.

Current architecture:

```text
SwitchBot sensors
-> Raspberry Pi BLE Python scripts in old/
-> MariaDB on Unraid/nas02
-> Grafana
```

Target architecture:

```text
SwitchBot sensors
-> ESP32 / OpenMQTTGateway
-> Mosquitto
-> new ingestion service
-> MariaDB
-> Grafana
```

Read `plan.md` before implementing migration work.

Confirmed runtime facts:

- Mosquitto: `192.168.178.100:1883`
- Mosquitto authentication: username and password required
- MQTT topic: `home/+/BTtoMQTT/#`
- Write interval: 5 minutes, using latest valid reading per room within the interval
- Initial DB target: shadow tables, not production tables
- Deployment/build target: plain Docker daemon on nas02 via `DOCKER_CONTEXT=nas02`; do not assume Docker Compose is available
- Notifications: keep ntfy and use the same topic as the old implementation, unless running in a shadow-alert suppression mode

## Shell Instructions

Follow the local Codex instruction file:

```text
@/Users/bhundt/.codex/RTK.md
```

Use `rtk` as the command prefix for shell commands where practical.

Use uv for Python dependency management and execution:

```bash
uv sync
uv run python -m unittest discover -s tests
uv run python -m mqtt_mariadb_ingest
```

Use the scripts in `scripts/` for nas02 container lifecycle:

```bash
./scripts/deploy_nas02.sh
./scripts/status_nas02.sh
./scripts/logs_nas02.sh
./scripts/stop_nas02.sh
./scripts/remove_nas02.sh
./scripts/rebuild_nas02.sh
```

## Old Implementation

Important files:

- `old/read_meters.py`: BLE scan, SwitchBot parsing, database writes, alarm checks.
- `old/check_reading.py`: freshness and completeness checks against MariaDB.
- `old/config.py`: room mapping, thresholds, DB metadata.
- `old/utils.py`: ntfy notifications and MariaDB connection retry logic.
- `old/migrate_sqlite_to_mariadb.py`: historical SQLite to MariaDB migration.

Do not remove or rewrite `old/` while implementing the new service. It is the reference implementation for feature parity.

## Feature Parity Requirements

The new service must preserve:

- Existing MariaDB table shape for `sensor_data` and `alarms`.
- Grafana compatibility.
- Room mapping for Living Room, Bedroom, Kitchen, Study, Bathroom, and Outdoor.
- Temperature, humidity, and battery-level handling.
- RSSI storage for MQTT-derived readings.
- Battery warning below 20 percent.
- Room-specific low-temperature thresholds.
- Room-specific high-humidity thresholds.
- ntfy push notifications.
- Alarm transition semantics using `alarms`:
  - notify on `OK -> ALARM`
  - notify on `ALARM -> OK`
  - do not repeatedly notify while already in `ALARM`
- Completeness checks for all configured rooms.
- Freshness checks using the current 15 minute threshold.
- Shared timestamp semantics for rows written in one logical batch.

## Database Schema

Keep this schema unless the user explicitly approves a change:

```sql
CREATE TABLE IF NOT EXISTS sensor_data (
    id INT AUTO_INCREMENT PRIMARY KEY,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    room VARCHAR(255),
    device_address VARCHAR(255),
    device_type VARCHAR(255),
    battery_level DOUBLE,
    temperature DOUBLE,
    humidity DOUBLE,
    rssi DOUBLE NULL
);

CREATE TABLE IF NOT EXISTS alarms (
    id INT AUTO_INCREMENT PRIMARY KEY,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    room VARCHAR(255),
    metric VARCHAR(255),
    alarm_state VARCHAR(255)
);
```

The old production `sensor_data` table may not have `rssi` yet. Adding `rssi DOUBLE NULL` is an approved non-destructive extension; existing rows remain valid with `NULL` RSSI.

## Implementation Direction

Prefer a Python Dockerized MQTT ingestion service:

```text
MQTT subscriber
-> payload parser
-> device-to-room mapper
-> latest-reading cache
-> periodic batch writer
-> alarm evaluator
-> MariaDB
-> ntfy
```

Use a periodic batch writer to preserve the old database timing model. Do not blindly insert every MQTT message as its own Grafana row unless the user explicitly asks for event-level storage.

The project uses uv. Keep `pyproject.toml` and `uv.lock` as the dependency source of truth.

Confirmed MQTT payload fields:

- device address: `id`; `mac` may also be present for Outdoor Meter
- device type: `model`
- temperature Celsius: `tempc`
- humidity percent: `hum`
- battery percent: `batt`
- signal strength: `rssi`

Confirmed device mapping:

```text
Living Room -> C1:34:30:37:35:3D -> Meter (Plus)
Bedroom     -> CD:34:30:37:22:11 -> Meter (Plus)
Kitchen     -> D1:34:30:37:57:58 -> Meter (Plus)
Study       -> CF:34:30:37:2A:78 -> Meter (Plus)
Bathroom    -> CA:5F:44:06:0D:6D -> Outdoor Meter
Outdoor     -> CA:5F:45:46:46:24 -> Outdoor Meter
```

## Configuration and Secrets

Do not copy plaintext secrets from `old/credentials.py`.

Use environment variables for:

- MQTT connection.
- MariaDB connection.
- ntfy endpoint/topic.
- write interval.
- freshness threshold.
- battery threshold.

Use a mounted YAML or JSON config for room/device mapping.

## Migration Safety

Use phases:

1. Observe-only mode: parse MQTT and log, no DB writes.
2. Shadow-write mode: write to shadow tables, not production.
3. Compare against Raspberry Pi data.
4. Cut over by stopping Pi jobs and writing to production tables.
5. Roll back by stopping the container and restarting Pi jobs.

Avoid writing both systems into production `sensor_data` at the same time.

Cutover summary:

1. Confirm shadow tables have clean data for 1-2 days.
2. Add production RSSI column if missing: `ALTER TABLE sensor_data ADD COLUMN rssi DOUBLE NULL;`
3. Stop and disable Raspberry Pi `read_meters.py` and `check_reading.py` jobs.
4. Set `.env` to `DB_SENSOR_TABLE=sensor_data` and `DB_ALARMS_TABLE=alarms`.
5. Redeploy with `./scripts/deploy_nas02.sh`.
6. Validate production rows, Grafana, logs, and alerting.
7. Roll back by stopping the container and restarting Pi jobs.

## Open Questions Before Coding

Most implementation details are now known. Remaining questions:

- Does Grafana query only `sensor_data`, or also `alarms`?
- Are MQTT and MariaDB credentials available as environment variables for local/container runs?
- During shadow mode, should ntfy notifications be sent to the real topic or suppressed/logged to avoid duplicate alerts from the Raspberry Pi job?
