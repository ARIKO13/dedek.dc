# Dockerfile - ATMDS Bot
# Optimize untuk Fly.io (256MB RAM) dan VPS deploy
# Pakai Python 3.12-slim untuk image yang ringan

FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies yang dibutuhkan:
# - build-essential: buat compile Python packages (cryptography, dll)
# - libffi-dev, libssl-dev: buat cryptography (gkeepapi dependency)
# - curl: buat health check
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    libssl-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (untuk caching layer - build lebih cepat kalau requirements.txt gak berubah)
COPY requirements.txt .

# Install Python dependencies tanpa cache (hemat storage)
RUN pip install --no-cache-dir -r requirements.txt

# Copy semua file project
COPY . .

# Buat direktori data untuk SQLite (persistent volume di Fly.io)
# Default DB_PATH = /app/data/atmds.db
RUN mkdir -p /app/data

# Set environment variables default
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV DB_PATH=/app/data/atmds.db
ENV TZ=Asia/Jakarta

# Health check: pastikan process masih jalan (cegah OOM kill yang gak terdeteksi)
# Fly.io auto-restart kalau health check gagal
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD pgrep -f "python bot.py" > /dev/null || exit 1

# Run bot
CMD ["python", "bot.py"]

# Catatan:
# - Image size: ~150MB (cukup kecil buat Fly.io)
# - Memory usage: ~150-200MB saat jalan (cukup untuk 256MB VM)
# - Persistent storage: mount volume ke /app/data (lihat fly.toml)
# - Untuk VPS: tinggal docker run -d --name dedek --env-file .env -v $(pwd)/data:/app/data dedek-bot
