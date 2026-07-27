#!/usr/bin/env bash
# Deploy Agent B (the Slack Q&A bot + the nightly KB pull) to the Droplet. Run from your laptop, AFTER
# building and pushing the Agent B image (deploy/README.md → "Agent B"). Agent A is left untouched — a
# separate container, so an Agent B problem never takes the PRD flow down.
#
#   ./deploy/agent_b.sh                 # deploy :latest
#   ./deploy/agent_b.sh v0.2.0          # a specific tag
#   DROPLET=root@1.2.3.4 ./deploy/agent_b.sh
#
# What it does on the box, all idempotent:
#   1. Pull the Agent B image.
#   2. One-shot seed pull: crawl the curated space → vault → RAG index (so the bot has something to
#      answer from before it starts). Safe to re-run (content-hash idempotent, S-B4/S-B6).
#   3. (Re)start the `agent-b` Slack-bot container — Socket Mode (outbound only, no inbound port),
#      memory-capped, restart unless-stopped.
#   4. Install the nightly pull cron (a one-shot container at 03:00).
set -euo pipefail

DROPLET="${DROPLET:-root@143.198.218.143}"
IMAGE="${IMAGE:-ghcr.io/nhansgithub/agent_b_bot}"
TAG="${1:-latest}"
REF="${IMAGE}:${TAG}"

echo "==> Deploying Agent B ${REF} to ${DROPLET}"

ssh -o ConnectTimeout=20 "$DROPLET" bash -s -- "$REF" <<'REMOTE'
set -euo pipefail
REF="$1"

MOUNTS=(--env-file /opt/agent/.env -v /opt/agent/config:/app/config:ro -v /data:/app/data)

echo "--> pulling $REF"
docker pull -q "$REF"

echo "--> seed pull: crawl → vault → index (idempotent)"
docker run --rm "${MOUNTS[@]}" "$REF" python scripts/run_agent_b_pull.py

echo "--> (re)starting the Slack bot container 'agent-b'"
docker rm -f agent-b >/dev/null 2>&1 || true
docker run -d --name agent-b --restart unless-stopped \
    --memory 512m \
    "${MOUNTS[@]}" \
    "$REF" >/dev/null

sleep 3
echo "--> agent-b logs:"
docker logs agent-b 2>&1 | tail -15

echo "--> installing the nightly pull cron (idempotent)"
CRON_LINE="0 3 * * * docker run --rm --env-file /opt/agent/.env -v /opt/agent/config:/app/config:ro -v /data:/app/data $REF python scripts/run_agent_b_pull.py >> /var/log/agent-b-pull.log 2>&1"
if crontab -l 2>/dev/null | grep -q 'run_agent_b_pull.py'; then
    echo "    cron already present — skipping"
else
    (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
    echo "    nightly pull cron installed"
fi

docker image prune -f >/dev/null 2>&1 || true
echo "--> Agent B deployed."
REMOTE

echo "==> Done. Test it: DM the bot, or @-mention it in the allowed channel."
echo "    Publish site (S-B5) is a separate step — see deploy/README.md → 'Agent B site'."
