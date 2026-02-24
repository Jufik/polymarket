# ── Build stage ──────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

RUN pip install uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src/ src/
RUN uv sync --all-extras --no-dev --frozen

# ── Runtime stage ────────────────────────────────────────────────────
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/pyproject.toml /app/pyproject.toml
ENV PATH="/app/.venv/bin:$PATH"

# Default: run the live pipeline
CMD ["pm-live"]
