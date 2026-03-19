FROM python:3.12-slim AS base
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

FROM base AS builder
WORKDIR /app

COPY requirements-docker.txt ./



COPY wheels /wheels
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    python -m pip install --upgrade pip wheel

RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    python -m pip install --user --no-index --find-links /wheels -r requirements-docker.txt



COPY . .
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    python -m pip install --user --no-cache-dir --no-deps .


FROM python:3.12-slim AS runtime

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1


RUN groupadd -r appuser && useradd -r -g appuser -d /home/appuser -m -s /bin/bash appuser
COPY --from=builder --chown=appuser:appuser /root/.local /home/appuser/.local
ENV PATH="/home/appuser/.local/bin:$PATH"


COPY --chown=appuser:appuser . .

RUN find /app -type d -name '__pycache__' -prune -exec rm -rf {} + && \
    find /app -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete && \
    rm -rf /app/build /app/dist /app/*.egg-info /app/tests /app/.git /app/.pytest_cache /app/.mypy_cache || true


USER appuser
RUN rm -rf /home/appuser/.cache || true


HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1


CMD ["python", "pyforge_deploy/cli.py"]
