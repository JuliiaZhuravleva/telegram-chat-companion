FROM python:3.12-slim

WORKDIR /app

# System dependencies for sticker rendering
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

# Copy build inputs (hatchling needs README + src to build)
COPY pyproject.toml README.md ./
COPY src/ src/

# Install package and dependencies (cached unless source changes)
RUN pip install --no-cache-dir .

# Copy remaining files (config, sql, etc.)
COPY . .

CMD ["sh", "-c", "alembic upgrade head && python -m src.main"]
