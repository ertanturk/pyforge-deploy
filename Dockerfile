FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY scripts/ ./scripts/

RUN pip install --no-cache-dir .

COPY src/ ./src/

RUN pip install --no-cache-dir .

ENTRYPOINT ["pyforge-deploy"]
CMD ["--help"]