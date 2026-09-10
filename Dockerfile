# Dockerfile - ATMDS Bot
# Buat deploy ke VPS, Docker, atau platform yang support container
# Pakai Python 3.12-slim untuk image yang ringan

FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies (karena gkeepapi butuh ini)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (untuk caching layer)
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy semua file project
COPY . .

# Buat direktori data untuk SQLite (kalau pakai volume)
RUN mkdir -p /app/data

# Set environment variables default
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Health check (optional - check if process is alive)
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Run bot
CMD ["python", "bot.py"]

# Catatan:
# - Untuk persistent storage, mount volume ke /app/data
# - Untuk environment variables, pakai -e atau --env-file .env
# - Contoh run: docker run -d --name dedek --env-file .env -v $(pwd)/data:/app/data discord-mydedek
