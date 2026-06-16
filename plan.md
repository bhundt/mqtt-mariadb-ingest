# MQTT to MariaDB Migration Plan

## Goal

Migrate the SwitchBot sensor ingestion path from the Raspberry Pi BLE collector to an ESP32/OpenMQTTGateway plus Mosquitto based ingestion service, while preserving current MariaDB/Grafana compatibility and alerting behavior.

Target architecture:

```text
SwitchBot sensors
-> ESP32 / OpenMQTTGateway
-> Mosquitto
-> new ingestion service
-> MariaDB
-> Grafana
```

## 1. Current Raspberry Pi Behavior

The old implementation lives in `old/`.

Main scripts:

- `old/read_meters.py`: performs one BLE scan, parses known SwitchBot advertisements, filters known rooms, writes readings to SQLite and MariaDB, and evaluates value alarms.
- `old/check_reading.py`: checks that the latest MariaDB data is complete and recent.
- `old/utils.py`: contains ntfy push notification and MariaDB connection helpers.
- `old/config.py`: contains room/device mapping, thresholds, scan timeout, and MariaDB connection metadata.

Current behavior to preserve:

- Reads SwitchBot `Meter Plus` and `Outdoor Meter` data.
- Stores temperature, humidity, battery level, room, device address, and device type.
- The MQTT implementation additionally stores RSSI when available.
- Filters readings to the six configured rooms:
  - Living Room
  - Bedroom
  - Kitchen
  - Study
  - Bathroom
  - Outdoor
- Uses room-specific thresholds from `old/config.py`.
- Sends push notifications through ntfy.
- Writes alarm transitions to the `alarms` table.
- Sends alarm notifications only on state changes:
  - `OK -> ALARM`
  - `ALARM -> OK`
- Checks incomplete data when not all configured rooms were read.
- Checks freshness using a 15 minute threshold.
- Uses one shared timestamp for all rows inserted by one read cycle.

Important detail: `old/read_meters.py` recognizes a `Meter` device type but does not currently parse it successfully; unsupported/unknown device types raise an error.

## 2. Existing MariaDB Schema

The current code creates and writes these MariaDB tables:

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

The old production table may not yet have `rssi`. Adding it is a non-destructive schema change because existing rows keep their previous values and receive `NULL` for RSSI. The new service auto-creates or upgrades its configured sensor table with `rssi DOUBLE NULL`.

Current insert behavior:

- `sensor_data`: one row per room per scan cycle.
- `timestamp`: generated once per cycle and reused for all readings in that cycle.
- `alarms`: one row only when a room/metric alarm state changes.
- `rssi`: available for MQTT-derived rows; old Raspberry Pi rows can remain `NULL`.

## 3. Confirmed MQTT Topic and Payload

OpenMQTTGateway publishes the SwitchBot BLE data to Mosquitto.

Broker:

```text
host: 192.168.178.100
port: 1883
auth: username and password required
```

Topic pattern:

```text
home/+/BTtoMQTT/<device-id-without-colons>
```

The `+` wildcard allows multiple OpenMQTTGateway ESP32 devices to publish readings for the same sensors. The ingestion service maps readings by sensor `id`, so the gateway name does not need to be fixed.

Confirmed messages:

Living Room:

```text
home/OpenMQTTGateway_ESP32C3_DKC02/BTtoMQTT/C1343037353D
```

```json
{"id":"C1:34:30:37:35:3D","rssi":-72,"brand":"SwitchBot","model":"Meter (Plus)","model_id":"THX1/W230150X","type":"THB","tempc":22.2,"tempf":71.96,"hum":48,"batt":63}
```

Bedroom:

```text
home/OpenMQTTGateway_ESP32C3_DKC02/BTtoMQTT/CD3430372211
```

```json
{"id":"CD:34:30:37:22:11","rssi":-74,"brand":"SwitchBot","model":"Meter (Plus)","model_id":"THX1/W230150X","type":"THB","tempc":21.3,"tempf":70.34,"hum":53,"batt":62}
```

Kitchen:

```text
home/OpenMQTTGateway_ESP32C3_DKC02/BTtoMQTT/D13430375758
```

```json
{"id":"D1:34:30:37:57:58","rssi":-77,"brand":"SwitchBot","model":"Meter (Plus)","model_id":"THX1/W230150X","type":"THB","tempc":21.4,"tempf":70.52,"hum":51,"batt":66}
```

Study:

```text
home/OpenMQTTGateway_ESP32C3_DKC02/BTtoMQTT/CF3430372A78
```

```json
{"id":"CF:34:30:37:2A:78","rssi":-83,"brand":"SwitchBot","model":"Meter (Plus)","model_id":"THX1/W230150X","type":"THB","tempc":21.5,"tempf":70.7,"hum":50,"batt":62}
```

Bathroom:

```text
home/OpenMQTTGateway_ESP32C3_DKC02/BTtoMQTT/CA5F44060D6D
```

