# Mynota Client Assistant

Telegram bot for managing B2B clients with Claude-driven insights. Built on the
Telegram Business API with multi-tenant support (organization → managers → clients).

## Stack

- Python 3.12, aiogram 3.13+
- SQLAlchemy 2.0 (async) + asyncpg + Alembic
- PostgreSQL 16
- Anthropic SDK (Claude Sonnet 4.5 + Haiku 4.5)
- APScheduler with SQLAlchemyJobStore on Postgres
- aiohttp health endpoint
- Docker Compose

## Quick start

1. Copy the environment template and fill in the secrets:

   ```bash
   cp .env.example .env
   ```

   Required: `BOT_TOKEN`, `ANTHROPIC_API_KEY`, `OWNER_TELEGRAM_ID`,
   `POSTGRES_PASSWORD` (align `DATABASE_URL` if you change the user/password).

2. Build and start the stack — the entrypoint runs `alembic upgrade head`
   automatically before starting the bot:

   ```bash
   docker compose up -d --build
   ```

3. Tail the logs and look for `Bot started`:

   ```bash
   docker compose logs -f app
   ```

4. Hit the healthcheck (also wired into the compose `healthcheck`):

   ```bash
   curl http://localhost:8080/health
   ```

## Commands (in the bot's DM)

| Command | What it does |
|---|---|
| `/start` | Greet, check that the Telegram Business link is active. |
| `/clients` | All clients in the org with message counts + last touch. |
| `/clients_no_tg` | Clients without a Telegram identity (manual entries). |
| `/client <slug>` | Full client card: profile, last 10 messages, pending reminders. |
| `/profile <slug>` | AI-maintained profile snapshot. |
| `/suggest <slug>` | 2–3 reply variants (formal / friendly / closing). |
| `/add_client` | FSM to create a manual client (off-Telegram channels). |
| `/note <slug>` | Record an inbound message (text or screenshot, OCR'd). |
| `/sent <slug>` | Record an outbound message you sent off-Telegram. |
| `/remind <slug> <when> <text>` | Create a reminder (`when` parsed in Russian: `завтра 10:00`, `через 3 дня`, `пятница 14:00`). |
| `/reminders` | Active reminders. |
| `/done <short_id>` | Close a reminder (8-char UUID prefix). |
| `/cancel` | Exit any FSM flow. |

## Adding another manager (e.g. a partner / spouse)

The owner is seeded automatically from `OWNER_TELEGRAM_ID`. To add another
manager into the same organization:

1. Have them DM the bot once so you know their Telegram user id, or look it up
   via `@userinfobot`.
2. Run the bundled script inside the container:

   ```bash
   docker compose exec app python -m scripts.add_manager 123456789 "Имя" --role manager
   ```

   Pass `--role owner` if you want them to have the same privileges as you.
   The script is idempotent — re-running it for an existing user is a no-op.

After adding them, they need to connect the bot on their own Telegram account
(next section). They get their own digest, their own reminders, and their own
view of the clients they own.

## Connecting the bot to a Telegram Business account

Each manager does this on their own phone:

1. Telegram → **Settings → Telegram Business** (requires Telegram Premium).
2. **Chatbots** → search for the bot by `@username` → tap it.
3. Grant the **Reply** right.
4. Send `/start` to the bot in a regular chat to confirm the link is active.

The bot will start receiving `business_connection` updates immediately and
record any new client chats as messages with `source=tg_business`.

## Viewing logs

```bash
# Tail live logs
docker compose logs -f app

# Last 200 lines
docker compose logs --tail=200 app

# Postgres logs (less interesting but here when you need them)
docker compose logs -f postgres

# Filter by error correlation id printed in user-facing "лог N" messages
docker compose logs app | grep '\[err:abcd1234\]'
```

Errors raised inside a handler are caught by a global error handler — the
manager sees `⚠️ Что-то сломалось. Лог: <id>` and the same id is in the
log line.

## Project layout

```
src/
├── ai/              # Claude prompts, client, analyzer, responder, profiler, OCR
├── bot/             # aiogram handlers, middleware, error handler
├── db/              # SQLAlchemy session, models, seed
├── scheduler/       # APScheduler jobs (digest, reminders, profile refresh)
├── services/        # Business logic (clients, reminders, digest, pipeline)
├── utils/           # Helpers (logger, slug)
├── config.py        # pydantic-settings
├── health.py        # aiohttp /health endpoint
└── main.py          # entry point

migrations/          # Alembic migrations
scripts/             # Operational scripts (add_manager.py)
tests/               # pytest suite
```

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Lint + format
ruff check src tests
ruff format src tests

# Pre-commit hooks (run ruff + ruff-format on commit)
pre-commit install

# Tests — need a Postgres available at TEST_DATABASE_URL
createdb mynota_test  # one-off
TEST_DATABASE_URL=postgresql+asyncpg://postgres@localhost:5432/mynota_test pytest
```
