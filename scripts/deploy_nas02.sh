#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/docker_env.sh"

docker --context "$DOCKER_CONTEXT" build -t "$IMAGE_NAME" .
docker --context "$DOCKER_CONTEXT" rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
docker --context "$DOCKER_CONTEXT" run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  --env-file .env \
  "$IMAGE_NAME"

docker --context "$DOCKER_CONTEXT" logs --tail 50 "$CONTAINER_NAME"