```json
{"id":"CA:5F:44:06:0D:6D","rssi":-52,"brand":"SwitchBot","model":"Outdoor Meter","model_id":"W340001X","type":"THB","tempc":21.9,"tempf":71.42,"hum":53,"batt":49,"mac":"CA:5F:44:06:0D:6D"}
```

Outdoor:

```text
home/OpenMQTTGateway_ESP32C3_DKC02/BTtoMQTT/CA5F45464624
```

```json
{"id":"CA:5F:45:46:46:24","rssi":-69,"brand":"SwitchBot","model":"Outdoor Meter","model_id":"W340001X","type":"THB","tempc":22.9,"tempf":73.22,"hum":44,"batt":79,"mac":"CA:5F:45:46:46:24"}
```

Parser mapping:

- device address: `id`; fallback to `mac` if present
- device type: `model`
- temperature: `tempc`
- humidity: `hum`
- battery level: `batt`
- signal strength: `rssi`

The implementation may still tolerate common aliases for robustness, but the confirmed payload fields above are the primary contract.

The MQTT device IDs match the old Linux BLE MAC addresses in `old/config.py`.

## 4. Proposed New Service Architecture

Build a small Python service:

```text
MQTT subscriber
-> JSON parser / normalizer
-> known-device room mapper
-> latest-reading cache
-> periodic batch writer
-> alarm evaluator
-> MariaDB
-> ntfy notifications
```

Use a periodic batch writer instead of inserting every MQTT message directly. MQTT messages arrive independently, but the old system is batch-oriented and writes one shared timestamp for all rooms. The new service should collect the latest valid reading per room and flush one batch at a configured interval.

Recommended default:

- MQTT subscription: continuous
- batch write interval: 5 minutes
- freshness threshold: 15 minutes, matching old behavior
- write policy: keep the latest valid reading per room within each 5 minute interval, then write one batch

Potential source layout:

```text
src/
  ingest.py
  config.py
  db.py
  mqtt_client.py
  alerts.py
  models.py
tests/
Dockerfile
```

## 5. Alerting and Notification Parity

Preserve these alert checks:

- Low battery:
  - `battery_level < 20`
  - metric: `battery`
- Low temperature:
  - compare against per-room `temp_lower_limit`
  - metric: `temperature`
- High humidity:
  - compare against per-room `humidity_upper_limit`
  - metric: `humidity`
- Incomplete data:
  - notify if a batch cannot include all configured rooms
- No recent data:
  - notify if no valid full write has happened within 15 minutes
- Database connection failure:
  - retry and notify after repeated failures

Use the existing `alarms` table to suppress repeated notifications while a metric remains in `ALARM`.

Recommended improvement for MQTT:

- Track per-room reading age.
- Do not write stale cached readings forever.
- Treat a room as missing if its latest MQTT reading is older than a configurable per-room freshness threshold.

## 6. Containerization and Deployment on Unraid/nas02

Build and run the new service as a Docker container.

Recommended container properties:

- Python runtime.
- uv-based dependency installation and execution.
- No Bluetooth or host device access required.
- Connects to Mosquitto and MariaDB via LAN or Docker network.
- Restart policy: `unless-stopped`.
- Logs to stdout/stderr for Unraid Docker logs.
- Configuration from environment variables plus a mounted room mapping file.

For nas02:

- Build using the nas02 Docker daemon with `DOCKER_CONTEXT=nas02`.
- Deploy with plain Docker commands; Docker Compose is not required on nas02.
- Keep the service independent from the MariaDB and Mosquitto containers so it can be restarted safely.

Local commands:

```bash
uv sync
uv run python -m unittest discover -s tests
uv run python -m mqtt_mariadb_ingest
```

Docker commands for nas02:

```bash
DOCKER_CONTEXT=nas02 docker build -t mqtt-mariadb-ingest:latest .
DOCKER_CONTEXT=nas02 docker rm -f mqtt-mariadb-ingest
DOCKER_CONTEXT=nas02 docker run -d \
  --name mqtt-mariadb-ingest \
  --restart unless-stopped \
  --env-file .env \
  mqtt-mariadb-ingest:latest
```

Equivalent helper:

```bash
./scripts/deploy_nas02.sh
```

Additional lifecycle helpers:

```bash
./scripts/status_nas02.sh
./scripts/logs_nas02.sh
./scripts/stop_nas02.sh
./scripts/remove_nas02.sh
./scripts/rebuild_nas02.sh
```

## 7. Configuration and Secrets

Do not keep secrets in Python source files.

Use environment variables for secrets and simple values:

```text
MQTT_HOST
MQTT_PORT
MQTT_USERNAME
MQTT_PASSWORD
MQTT_TOPIC
MARIADB_HOST
MARIADB_PORT
MARIADB_USER
MARIADB_PASSWORD
MARIADB_DATABASE
NTFY_BASE_URL
NTFY_TOPIC
WRITE_INTERVAL_SECONDS
FRESHNESS_THRESHOLD_MINUTES
BATTERY_LOWER_LIMIT
```

Known values:

