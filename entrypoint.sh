#!/bin/sh
set -e

# Apply pending DB migrations before starting the bot. Idempotent — a no-op
# if the schema is already current.
echo "[entrypoint] alembic upgrade head"
alembic upgrade head

echo "[entrypoint] starting bot"
exec python -m src.main
