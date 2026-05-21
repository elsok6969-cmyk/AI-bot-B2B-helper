# Mynota Client Assistant

B2B client manager with three ingest channels (Telegram bot Business API,
personal Telegram via Telethon, Yandex Mail IMAP) and a local web dashboard
for triaging AI-generated reply drafts. Built around a Claude-driven pipeline
(analyzer + responder + profiler) with multi-tenant support
(organization → managers → clients).

## Stack

- Python 3.12, aiogram 3.13+
- FastAPI + Jinja2 + HTMX (local-only web UI on `127.0.0.1:8090`)
- Telethon (personal Telegram account ingest)
- aioimaplib + aiosmtplib (Yandex Mail IMAP/SMTP)
- SQLAlchemy 2.0 (async) + asyncpg + Alembic
- PostgreSQL 16
- Anthropic SDK (Claude Sonnet 4.5 + Haiku 4.5)
- APScheduler with SQLAlchemyJobStore on Postgres
- aiohttp health endpoint
- Docker Compose

## Channels

The system ingests inbound from three sources, persists them into the same
`messages` table, runs them through the same AI pipeline, and exposes them in
the web dashboard. Outbound is **never automatic** — AI drafts wait for your
click before being sent.

| Channel | Ingest | Outbound |
|---|---|---|
| Telegram Business (bot) | `business_message` webhook (handled by the existing aiogram bot) | manual — bot can't post for you, so the draft is just a copy-and-paste aid |
| Telegram personal (Telethon) | `NewMessage` events on private chats from the authorized user account | sent via the same Telethon client, on explicit click |
| Yandex Mail | IMAP poller, every `MAIL_POLL_INTERVAL_MINUTES` | SMTP on explicit click |

## Web dashboard

After `docker compose up -d --build`, open <http://localhost:8090>:

- `/` — summary tiles (clients, drafts queue, due reminders)
- `/clients` — searchable list, client cards with full history
- `/drafts` — AI-generated reply queue: pick a variant, edit, click **Отправить**
- `/mail` — configure your Yandex mailbox (use an [app password](https://yandex.ru/support/id/authorization/app-passwords.html), not the main one)
- `/telethon` — phone-number login flow for your personal Telegram account
- `/settings` — read-only view of the relevant `.env` knobs

## Quick start (macOS — double-click)

> Для macOS создан файл **`Start.command`**. Двойной клик по нему:
> 1. Откроет терминал
> 2. Проверит Python, Homebrew, PostgreSQL
> 3. Создаст `.env` (если нужно), БД, виртуальное окружение
> 4. Установит все зависимости, накатит миграции и запустит бота
>
> **Перед первым запуском** отредактируй `.env` и укажи `BOT_TOKEN`, `OWNER_TELEGRAM_ID` и API-ключ.
>
> Если при двойном клике открывается TextEdit вместо терминала: **ПКМ → Открыть с помощью → Terminal** (один раз, дальше будет работать двойным кликом).

## Quick start (Docker)

1. Copy the environment template and fill in the secrets:

   ```bash
   cp .env.example .env
   ```

   Required for the bot to run: `BOT_TOKEN`, `OWNER_TELEGRAM_ID`,
   `POSTGRES_PASSWORD` (align `DATABASE_URL` if you change the
   user/password), plus the API key for your chosen `AI_PROVIDER`:
   `ANTHROPIC_API_KEY` for production, or `KIMI_API_KEY` (with
   `AI_PROVIDER=kimi`) for cheap testing — see
   [Testing with Kimi](#testing-with-kimi-instead-of-anthropic).

   Additionally required for the new channels:

   - `SECRETS_KEY` — Fernet key used to encrypt the Telethon session and
     the Yandex mailbox password at rest. Generate one with:

     ```bash
     python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
     ```

   - `TELETHON_API_ID` + `TELETHON_API_HASH` — get them from
     <https://my.telegram.org> → API Development Tools. Required only if
     you want Telethon ingest of your personal account.

   The web UI itself needs no extra config — it serves on
   `127.0.0.1:8090` by default. Mail polling defaults to every 2
   minutes.

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

## Testing with Kimi instead of Anthropic

Anthropic credits aren't cheap. For end-to-end testing you can switch the
bot to [Moonshot AI's Kimi](https://platform.moonshot.ai/) — same code
path, OpenAI-compatible API, an order of magnitude cheaper. The four AI
features (analyzer, responder, profiler, OCR) all run through the same
`AIClient`; the only thing that changes is the provider behind it.

In `.env`:

```ini
AI_PROVIDER=kimi
KIMI_API_KEY=sk-...                # from https://platform.moonshot.ai/
# Optional — defaults shown:
KIMI_BASE_URL=https://api.moonshot.ai/v1
KIMI_MODEL_FAST=moonshot-v1-8k
KIMI_MODEL_SMART=kimi-k2-0905-preview
KIMI_MODEL_VISION=moonshot-v1-32k-vision-preview
```

Leave `ANTHROPIC_API_KEY` blank — only the active provider's key is
required. Re-build and restart:

```bash
docker compose up -d --build
docker compose logs -f app | grep "AI provider"  # should print: AI provider: kimi @ https://api.moonshot.ai/v1
```

When you're happy with the smoke test, flip `AI_PROVIDER=anthropic` and
restart for the production run.

> Notes: tool-call reliability and Russian language quality on Kimi are
> good but not identical to Claude — use Kimi for plumbing/UX testing,
> not for final tuning of prompts. The `kimi-k2-0905-preview` SMART
> model and `moonshot-v1-*-vision-preview` are flagged as preview by
> Moonshot, so model IDs may shift; check the Moonshot console if a
> request 404s.

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
