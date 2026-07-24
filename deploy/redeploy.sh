#!/usr/bin/env bash
# Pull the current image onto the Droplet and restart the service. Run from your laptop.
#
#   ./deploy/redeploy.sh                 # deploy :latest (whatever master last built)
#   ./deploy/redeploy.sh v0.1.4          # deploy a specific tag
#   DROPLET=root@1.2.3.4 ./deploy/redeploy.sh
#
# It does NOT build anything — the image is built off the box by GitHub Actions (AD-21). This only
# pulls and restarts, then proves the result rather than assuming it.
#
# The verification at the end is the point. A container can start, answer /health, and still be
# deaf — if the config volume is missing it reports `config: missing` and accepts no webhooks. This
# script treats that as a failed deploy and exits non-zero.
set -euo pipefail

DROPLET="${DROPLET:-root@143.198.218.143}"
IMAGE="${IMAGE:-ghcr.io/nhansgithub/agent_a}"
TAG="${1:-latest}"
REF="${IMAGE}:${TAG}"

echo "==> Deploying ${REF} to ${DROPLET}"

ssh -o ConnectTimeout=20 "$DROPLET" bash -s -- "$REF" <<'REMOTE'
set -euo pipefail
REF="$1"

echo "--> pulling $REF"
docker pull -q "$REF"

# Keep the previous image id so a bad deploy can be rolled back by digest.
PREVIOUS=$(docker inspect agent --format '{{.Image}}' 2>/dev/null || echo "none")
echo "--> previous image: $PREVIOUS"

echo "--> recreating the container"
docker rm -f agent >/dev/null 2>&1 || true
docker run -d --name agent --restart unless-stopped \
    --memory 768m \
    -p 127.0.0.1:8000:8000 \
    --env-file /opt/agent/.env \
    -v /opt/agent/config:/app/config:ro \
    -v /data:/data \
    "$REF" >/dev/null

# Give uvicorn a moment, then poll rather than guessing a fixed sleep.
for _ in $(seq 1 20); do
    BODY=$(curl -fsS --max-time 5 localhost:8000/health 2>/dev/null || true)
    [ -n "$BODY" ] && break
    sleep 1
done

echo "--> /health: ${BODY:-<no response>}"
case "$BODY" in
    *'"config":"loaded"'*) echo "--> OK: config loaded, webhooks mounted" ;;
    "")  echo "!! FAILED: the service never answered /health"; docker logs agent 2>&1 | tail -25; exit 1 ;;
    *)   echo "!! FAILED: config not loaded — check the /opt/agent/config mount"
         docker logs agent 2>&1 | tail -25; exit 1 ;;
esac

docker image prune -f >/dev/null 2>&1 || true
REMOTE

echo "==> Verifying from the public internet"
PUBLIC=$(curl -fsS --max-time 20 https://poetroastery.com/health)
echo "    $PUBLIC"
case "$PUBLIC" in
    *'"config":"loaded"'*) echo "==> Deploy OK" ;;
    *) echo "!! Reachable locally but not correct through Caddy"; exit 1 ;;
esac
