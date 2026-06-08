# Multi-stage build per PHASE2-SPEC.md §3.
# Final image: python:3.12-slim, non-root, no build tools.

# ---- builder ----
FROM python:3.12-slim AS builder

# Install uv for the dependency sync. Pin a specific uv version for
# reproducibility.
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

WORKDIR /build

# Layer 1: project metadata (rarely changes).
COPY pyproject.toml ./

# Layer 2: dependency resolution (cached if pyproject.toml is unchanged).
RUN uv venv .venv && \
    . .venv/bin/activate && \
    uv pip install --no-cache \
        'httpx>=0.27' \
        'websockets>=12' \
        'pydantic>=2.5' \
        'structlog>=24.1'

# Layer 3: source code.
COPY . .

# ---- runner ----
FROM python:3.12-slim AS runner

# Run as a non-root user. UID 1000 matches the NTFY container in the
# compose stack — keeps volume permissions consistent.
RUN groupadd -g 1000 atlas && \
    useradd -u 1000 -g 1000 -m -d /home/atlas atlas && \
    mkdir -p /var/lib/atlas-notifier && \
    chown -R atlas:atlas /var/lib/atlas-notifier

# Copy the prepared venv from the builder.
COPY --from=builder --chown=atlas:atlas /build/.venv /home/atlas/.venv
COPY --from=builder --chown=atlas:atlas /build/*.py /home/atlas/

USER atlas
WORKDIR /home/atlas

ENV PATH="/home/atlas/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# The notifier is long-running; no exposed port for the notifier itself
# (the healthz endpoint binds 127.0.0.1:18080 — see config.py).
EXPOSE 18080

# Healthcheck via the in-process HealthCheck class — we probe the
# supervisor's reachability flags by writing a sentinel file. The
# supervisor in main.py would need to update this on a timer; for now,
# the simplest correct healthcheck is the file-exists sentinel. The
# spec's docker-compose fragment uses a file-based check.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request, json, sys; r = urllib.request.urlopen('http://127.0.0.1:18080/healthz', timeout=3); d = json.loads(r.read()); sys.exit(0 if d.get('openwebui_reachable') and d.get('ntfy_reachable') else 1)"

CMD ["python", "-m", "main"]
