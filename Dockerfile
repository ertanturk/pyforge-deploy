FROM python:3.12-slim

WORKDIR /app


COPY requirements-docker.txt ./


RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements-docker.txt \
    && apt-get purge -y --auto-remove build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY . .


RUN pip install --no-cache-dir --no-deps .


ENV PYTHONUNBUFFERED=1


CMD ["python"]
