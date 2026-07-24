#!/usr/bin/env bash
# One-time Droplet provisioning for the 1 GB host (AD-21, PRD §15).
#
# Run this once on a fresh Ubuntu LTS Droplet as root. It sets up the memory cushion, firewall, and
# the directories the pulled image mounts. It does NOT build the image — that is done off the box
# (AD-21); this host only pulls and runs it.
#
# Idempotent: safe to re-run.
set -euo pipefail

echo "==> 1-2 GB swap file (AD-21 — cushions transient memory spikes on the 1 GB box)"
if [ ! -f /swapfile ]; then
	fallocate -l 2G /swapfile
	chmod 600 /swapfile
	mkswap /swapfile
	swapon /swapfile
	grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
	echo "    swap enabled"
else
	echo "    swap already present — skipping"
fi

echo "==> Firewall: expose only 443 (webhooks) and 22 (SSH) (AD-21, PRD §15.4)"
ufw allow 22/tcp
ufw allow 443/tcp
ufw --force enable
echo "    ufw active; FastAPI stays bound to localhost behind Caddy"

echo "==> Data directory for the SQLite store + exported .md files (backed up by litestream, AD-23)"
mkdir -p /data/userdocs
echo "    /data ready"

echo "==> Reminder"
cat <<'NOTE'
    - Build the image OFF this box and `docker pull` it here (a build on 1 GB can OOM).
    - Put secrets in /opt/agent/.env (see .env.example); never commit them.
    - Start Caddy with deploy/Caddyfile, litestream with deploy/litestream.yml, and install the
      cron entry from deploy/reconcile.cron.
    - See deploy/README.md for the full sequence.
NOTE
