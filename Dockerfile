FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc \
    && rm -rf /var/lib/apt/lists/*


COPY requirements-docker.txt ./


RUN --mount=type=cache,target=/root/.cache/pip \
    pip wheel -r requirements-docker.txt -w /wheels --no-cache-dir || true




RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && \
    pip install --user --no-index --find-links /wheels -r requirements-docker.txt



COPY . .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --user --no-cache-dir --no-deps .


FROM python:3.12-slim

WORKDIR /app


COPY --from=builder /root/.local /opt/pyforge/.local
ENV PATH="/opt/pyforge/.local/bin:$PATH"
RUN groupadd -r pyforge && useradd -r -g pyforge -d /home/pyforge -m -s /bin/bash pyforge \
    && chown -R pyforge:pyforge /opt/pyforge
USER pyforge


COPY . .

ENV PYTHONUNBUFFERED=1


CMD ["python"]


RUN rm -rf build dist *.egg-info || true

RUN rm -rf /home/pyforge/.cache/pip || true
