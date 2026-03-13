FROM python:3.12-slim

WORKDIR /app


RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*




COPY requirements-dev.txt ./

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements-dev.txt &&  true






COPY . .


RUN pip install --no-cache-dir .


ENV PYTHONUNBUFFERED=1


CMD ["python"]
