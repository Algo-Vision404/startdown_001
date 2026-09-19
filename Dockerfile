# Dockerfile
#
# Render will use this instead of its default Python environment.
# This gives us full control over system dependencies.
# cmake is required to compile liboqs from source.

FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    cmake \
    build-essential \
    git \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy and install Python dependencies first
# Docker caches this layer — if requirements.txt hasn't changed,
# it skips reinstalling on every deploy
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all source files
COPY . .

# Create data directory
RUN mkdir -p data

# Render sets PORT environment variable automatically
# Our app reads it
ENV PORT=9000

CMD ["python", "main.py"]
