# Mynota Client Assistant

Telegram bot for managing B2B clients with Claude-driven insights. Built on the
Telegram Business API with multi-tenant support (organization → managers → clients).

## Stack

- Python 3.12, aiogram 3.13+
- SQLAlchemy 2.0 (async) + asyncpg + Alembic
- PostgreSQL 16
- Anthropic SDK (Claude Sonnet 4.5 + Haiku)
- APScheduler (Postgres jobstore)
- Docker Compose

## Quick start

1. Copy the environment template and fill in the secrets:

   ```bash
   cp .env.example .env
   ```

   Required: `BOT_TOKEN`, `ANTHROPIC_API_KEY`, `OWNER_TELEGRAM_ID`,
   `POSTGRES_PASSWORD` (and align `DATABASE_URL` if you change the user/password).

2. Build and start the stack:

   ```bash
   docker compose up -d --build
   ```

3. Apply migrations:

   ```bash
   docker compose exec app alembic upgrade head
   ```

4. Tail the logs:

   ```bash
   docker compose logs -f app
   ```

   You should see `Bot started` once the bot connects to Telegram.

## Project layout

```
src/
├── ai/              # Claude prompts and pipeline
├── bot/             # aiogram handlers
│   └── handlers/
├── db/              # SQLAlchemy session and models
│   └── models/
├── scheduler/       # APScheduler jobs
├── services/        # Business logic
├── utils/           # Helpers (logger, etc.)
├── config.py        # pydantic-settings config
└── main.py          # entry point

migrations/          # Alembic migrations
tests/               # pytest suite
```

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check src tests
mypy src
pytest
```
