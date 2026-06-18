# syntax=docker/dockerfile:1
FROM python:3.11-slim

# System deps for OpenCV headless
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY app/       ./app/
COPY pipeline/  ./pipeline/
COPY scripts/   ./scripts/
COPY contracts/ ./contracts/
COPY dashboard/ ./dashboard/
COPY data/events_phase8_submission.jsonl ./data/events_phase8_submission.jsonl

# Data directory is mounted as a volume; create it so it exists at build time
RUN mkdir -p /app/data/reports

# Expose API and dashboard ports
EXPOSE 8000
EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
