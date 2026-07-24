# Deploy — the 1 GB Droplet

The operational envelope is a first-class constraint (AD-21, PRD §15). The single most important rule:

> **Build the Docker image OFF the Droplet and `docker pull` it.** A build on 1 GB can OOM and fail.

## Prerequisites (from SETUP-GUIDE.md)

- A DigitalOcean Droplet: Basic / Regular SSD, **1 GB / 1 vCPU / 25 GB, Ubuntu LTS**.
- A DO Spaces bucket + keys (for the litestream backup, AD-23).
- A container registry the Droplet can pull from (GHCR / Docker Hub / DO Container Registry).
- `config/registry.yaml` and `.env` filled in (SETUP-GUIDE.md Parts 1-6).

## 1. Build off the box and push

On your laptop or in CI (**not** on the Droplet):

```bash
docker build -f deploy/Dockerfile -t <registry>/leapxpert-agent-a:latest .
docker push <registry>/leapxpert-agent-a:latest
```

A GitHub Actions workflow doing exactly this is the recommended path (Story 6.4 needs the build to
be off-box). See `.github/workflows/build-image.yml`.

## 2. Provision the Droplet (once)

```bash
scp deploy/provision.sh root@<droplet-ip>:/root/
ssh root@<droplet-ip> 'bash /root/provision.sh'
```

Adds a 2 GB swap file, opens only 443 + 22, and creates `/data`.

## 3. Configure and run

On the Droplet:

```bash
mkdir -p /opt/agent && cd /opt/agent
# copy your filled-in .env and config/ here (scp), then:
docker pull <registry>/leapxpert-agent-a:latest
docker run -d --name agent --restart unless-stopped \
    --memory 768m \
    -p 127.0.0.1:8000:8000 \
    --env-file /opt/agent/.env \
    -v /opt/agent/config:/app/config:ro \
    -v /data:/data \
    <registry>/leapxpert-agent-a:latest
```

`--memory 768m` leaves headroom under 1 GB + swap. FastAPI is bound to localhost via the port
mapping; nothing but Caddy reaches it.

## 4. Caddy (TLS + reverse proxy)

```bash
apt-get install -y caddy   # or the Marketplace image ships it
cp deploy/Caddyfile /etc/caddy/Caddyfile   # edit the domain first
systemctl restart caddy
```

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
