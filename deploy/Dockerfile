# syntax=docker/dockerfile:1
#
# The single deploy artifact (NFR-07, AD-21).
#
# ⚠️  BUILD THIS OFF THE DROPLET. A Docker build on the 1 GB box can OOM and fail (AD-21). Build in
#     CI or on your laptop, push to a registry, and `docker pull` it onto the Droplet. See
#     deploy/README.md.
#
# Slim base, no build toolchain in the final image, single Uvicorn worker at runtime (AD-21).

FROM python:3.12-slim AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
# Install into a venv we can copy wholesale into the runtime stage — keeps pip and wheels out of it.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml README.md ./
COPY app ./app
# Install the pinned runtime deps + the package itself (no dev extras in the image).
RUN pip install .

# ---------------------------------------------------------------------------------------------

FROM python:3.12-slim AS runtime

# Non-root: the agent has broad Atlassian rights via its token; the process should not also be root.
RUN useradd --create-home --uid 10001 agent

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=build /opt/venv /opt/venv
COPY --from=build /app/app /app/app
COPY config /app/config

WORKDIR /app
# /data holds the SQLite store and the exported .md files; litestream replicates it (AD-23).
RUN mkdir -p /data && chown -R agent:agent /data /app
USER agent

EXPOSE 8000
# Single worker (AD-21) bound to localhost is enforced by the run command; Caddy fronts it.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
