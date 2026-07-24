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

echo "==> Docker (the box only PULLS the image — it never builds one, AD-21)"
if command -v docker >/dev/null 2>&1; then
	echo "    docker already installed — skipping"
else
	# Stock Ubuntu has no docker package. Docker's official convenience script adds their apt repo.
	curl -fsSL https://get.docker.com | sh
	systemctl enable --now docker
	echo "    docker installed"
fi

echo "==> Caddy (TLS terminator + reverse proxy)"
if command -v caddy >/dev/null 2>&1; then
	echo "    caddy already installed — skipping"
else
	# Caddy is NOT in the stock Ubuntu repos; `apt-get install caddy` alone fails. Add the official
	# Cloudsmith repo first (these steps are from caddyserver.com/docs/install).
	apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl gnupg
	curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' |
		gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
	curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
		>/etc/apt/sources.list.d/caddy-stable.list
	apt-get update
	apt-get install -y caddy
	echo "    caddy installed"
fi

echo "==> Data directory for the SQLite store + exported .md files (backed up by litestream, AD-23)"
mkdir -p /data/userdocs
mkdir -p /opt/agent/config
# The container runs as non-root uid 10001 (the image's `agent` user). A bind mount keeps the HOST's
# ownership and shadows whatever the image set, so a root-owned /data leaves the process unable to
# create its SQLite file — the container then crash-loops on
# `sqlite3.OperationalError: unable to open database file`. Match the uid rather than loosening the
# mode: the agent holds broad Atlassian rights via its token and should not also run as root.
chown -R 10001:10001 /data
echo "    /data (owned by uid 10001) and /opt/agent ready"

echo "==> Reminder"
cat <<'NOTE'
    - Build the image OFF this box and `docker pull` it here (a build on 1 GB can OOM).
    - Put secrets in /opt/agent/.env (see .env.example); never commit them.
    - Start Caddy with deploy/Caddyfile, litestream with deploy/litestream.yml, and install the
      cron entry from deploy/reconcile.cron.
    - See deploy/README.md for the full sequence.
NOTE
