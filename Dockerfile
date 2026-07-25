FROM python:3.11-slim

# Minimal system deps (save RAM)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libssl-dev libffi-dev curl \
    && apt-get clean

WORKDIR /app

# Install Python deps (strip heavy numpy to save ~100MB RAM)
COPY requirements.txt .
RUN pip install --no-cache-dir --no-deps numpy==1.24.0 \
    && pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Back4app provides PORT automatically
ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV PYTHONHASHSEED=0

# Reduce memory footprint
ENV MALLOC_TRIM_THRESHOLD_=65536

# Health check
HEALTHCHECK --interval=60s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# Start bot
CMD ["python", "-O", "bot/main.py"]
