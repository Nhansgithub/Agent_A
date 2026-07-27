# Deploy — the 1 GB Droplet

The operational envelope is a first-class constraint (AD-21, PRD §15). The single most important rule:

> **Build the Docker image OFF the Droplet and `docker pull` it.** A build on 1 GB can OOM and fail.

## Prerequisites (from SETUP-GUIDE.md)

- A DigitalOcean Droplet: Basic / Regular SSD, **1 GB / 1 vCPU / 25 GB, Ubuntu LTS**.
- A DO Spaces bucket + keys (for the litestream backup, AD-23).
- A container registry the Droplet can pull from (GHCR / Docker Hub / DO Container Registry).
- `config/registry.yaml` and `.env` filled in (SETUP-GUIDE.md Parts 1-6).

## Routine changes (after the first deploy)

Once the box is up, shipping a code change is **two commands**:

```bash
git push                    # master push -> GitHub Actions builds and pushes :latest
./deploy/redeploy.sh        # pull + restart + verify (waits for the build to finish first)
```

`redeploy.sh` verifies rather than assumes: it polls `/health` on the box, fails the deploy if
`config` is not `loaded` (a missing config mount leaves the service alive but deaf), and then
re-checks through Caddy from the public internet. Deploy a specific build with
`./deploy/redeploy.sh v0.1.4` or `./deploy/redeploy.sh sha-1a2b3c4`.

Tagging is optional now — `v*` tags still build and give you an immutable tag to roll back to.

## 1. Build off the box and push

**Recommended: let GitHub Actions do it.** The `CI` workflow (`.github/workflows/ci.yml`) builds this
image and pushes it to GHCR — but only in its `build` job, which runs **after** the `test` job passes
(`needs: test`) and only on `master` or a `v*` tag. So a red gate never produces a deployable image,
and the off-box rule (AD-21) is satisfied without Docker on your laptop. Just push to `master`, or push
a release tag / run the workflow manually:

```bash
git push origin master                        # master build → :latest + :sha-<short>
git tag v0.1.0 && git push origin v0.1.0       # or: Actions → CI → Run workflow
```

The image lands at **`ghcr.io/nhansgithub/agent_a`** (`:latest` and `:<tag>`). GHCR requires the
image name to be **lowercase** — `github.repository` keeps the GitHub casing (`Nhansgithub/Agent_A`),
so the workflow lowercases it before tagging.

This package is currently **public**, so the Droplet needs no registry login.

Only if you have Docker locally and prefer to build by hand:

```bash
docker build -f deploy/Dockerfile -t <registry>/leapxpert-agent-a:latest .
docker push <registry>/leapxpert-agent-a:latest
```

## 2. Provision the Droplet (once)

```bash
scp deploy/provision.sh root@<droplet-ip>:/root/
ssh root@<droplet-ip> 'bash /root/provision.sh'
```

Adds a 2 GB swap file, opens only 443 + 22, installs **Docker** and **Caddy**, and creates `/data`
and `/opt/agent/config`. Idempotent — safe to re-run.

## 3. Configure and run

On the Droplet:

```bash
mkdir -p /opt/agent && cd /opt/agent
# copy your filled-in .env and config/ here (scp), then:

# ghcr.io/nhansgithub/agent_a is PUBLIC, so no docker login is needed. (If you make the package
# private later, authenticate first with a GitHub PAT carrying read:packages — via
# --password-stdin, so the token does not land in the shell history.)
docker pull ghcr.io/nhansgithub/agent_a:latest
docker run -d --name agent --restart unless-stopped \
    --memory 768m \
    -p 127.0.0.1:8000:8000 \
    --env-file /opt/agent/.env \
    -v /opt/agent/config:/app/config:ro \
    -v /data:/data \
    ghcr.io/nhansgithub/agent_a:latest
```

`--memory 768m` leaves headroom under 1 GB + swap. FastAPI is bound to localhost via the port
mapping; nothing but Caddy reaches it.

**The `config` mount is not optional.** The image deliberately ships *without* `registry.yaml`
(see `.dockerignore`) so one image serves every tenant. Without the mount the container starts,
answers `/health`, and silently accepts no webhooks. Verify before going further:

```bash
curl -s localhost:8000/health
# {"status":"ok","config":"loaded","webhooks":"mounted"}     <- what you want
# {"status":"ok","config":"missing","webhooks":"not-mounted"} <- config volume not mounted
```

If it says `missing`, fix the `-v .../config:/app/config:ro` path before registering webhooks —
otherwise every Atlassian delivery is dropped with only a log line to show for it.

## 4. Caddy (TLS + reverse proxy)

`provision.sh` already installed Caddy (it is **not** in the stock Ubuntu repos — a bare
`apt-get install caddy` fails, which is why provisioning adds the official repo first).

```bash
# Set your domain in the Caddyfile BEFORE copying it — Caddy requests a certificate for whatever
# hostname is in there, and Let's Encrypt rate-limits repeated failures for a wrong name.
scp deploy/Caddyfile root@<droplet-ip>:/etc/caddy/Caddyfile
ssh root@<droplet-ip> 'systemctl restart caddy && systemctl status caddy --no-pager'
```

