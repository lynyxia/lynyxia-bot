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
2. Name it, choose category **Chat Bot**.
3. Set the **OAuth Redirect URL** to exactly
   `http://localhost:8080/oauth/callback` (or whatever you configure in
   `BOT_TWITCH__OAUTH_REDIRECT_URI`). The bot serves this callback itself.
4. Copy the **Client ID** into `BOT_TWITCH__CLIENT_ID` and generate a
   **Client Secret** into `BOT_TWITCH__CLIENT_SECRET`.

### OAuth configuration (bot-managed)

`BOT_TWITCH__CLIENT_SECRET` is **required** by TwitchIO 3.x — it validates the
app and mints the app token used for general API calls.

TwitchIO 3 delivers chat through **EventSub**, not IRC, so an IRC/TMI token is
not useful here. You do **not** configure a user token manually. On first start
the bot:

1. Logs a one-time authorization URL.
2. Serves the OAuth callback on `BOT_TWITCH__OAUTH_PORT` via TwitchIO's built-in
   aiohttp adapter (no Starlette dependency).
3. Stores the resulting access + refresh token in the `oauth_tokens`
   PostgreSQL table.
4. Refreshes the token automatically and persists rotations to that table, so
   restarts keep working.

Open the logged URL once and log in as the **bot account**, granting:

```
user:read:chat user:write:chat user:bot channel:bot
```

The callback port is published by Compose. If the bot runs on a remote host,
set `BOT_TWITCH__OAUTH_REDIRECT_URI` to a publicly reachable URL and configure
the app registration accordingly.

EventSub also needs stable numeric IDs, not login names:

- `BOT_TWITCH__BOT_ID` — the bot account's numeric user ID
  (`twitch api get users -q login=<bot-login>`).
- `BOT_TWITCH__CHANNEL_ID` — a JSON array of the numeric user IDs of **every**
  channel the bot should join.
- `BOT_TWITCH__CHANNEL` — a matching JSON array of channel login names, used
  for logging/readability. The two arrays must have the same length.

The bot listens in multiple channels at once. Single strings are still
accepted and normalized to one-element arrays:

```bash
BOT_TWITCH__CHANNEL=["first-channel","second-channel"]
BOT_TWITCH__CHANNEL_ID=["987654321","123123123"]
```

`BOT_ADMIN_USER_IDS` is a JSON list of Twitch **user IDs** allowed to run admin
commands — store IDs, never display names.

Once a token exists, the bot subscribes to `channel.chat.message` for **each**
ID in `BOT_TWITCH__CHANNEL_ID`. Each `CommandContext` carries the originating
channel's ID and name, and `ctx.reply()` answers in that same channel. Token
values are never logged, and no token file is written to disk.

> **Startup log note:** `twitchio.client: If you require the StarletteAdapter
> please install ... 'pip install twitchio[starlette]'` is informational. This
> bot uses the built-in aiohttp adapter, so the extra package is not needed.

---

## 5. Environment variables

All variables are read in `src/bot/config.py`. Nested settings use `__`.

| Variable | Purpose |
| --- | --- |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | PostgreSQL container credentials |
| `BOT_TWITCH__CLIENT_ID`, `BOT_TWITCH__CLIENT_SECRET` | Twitch app credentials |
| `BOT_TWITCH__BOT_USERNAME` | Bot account login (informational) |
| `BOT_TWITCH__BOT_ID` | Bot account's numeric ID |
| `BOT_TWITCH__CHANNEL_ID` | JSON array of numeric IDs for all channels |
| `BOT_TWITCH__CHANNEL` | JSON array of matching channel names (informational) |
| `BOT_TWITCH__OAUTH_REDIRECT_URI` | Public callback URL registered with Twitch |
| `BOT_TWITCH__OAUTH_BIND_HOST`, `BOT_TWITCH__OAUTH_PORT` | Bind address/port for the callback server |
| `BOT_TWITCH__OAUTH_SCOPES` | JSON array of scopes requested at authorization |
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
- Compose publishes the OAuth callback port (default `8080`) so you can
  complete the one-time authorization from your browser.

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
revision `0001` creates `channels`, `users` and `chat_messages`, using Twitch
IDs as stable identifiers and appropriate indexes/constraints. Revision `0002`
adds `oauth_tokens` for the bot-managed token pair.

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
- `twitch` — `await services.twitch.send_message(channel_id, text)` (sends to a specific channel by numeric ID).
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
- Core lifecycle, graceful signal shutdown, TwitchIO 3 EventSub chat subscription
- Module discovery, config-based enable/disable, isolated lifecycle failures
- Event/command router with unsubscribe support
- Async database service with pooling and readiness retries
- SQLAlchemy models, repositories and Alembic migrations (schema + OAuth tokens)
- Bot-managed Twitch OAuth with PostgreSQL persistence and automatic refresh
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
