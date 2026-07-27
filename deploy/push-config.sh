#!/usr/bin/env bash
# Sync the two HOST-ONLY files to the Droplet: `.env` (secrets) and `config/registry.yaml` (this
# tenant's real ids + the agent_b block). Both are gitignored on purpose — they never ride in git,
# the Docker image, or a registry (see .dockerignore). So they exist in exactly two places: your
# laptop and the box, and it is on you to keep them in sync. This script is that sync.
#
#   ./deploy/push-config.sh
#   DROPLET=root@1.2.3.4 ./deploy/push-config.sh
#
# After syncing you must RESTART the containers to pick up the change: `.env` is read once at container
# start (--env-file), and registry.yaml is mounted read-only and read at process start. So the usual
# follow-up is `./deploy/redeploy.sh && ./deploy/agent_b.sh`.
set -euo pipefail

DROPLET="${DROPLET:-root@143.198.218.143}"

[ -f .env ] || { echo "!! no .env in $(pwd) — run from the repo root"; exit 1; }
[ -f config/registry.yaml ] || { echo "!! no config/registry.yaml — run from the repo root"; exit 1; }

echo "==> Syncing .env + config/registry.yaml to ${DROPLET}"
# Ensure the target dir exists (a fresh box), then copy both files.
ssh -o ConnectTimeout=20 "$DROPLET" 'mkdir -p /opt/agent/config'
scp .env "$DROPLET:/opt/agent/.env"
scp config/registry.yaml "$DROPLET:/opt/agent/config/registry.yaml"

echo "==> Done. Now restart so the containers pick up the change:"
echo "    ./deploy/redeploy.sh && ./deploy/agent_b.sh"
