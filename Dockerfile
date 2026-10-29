FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install curl for health checks
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Copy application files
COPY backend /app/backend
COPY frontend /app/frontend
COPY for_candidates /app/for_candidates
COPY run.py /app/run.py
COPY README.md /app/README.md

# Ensure persistent data directory exists
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["python", "run.py"]

