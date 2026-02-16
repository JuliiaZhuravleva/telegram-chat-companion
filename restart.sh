#!/usr/bin/env bash
# Rebuild and restart the bot container (keeps postgres running)
set -e

cd "$(dirname "$0")"

echo "==> Rebuilding bot image..."
docker compose build bot

echo "==> Restarting bot..."
docker compose up -d bot

echo "==> Tailing logs (Ctrl+C to stop)..."
docker compose logs -f --tail=30 bot
