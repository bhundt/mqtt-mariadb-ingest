# MQTT MariaDB Ingest

Ingests SwitchBot readings published by OpenMQTTGateway into MariaDB.

The current implementation is configured for shadow-table migration:

- MQTT broker: `192.168.178.100:1883`
- MQTT topic: `home/+/BTtoMQTT/#`
- MariaDB tables: `sensor_data_mqtt_shadow`, `alarms_mqtt_shadow`
- Write interval: 5 minutes
- Write policy: latest valid reading per room per interval
- Extra MQTT-only measurement: `rssi`, stored as nullable `DOUBLE`

## Local Setup

Use uv for dependency management:

```bash
uv sync
uv run python -m unittest discover -s tests
```

Runtime secrets belong in `.env`, which is intentionally ignored by git. Start from:

```bash
cp .env.example .env
```

Then fill in:

- `MQTT_USERNAME`
- `MQTT_PASSWORD`
- `MARIADB_PASSWORD`
- `NTFY_TOPIC`

During shadow-table validation, keep:

```text
SEND_NOTIFICATIONS=false
DB_SENSOR_TABLE=sensor_data_mqtt_shadow
DB_ALARMS_TABLE=alarms_mqtt_shadow
```

## Run Locally

```bash
uv run python -m mqtt_mariadb_ingest
```

## Docker on nas02

Use the Docker daemon on nas02. This project does not require Docker Compose.

Common lifecycle commands:

```bash
./scripts/deploy_nas02.sh    # build image, replace container, show recent logs
./scripts/status_nas02.sh    # show container status
./scripts/logs_nas02.sh      # follow logs
./scripts/stop_nas02.sh      # stop container
./scripts/remove_nas02.sh    # force remove container
./scripts/rebuild_nas02.sh   # rebuild image with --no-cache
```

The scripts default to:

```text
DOCKER_CONTEXT=nas02
IMAGE_NAME=mqtt-mariadb-ingest:latest
CONTAINER_NAME=mqtt-mariadb-ingest
```

Override them per command if needed:

```bash
CONTAINER_NAME=mqtt-mariadb-ingest-test ./scripts/deploy_nas02.sh
```

The container uses the ignored local `.env` file at deploy time. The room config is copied into the image at build time.

Equivalent raw Docker commands:

```bash
DOCKER_CONTEXT=nas02 docker build -t mqtt-mariadb-ingest:latest .
DOCKER_CONTEXT=nas02 docker rm -f mqtt-mariadb-ingest
DOCKER_CONTEXT=nas02 docker run -d \
  --name mqtt-mariadb-ingest \
  --restart unless-stopped \
  --env-file .env \
  mqtt-mariadb-ingest:latest
```

## Cutover Runbook

Use this after 1-2 days of clean shadow data.

### 1. Verify Shadow Data

Check that recent batches have all six rooms:

```sql
SELECT timestamp, COUNT(*) AS rows_per_batch
FROM sensor_data_mqtt_shadow
GROUP BY timestamp
ORDER BY timestamp DESC
LIMIT 20;
```

Inspect the latest readings:

```sql
SELECT room, device_address, device_type, temperature, humidity, battery_level, rssi, timestamp
FROM sensor_data_mqtt_shadow
WHERE timestamp = (SELECT MAX(timestamp) FROM sensor_data_mqtt_shadow)
ORDER BY room;
```

Check shadow alarm transitions:

```sql
SELECT *
FROM alarms_mqtt_shadow
ORDER BY id DESC
LIMIT 20;
```

### 2. Prepare Production Schema

Add RSSI to production `sensor_data` if it is not already present:

```sql
ALTER TABLE sensor_data ADD COLUMN rssi DOUBLE NULL;
```

If the column already exists, MariaDB will report a duplicate-column error. That is harmless; do not drop or recreate the table.

### 3. Stop Raspberry Pi Jobs

Stop both old Raspberry Pi jobs:

- `old/read_meters.py` job
- `old/check_reading.py` job

Make sure they will not restart automatically through cron, systemd, or another scheduler.

### 4. Switch `.env` to Production Tables

Change:

```text
DB_SENSOR_TABLE=sensor_data
DB_ALARMS_TABLE=alarms
```

Keep notifications disabled for the first production write cycle unless you explicitly want live alerts immediately:

```text
SEND_NOTIFICATIONS=false
```

After production writes look correct, enable:

```text
SEND_NOTIFICATIONS=true
```

### 5. Redeploy Container

```bash
./scripts/deploy_nas02.sh
./scripts/logs_nas02.sh
```

### 6. Validate Production Writes

Check latest production rows:

```sql
SELECT room, device_address, device_type, temperature, humidity, battery_level, rssi, timestamp
FROM sensor_data
WHERE timestamp = (SELECT MAX(timestamp) FROM sensor_data)
ORDER BY room;
```

Check batch size:

```sql
SELECT timestamp, COUNT(*) AS rows_per_batch
FROM sensor_data
GROUP BY timestamp
ORDER BY timestamp DESC
LIMIT 10;
```

Expected: one row per configured room, usually six rows per batch.

### 7. Monitor

Watch:

- Grafana panels
- container logs via `./scripts/logs_nas02.sh`
- production `sensor_data`
- production `alarms`
- ntfy behavior after `SEND_NOTIFICATIONS=true`

### Rollback

If production ingestion fails:

```bash
./scripts/stop_nas02.sh
```

Then restart the Raspberry Pi read/check jobs.

If needed, switch `.env` back to shadow tables before redeploying:

```text
DB_SENSOR_TABLE=sensor_data_mqtt_shadow
DB_ALARMS_TABLE=alarms_mqtt_shadow
SEND_NOTIFICATIONS=false
```
