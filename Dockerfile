# ── Stage 1: build ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build tools
RUN pip install --no-cache-dir wheel pip-tools

# Copy and compile dependency pins, then build wheels
COPY requirements.in .
RUN pip-compile requirements.in -o requirements.txt --no-header --quiet && \
    pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# Copy source and build the package wheel
COPY src/ ./src/
COPY pyproject.toml .
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /wheels .

# ── Stage 2: runtime ────────────────────────────────────────────────────────
FROM python:3.12-slim

# Non-root user
RUN groupadd -r -g 1001 prometheus && \
    useradd -r -u 1001 -g prometheus -M -d /app -s /sbin/nologin prometheus

WORKDIR /app

# Install from pre-built wheels — no build tools needed at runtime
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels /wheels/*.whl && \
    rm -rf /wheels

USER prometheus

EXPOSE 9090

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9090/metrics')" || exit 1

ENTRYPOINT ["binance-exporter"]
CMD ["--port", "9090"]
