# Use slim Python base
FROM python:3.11-slim

# Create app directory
WORKDIR /app

# Install system deps needed for building psycopg2
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential libpq-dev gcc && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy dependency files first to use layer cache
COPY requirements.txt .

# Install Python deps
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create runtime data dir (if using SQLite for quick demos)
RUN mkdir -p /app/data

# Expose port (convention)
EXPOSE 8000

# Use gunicorn with uvicorn workers; use PORT env var provided by hosts
CMD ["gunicorn", "-k", "uvicorn.workers.UvicornWorker", "app.main:app", "--bind", "0.0.0.0:8000", "--workers", "2", "--log-level", "info"]
