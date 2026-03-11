FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    git \
    && rm -rf /var/lib/apt/lists/*


# Copy dev requirements if present
COPY pyproject.toml README.md requirements-dev.txt ./
COPY scripts/ ./scripts/


# Install dev dependencies if requirements-dev.txt exists
RUN if [ -f requirements-dev.txt ]; then pip install --no-cache-dir -r requirements-dev.txt; fi

COPY src/ ./src/

RUN pip install --no-cache-dir .

ENTRYPOINT ["pyforge-deploy"]
CMD ["--help"]