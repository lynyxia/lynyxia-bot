# Modular Twitch Chatbot

A Python 3.12+ framework for building Twitch chatbots out of small, independent
modules. This repository currently contains the **foundation milestone**: core
lifecycle, module discovery, configuration, event routing, PostgreSQL/Alembic
infrastructure, Docker deployment and tests.

The three initial feature modules (`ai_chat`, `quotes`, `chat_logger`) are
**architecture stubs only** — their command and persistence behaviour arrives
in later milestones. The database schema and repository interfaces they will
use already exist.

---

## 1. Architecture overview

```
src/bot/
├── main.py                  # entry point, signal handling
├── config.py                # all configuration (Pydantic Settings)
├── core/
│   ├── application.py       # composition root + lifecycle
│   ├── bot.py               # the only TwitchIO-coupled component
│   ├── module_manager.py    # discovery, enable/disable, lifecycle
│   ├── event_router.py      # event + command registration/dispatch
│   └── permissions.py       # admin checks
├── modules/
│   ├── base.py              # Module / ModuleContext contract
│   └── <feature>/           # one Module subclass per package
├── services/
│   ├── database.py          # engine, session factory, pooling, retry
│   ├── twitch.py            # thin client wrapper
│   └── llm.py               # LLM provider *interface* only
├── database/
│   ├── models/              # SQLAlchemy 2.x ORM models
│   ├── repositories/        # all data access
│   └── migrations/          # Alembic environment + revisions
└── utils/logging.py
```

### Design decisions

- **Modules never import other modules.** They receive a `ModuleContext`
  containing their own config, an `EventRouter`, shared services and a logger.
- **TwitchIO is isolated.** `core/bot.py` translates Twitch events into calls
  on the router; modules see a plain `CommandContext`. This makes modules easy
  to test without Twitch. *Trade-off:* we reimplement simple `!command`
  prefix parsing instead of using TwitchIO's decorator command system.
- **Discovery by package scanning.** `pkgutil` + `importlib` find `Module`
  subclasses inside `bot.modules`; `BOT_ENABLED_MODULES` decides which load.
  Entry-point plugins would allow external distributions but add packaging
  complexity, so they are deferred.
- **No hot-reloading** (per requirements).
- **Alembic only** for schema changes; the application never creates tables.
- **`SecretStr` everywhere** for credentials, so they cannot leak via reprs.
- In-package discovery means a module is enabled by adding its package under
  `src/bot/modules/` and listing its `name` in `BOT_ENABLED_MODULES`.

### Module lifecycle

1. `setup()` — validate config, register handlers/subscriptions.
2. `start()` — begin background work, after *all* modules are set up.
3. `stop()` — release resources; the base class unsubscribes everything
   registered through `self.subscribe(...)`.

Failures in any phase are logged and skipped, so one broken module cannot
prevent startup.

---

## 2. Prerequisites

- Docker **and** Docker Compose (recommended for running the app)
- Python 3.12+ and `pip` (for local development/tests)
- A Twitch application and a dedicated bot account
- PostgreSQL 16 (provided by Compose)

---

## 3. Initial setup

```bash
git clone <your-repo-url> Lynyxia-Bot
cd Lynyxia-Bot
cp .env.example .env
# Edit .env with real credentials (never commit it).
docker compose build
docker compose run --rm bot alembic upgrade head
docker compose up -d
docker compose logs -f bot
```

`.env` is ignored by Git. Only `.env.example` is committed.

---

## 4. Twitch application registration

1. Go to <https://dev.twitch.tv/console/apps> and **Register Your Application**.
2. Name it, set the OAuth Redirect URL to `http://localhost` (a bot using the
   client credentials flow does not need a live callback).
3. Choose category **Chat Bot**.
4. Copy the **Client ID** into `BOT_TWITCH__CLIENT_ID` and generate a
   **Client Secret** into `BOT_TWITCH__CLIENT_SECRET`.

### OAuth configuration

