#!/bin/bash
# Enables pgvector before the bot applies alembic migrations.
# The bot container runs `alembic upgrade head` on startup.

set -e

echo "Enabling pgvector extension..."

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS vector;
EOSQL

echo "pgvector extension enabled"