The domain's **A record must already point at the Droplet IP** when Caddy starts: it proves
ownership over HTTP-01, which fails if DNS has not propagated yet.

Caddy auto-provisions a Let's Encrypt cert for your domain and proxies only `/webhooks/*` + `/health`.

## 5. litestream (off-box backup, AD-23)

```bash
# install the litestream binary (>= 0.5.4), then:
cp deploy/litestream.yml /etc/litestream.yml   # edit region/bucket
systemctl enable --now litestream
```

Restore (point-in-time) after a disk loss:

```bash
litestream restore -o /data/state.db s3://<bucket>/leapxpert-agent-a/state
```

## 6. Reconciler cron (AD-22)

```bash
crontab -l 2>/dev/null | cat - deploy/reconcile.cron | crontab -
```

## 7. Register the webhooks

SETUP-GUIDE.md Part 7 — point Jira + Confluence at `https://<your-domain>/webhooks/atlassian` with
the shared secret.

## 8. Run the demo

Create a `final_PRD_<name>` page in the source folder and walk the two gates (PM PASS, then Head of
Product Done). Watch LangSmith for per-step latency/cost. That is the PRD §12 Definition of Done.

## If 1 GB is too tight

Resizing the Droplet up (to 2 GB) is a few-minute, reversible operation in the DO panel (§15.3).
Treat an OOM during the §12 run as the signal to resize, not to redesign — nothing hard-codes a
1-GB-only assumption.

---

## Agent B (Epic 7) — the KB + Slack Q&A bot

Agent B ships as its **own** image (`deploy/Dockerfile.agent_b`) so Agent A's image stays lean — Agent
B carries the `agent_b` extra (fastembed → onnxruntime + numpy + slack-bolt) which Agent A must not.
It runs as a **separate container** (`agent-b`) plus a nightly pull cron; Agent A is untouched.

**Prerequisites on the box:** the same `/opt/agent/.env` (now also holding the three `AGENTB_SLACK_*`
keys) and `/opt/agent/config/registry.yaml` — make sure its `agent_b:` block matches your local
`config/registry.yaml` (the folder ids, `allowed_channel_ids`, and **no** `embeddings.store` key). The
easy way to keep them matched: **`./deploy/push-config.sh`** (see "Keeping .env + registry.yaml in
sync" below).

### 1. Build the Agent B image

**Automatic:** every push to `master` (or a `v*` tag) runs the CI `build-agent-b` job, which builds
`deploy/Dockerfile.agent_b` and pushes `ghcr.io/<owner>/agent_b_bot:latest` — off the box (AD-21),
only after tests pass. You normally do nothing here; just wait for green CI.

Manual fallback (e.g. building a one-off locally — never on the 1 GB box, it can OOM):

```bash
docker build -f deploy/Dockerfile.agent_b -t ghcr.io/nhansgithub/agent_b_bot:latest .
docker push ghcr.io/nhansgithub/agent_b_bot:latest
```

### 2. Deploy the bot + nightly pull

```bash
./deploy/agent_b.sh          # pulls the image, seeds the vault+index, starts `agent-b`, installs cron
```

The bot is **Socket Mode** — it connects out to Slack, so there is **no Caddy route and no firewall
change** for it. Test by DM-ing the bot or @-mentioning it in the allowed channel (`C0BL3KQSK1S`).
Memory: `agent-b` is capped at 512m and shares the box with Agent A (768m) + Caddy; the 2 GB swap
cushions the overlap. If it thrashes, resize to 2 GB (see above) or give Agent B its own host.

### 3. (Optional) The public KB site — S-B5

Build the Quartz site off-box and serve the output at `agent.poetroastery.com` (read-only, no auth):

```bash
QUARTZ_REF=v4.5.1 ./deploy/build_site.sh        # clones Quartz, stages the vault, `npx quartz build`
# rsync the built site to the box's /opt/agent/data/site, add an `agent.poetroastery.com` A record,
# then reload Caddy — the vhost is already in deploy/Caddyfile.
```

### 4. (Optional) The Q&A eval gate — S-B9

Fill `fixtures/agent_b/golden.json` with real page ids (README there) and run
`scripts/run_agent_b_eval.py` (API-gated) to bank the answer-quality bar.

---

## Keeping `.env` + `registry.yaml` in sync with the box

These two files are **gitignored** — they hold secrets (`.env`) and one tenant's real ids
(`config/registry.yaml`), so by design they never enter git, the image, or a registry (`.dockerignore`).
They live in exactly two places: your laptop and the Droplet's `/opt/agent/`. So **whenever you edit
either one locally, copy it up**:

```bash
./deploy/push-config.sh      # scp .env + config/registry.yaml → the box
```

Then restart so the containers re-read them (`.env` is read at container start; `registry.yaml` is
mounted read-only): `./deploy/redeploy.sh && ./deploy/agent_b.sh`. Code changes ride in the image via
CI; only these two files travel by `scp`.
