#!/usr/bin/env bash
# Publish the Agent B KB site (S-B5) in one command — run from your LAPTOP (needs Node/npm + ssh).
#
# The vault is generated ON the box by the nightly pull (/data/vault), and a Quartz/Node build can OOM
# the 1 GB box (AD-21) — so we build OFF the box: pull the vault down, build here, push the site up,
# reload Caddy. The site then serves at https://agent.poetroastery.com (the vhost in deploy/Caddyfile),
# read-only, no auth (D-45).
#
#   ./deploy/site.sh
#   DROPLET=root@1.2.3.4 QUARTZ_REF=v4.5.1 ./deploy/site.sh
#
# Prerequisites on THIS machine: Node.js + npm (Quartz is a Node tool), git, rsync, ssh access to the
# box. The `agent` A-record must already point at the box (you added it).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
DROPLET="${DROPLET:-root@143.198.218.143}"

command -v npx >/dev/null || { echo "!! Node/npm not found — install Node first (Quartz needs it)."; exit 1; }

echo "==> 1/4  Pull the generated vault down from the box (/data/vault → ./data/vault)"
mkdir -p ./data/vault
rsync -az --delete "$DROPLET:/data/vault/" ./data/vault/
if [ -z "$(ls -A ./data/vault 2>/dev/null)" ]; then
    echo "!! The box's vault is empty. Run a pull first:  ./deploy/agent_b.sh  (or the pull one-shot)."
    exit 1
fi

echo "==> 2/4  Build the Quartz site here (off-box)"
./deploy/build_site.sh          # clones Quartz, stages ./data/vault, `npx quartz build` → ./data/site

echo "==> 3/4  Upload the built site to the box (Caddy's web root)"
ssh -o ConnectTimeout=20 "$DROPLET" 'mkdir -p /opt/agent/data/site'
rsync -az --delete ./data/site/ "$DROPLET:/opt/agent/data/site/"

echo "==> 4/4  Install the Caddyfile (adds the agent.poetroastery.com vhost) and reload"
scp deploy/Caddyfile "$DROPLET:/etc/caddy/Caddyfile"
ssh -o ConnectTimeout=20 "$DROPLET" 'caddy validate --config /etc/caddy/Caddyfile && systemctl reload caddy'

echo "==> Done. Give DNS + the first TLS cert a minute, then open https://agent.poetroastery.com"
echo "    Re-run this anytime after a pull to refresh the published site."
