#!/usr/bin/env bash
# Build the Agent B Quartz site (S-B5). Runs OFF the 1 GB Droplet — in CI or on a build host — because
# a Node/npm build can OOM on 1 GB, exactly like the Docker image build (AD-21). The output is a static
# directory Caddy serves at agent.poetroastery.com, read-only and unauthenticated (D-45).
#
# What it does:
#   1. Clone (or reuse) a PINNED Quartz v4 checkout — Quartz is the Node SSG, not vendored into this
#      Python repo; pinning a commit keeps builds reproducible.
#   2. Let the Python prep step (scripts/build_agent_b_site.py) drop in the config-driven
#      quartz.config.ts + custom.scss and stage the vault's notes into content/.
#   3. Run `npx quartz build`, emitting the site into AgentBConfig.publish.output_dir.
#
# Usage:  QUARTZ_REF=v4.5.1 ./deploy/build_site.sh [/path/to/quartz-checkout]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QUARTZ_DIR="${1:-$ROOT/quartz}"
QUARTZ_REPO="${QUARTZ_REPO:-https://github.com/jackyzha0/quartz.git}"
QUARTZ_REF="${QUARTZ_REF:-v4}"       # Quartz's default v4 branch (always exists); pin a tag via env
PY="${PYTHON:-$ROOT/.venv/bin/python}"

if [ ! -d "$QUARTZ_DIR/.git" ]; then
	echo "cloning Quartz $QUARTZ_REF into $QUARTZ_DIR"
	git clone --depth 1 --branch "$QUARTZ_REF" "$QUARTZ_REPO" "$QUARTZ_DIR"
fi

( cd "$QUARTZ_DIR" && npm ci )

# Python owns the config generation + content staging (the unit-tested part, agent_b.pipeline.publish).
"$PY" "$ROOT/scripts/build_agent_b_site.py" --quartz-dir "$QUARTZ_DIR" --build

echo "site built. Serve AgentBConfig.publish.output_dir via deploy/Caddyfile (agent.poetroastery.com)."