The bot connects with a user OAuth token for the dedicated bot account. Use an
IRC-focused token generator (e.g. <https://twitchapps.com/tmi/>) while logged
in as the bot account, and paste the full value **including the `oauth:` prefix**
into `BOT_TWITCH__OAUTH_TOKEN`.

Set:

- `BOT_TWITCH__BOT_USERNAME` — the bot account's login name.
- `BOT_TWITCH__CHANNEL` — the channel the bot should join.
- `BOT_ADMIN_USER_IDS` — JSON list of Twitch **user IDs** allowed to run admin
  commands. Find an ID at <https://www.twitch.tv/yourname> → use an ID lookup
  service or the Twitch API. Store IDs, not display names.

---

## 5. Environment variables

All variables are read in `src/bot/config.py`. Nested settings use `__`.

| Variable | Purpose |
| --- | --- |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | PostgreSQL container credentials |
| `BOT_TWITCH__CLIENT_ID`, `BOT_TWITCH__CLIENT_SECRET` | Twitch app credentials |
| `BOT_TWITCH__BOT_USERNAME`, `BOT_TWITCH__OAUTH_TOKEN` | Bot account identity |
| `BOT_TWITCH__CHANNEL` | Channel to join |
| `BOT_DATABASE__HOST/PORT/USER/PASSWORD/NAME` | Database connection (host is overridden to `db` in Compose) |
| `BOT_DATABASE__POOL_SIZE`, `BOT_DATABASE__MAX_OVERFLOW` | Connection pooling |
| `BOT_DATABASE__CONNECT_RETRIES`, `BOT_DATABASE__CONNECT_RETRY_DELAY` | Startup readiness retries |
| `BOT_LLM__PROVIDER/API_KEY/BASE_URL/MODEL/TIMEOUT_SECONDS/MAX_RESPONSE_CHARS` | Future LLM adapter config |
| `BOT_LOGGING__LEVEL` | Root log level (e.g. `INFO`) |
| `BOT_ENABLED_MODULES` | JSON list of module names to load |
| `BOT_ADMIN_USER_IDS` | JSON list of admin Twitch user IDs |
| `BOT_MODULE__<NAME>` | JSON object with that module's own config |

Example module configuration:

```bash
BOT_MODULE__AI_CHAT='{"system_prompt":"You are helpful.","cooldown_seconds":5}'
```

Modules read it via `self.context.config`. No secrets should be placed here
in plain text that isn't already in the environment.

---

## 6. Starting the application with Docker Compose

```bash
docker compose up -d --build
docker compose logs -f bot
```

- PostgreSQL data persists in the `pgdata` named volume.
- The `db` service has a `pg_isready` healthcheck; `bot` waits for it.
- `DatabaseService.connect()` additionally retries with backoff.
- Both containers use `restart: unless-stopped` and rotating JSON logs.
- The Python image is multi-stage and runs as a non-root `bot` user.
- Credentials come only from `.env`; none are baked into the image.

Stop everything (data volume is preserved):

```bash
docker compose down
```

Add `-v` to also delete the database volume.

---

## 7. Running migrations

```bash
# Apply migrations
docker compose run --rm bot alembic upgrade head

# Create a new revision after editing src/bot/database/models/
docker compose run --rm bot alembic revision --autogenerate -m "describe change"

# Roll back one revision
docker compose run --rm bot alembic downgrade -1
```

Alembic reads the same validated `DatabaseSettings` as the application. The
initial revision `0001` creates `channels`, `users` and `chat_messages`, using
Twitch IDs as stable identifiers and appropriate indexes/constraints.

---

## 8. Running tests

Local development:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
ruff check .
pytest
```

Unit tests require no external services. Database integration tests are
skipped unless you point them at a disposable PostgreSQL database:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://bot:pw@localhost:5432/test_db pytest
```

The suite covers configuration, event/command dispatch, permissions, module
discovery and lifecycle, failure isolation, application startup/shutdown, and
repository round-trips. Nothing contacts real Twitch or a paid LLM API.

---

## 9. Adding a new module

Create a package under `src/bot/modules/` containing exactly one `Module`
subclass with a unique `name`:

```python
# src/bot/modules/greeter/__init__.py
from bot.modules.base import Module


class GreeterModule(Module):
    name = "greeter"

    async def setup(self) -> None:
        # Register command and event handlers. The base class removes them
        # automatically during stop().
        self.subscribe(self.context.router.on_command("hello", self.on_hello))
        self.subscribe(self.context.router.on_event("message", self.on_message))

    async def on_hello(self, ctx) -> None:
        await ctx.reply(f"Hello @{ctx.author_name}!")

    async def on_message(self, message) -> None:
        # `message` is the raw TwitchIO Message. Keep heavy work off the
        # event loop (e.g. schedule it with asyncio.create_task).
        pass

    async def start(self) -> None:
        self.log.info("Greeter ready")

    async def stop(self) -> None:
        self.log.info("Greeter stopping")
```

Then add it to configuration and restart:

```bash
BOT_ENABLED_MODULES='["chat_logger","quotes","ai_chat","greeter"]'
```

`settings.enabled_modules` determines what loads; discovery itself is
automatic. Removed or misspelled names are silently skipped with a log line.

### Available services

Inside a module, `self.context.services` provides:

- `database` — `DatabaseService`; use `async with self.context.services.database.session() as session:` or an injected repository.
- `twitch` — `await services.twitch.send_message(channel, text)`.
- `permissions` — `services.permissions.is_admin(ctx.author_id)` for admin commands.
- `llm` — currently `None`; an `LLMProvider` implementation will be wired in with the AI module.

Always inject a repository instead of creating your own engine, and never
perform blocking I/O on the event loop.

---

## 10. Enabling and disabling modules

Edit `BOT_ENABLED_MODULES` in `.env` (a JSON array of module `name` values) and
restart the container. Discovery logs every module found, every module
skipped as disabled, and every module loaded/stopped. Hot-reloading is not
supported in this milestone.

---

## 11. Status and outstanding work

**Done in this milestone**

- Centralized configuration with secret redaction and validation
- Core lifecycle, graceful signal shutdown, Twitch connection wrapper
- Module discovery, config-based enable/disable, isolated lifecycle failures
- Event/command router with unsubscribe support
- Async database service with pooling and readiness retries
- SQLAlchemy models, repositories and Alembic initial migration
- Multi-stage non-root Docker image, Compose with healthchecks and volumes
- Unit + optional integration tests, Ruff configuration

**Explicitly deferred to later milestones**

- AI Chat (`!chat`, `!chatsettings` allowlist, LLM adapter, cooldowns)
- Quotes (`!rq`, `!rq username`)
- Chat logger batching/retention
- LLM provider dependency and concrete adapter
- Hot-reloading

If you want a different command-dispatch strategy (native TwitchIO commands)
or external/entry-point plugins before those milestones, that change is best
made now, while the module surface is still small.
