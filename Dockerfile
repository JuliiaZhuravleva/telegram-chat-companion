FROM python:3.12-slim

WORKDIR /app

# Copy build inputs (hatchling needs README + src to build)
COPY pyproject.toml README.md ./
COPY src/ src/

# Install package and dependencies (cached unless source changes)
RUN pip install --no-cache-dir .

# Copy remaining files (config, sql, etc.)
COPY . .

CMD ["python", "-m", "src.main"]