```text
MQTT_HOST=192.168.178.100
MQTT_PORT=1883
MQTT_TOPIC=home/+/BTtoMQTT/#
WRITE_INTERVAL_SECONDS=300
FRESHNESS_THRESHOLD_MINUTES=15
BATTERY_LOWER_LIMIT=20
```

MariaDB connection details should follow `old/config.py` for host, port, user, and database. The password must come from environment or a secret, not from `old/credentials.py`.

Use a mounted YAML or JSON file for room/device mapping:

```yaml
rooms:
  - name: Living Room
    mqtt_ids:
     - C1:34:30:37:35:3D
    device_type: Meter (Plus)
    temp_lower_limit: 18.0
    humidity_upper_limit: 60.0
```

The old `old/credentials.py` contains plaintext secrets. Do not copy that pattern into the new implementation.

## 8. Testing Strategy

Unit tests:

- Parse representative MQTT payloads.
- Support expected payload field aliases.
- Map device IDs to room names.
- Ignore unknown devices.
- Reject readings missing temperature, humidity, or battery if those fields are required.
- Preserve per-room thresholds.
- Verify alarm transition behavior:
  - no duplicate notifications while already in `ALARM`
  - recovery notification on `ALARM -> OK`
- Verify batch write behavior:
  - all rooms in a flush share the same timestamp
  - stale cached readings are not written as fresh data

Integration tests:

- Use a disposable MariaDB schema or container.
- Verify inserts into `sensor_data`.
- Verify inserts into `alarms`.
- Simulate MQTT messages with a test broker/client.

Manual validation:

- Run service in observe-only mode against real Mosquitto.
- Compare parsed MQTT values with values from the old Raspberry Pi collector.
- Verify Grafana still renders from the existing schema after cutover.

## 9. Safe Migration Strategy

Avoid writing both the Raspberry Pi collector and the new MQTT service into the production `sensor_data` table at the same time. That would create duplicate or competing Grafana data.

Recommended phases:

1. Observe-only mode
   - Subscribe to MQTT.
   - Parse and log normalized readings.
   - Do not write to MariaDB.

2. Shadow-write mode
   - Write to `sensor_data_mqtt_shadow`.
   - Write to `alarms_mqtt_shadow`.
   - Include `rssi DOUBLE NULL` in `sensor_data_mqtt_shadow`.
   - Keep the old Raspberry Pi job writing production tables.

3. Compare phase
   - Run both systems for several days.
   - Compare per-room temperature, humidity, battery, and missing-data rates.
   - Verify alert behavior without sending duplicate production notifications, or send shadow alerts to logs only.

4. Cutover
   - Stop the Raspberry Pi read/check cron jobs.
   - Add `rssi DOUBLE NULL` to production `sensor_data` if it is not already present.
   - Switch MQTT ingestion to production `sensor_data` and `alarms`.
   - Watch Grafana, logs, and ntfy notifications.

Cutover runbook:

1. Verify shadow data has clean six-row batches for 1-2 days.
2. Add RSSI to production if missing:

   ```sql
   ALTER TABLE sensor_data ADD COLUMN rssi DOUBLE NULL;
   ```

3. Stop the Raspberry Pi `read_meters.py` and `check_reading.py` jobs and disable their scheduler.
4. Change `.env`:

   ```text
   DB_SENSOR_TABLE=sensor_data
   DB_ALARMS_TABLE=alarms
   ```

5. Keep `SEND_NOTIFICATIONS=false` for the first production write cycle; enable `SEND_NOTIFICATIONS=true` after validation.
6. Redeploy with `./scripts/deploy_nas02.sh`.
7. Validate production `sensor_data`, `alarms`, Grafana, and container logs.

5. Rollback
   - Stop the MQTT ingestion container.
   - Restart Raspberry Pi jobs.

Parallel runtime: run for a couple of days first, then decide whether to extend to 3 to 7 days based on observed stability.

## 10. Risks, Assumptions, and Open Questions

Risks:

- MQTT is event-oriented, while the old database model is batch-oriented.
- OpenMQTTGateway may publish duplicate messages or multiple messages per advertisement.
- Shadow alerting needs care to avoid duplicate ntfy notifications during parallel testing.

Assumptions:

- Mosquitto already receives SwitchBot BLE data.
- Mosquitto runs at `192.168.178.100:1883` and requires username/password.
- MariaDB and Grafana should keep using the existing schema.
- ntfy remains the push notification mechanism.
- Docker on nas02/Unraid is the preferred deployment target.
- Docker builds and deployments should use `DOCKER_CONTEXT=nas02`.
- The initial implementation should write to shadow tables.
- The effective write interval is 5 minutes.
- Duplicate/downsampling behavior should use the latest valid reading per room within the 5 minute write interval.

Open questions:

- Does Grafana query only `sensor_data`, or also `alarms`?
- Are MQTT and MariaDB credentials available as environment variables or should an `.env.example` be created for local development?
- During shadow mode, should ntfy notifications be sent to the real topic or suppressed/logged to avoid duplicate alerts from the Raspberry Pi job?
