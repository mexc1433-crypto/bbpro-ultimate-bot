FROM python:3.11-slim

# System deps for SSL/TLS (cTrader needs it)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libssl-dev libffi-dev curl \
    && rm -rf /var/lib/apt/lists/*

# App directory
WORKDIR /app

# Install Python deps first (better caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Koyeb uses PORT env var (default 8000)
ENV PORT=8000
ENV PYTHONUNBUFFERED=1

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# Start bot
CMD ["python", "bot/main.py"]
