# Use slim Python base
FROM python:3.11-slim

# Create app directory
WORKDIR /app

# Install system deps needed for building psycopg2
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential libpq-dev gcc curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy dependency files first to use layer cache
COPY requirements.txt .

# Install Python deps
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Healthcheck script
COPY healthcheck.sh /usr/local/bin/healthcheck.sh
RUN chmod +x /usr/local/bin/healthcheck.sh
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Create runtime data dir (if using SQLite for quick demos)
RUN mkdir -p /app/data

# Expose port (convention)
EXPOSE 8000

# Use gunicorn with uvicorn workers; use PORT env var provided by hosts
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
