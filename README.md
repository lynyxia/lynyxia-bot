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
│   ├── chat_logger/         # batched chat message persistence
│   ├── quotes/              # !rq commands
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
- **TwitchIO is isolated.** `core/bot.py` translates Twitch events into a
  normalized `ChatMessageEvent` and `CommandContext`; modules never touch
  TwitchIO objects. This makes modules easy to test without Twitch. *Trade-off:*
  we reimplement simple `!command` prefix parsing instead of using TwitchIO's
  decorator command system.
- **Discovery walks packages recursively**, so a feature's `Module` subclass
  may live in a submodule (e.g. `chat_logger/module.py`) while its package
  `__init__.py` re-exports it.
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

### Per-channel broadcaster authorization

Chat replies prefer the **app access token** so that Twitch applies its **Chat
Bot badge**. Twitch requires each broadcaster to grant `channel:bot` to the app
(or the bot to be a moderator) before an app token may send in that channel.

On startup the bot logs one authorization URL for every configured channel that
has no stored authorization. The channel owner opens their URL and logs in as
the **broadcaster**. The callback stores that token in the
`channel_authorizations` table, and TwitchIO refreshes it automatically.

- Requested scopes are configured with `BOT_TWITCH__CHANNEL_SCOPES` (default
  `["channel:bot"]`).
- If an app-token send returns `401`, the bot retries once with its own managed
  **user token**, so commands keep working before a channel is authorized (or
  while its grant is revoked). Those messages do **not** get the Chat Bot
  badge; a single info log per channel explains how to fix that.
- A successful app-token send (including channels where the bot is a moderator)
  keeps the badge and does not use the fallback.

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
| `BOT_TWITCH__OAUTH_SCOPES` | JSON array of scopes requested at bot authorization |
| `BOT_TWITCH__CHANNEL_SCOPES` | JSON array of scopes each broadcaster grants |
| `BOT_DATABASE__HOST/PORT/USER/PASSWORD/NAME` | Database connection (host is overridden to `db` in Compose) |
| `BOT_DATABASE__POOL_SIZE`, `BOT_DATABASE__MAX_OVERFLOW` | Connection pooling |
| `BOT_DATABASE__CONNECT_RETRIES`, `BOT_DATABASE__CONNECT_RETRY_DELAY` | Startup readiness retries |
| `BOT_LLM__PROVIDER/API_KEY/BASE_URL/MODEL/TIMEOUT_SECONDS/MAX_RESPONSE_CHARS` | Future LLM adapter config |
| `BOT_LOGGING__LEVEL` | Root log level (e.g. `INFO`) |
| `BOT_CHAT__MIN_INTERVAL_SECONDS` | Minimum gap between outgoing chat messages (default `1.5`) |
| `BOT_CHAT__QUEUE_MAXSIZE` | Bounded outbound send queue size |
| `BOT_CHAT__MAX_RETRIES`, `BOT_CHAT__BACKOFF_BASE_SECONDS` | 429 retry/backoff settings |
| `BOT_CHAT__BANNED_PHRASES` | JSON array of case-insensitive phrases to filter |
| `BOT_CHAT__BANNED_PHRASE_REPLACEMENT` | Text sent instead of a message containing a banned phrase |
| `BOT_ENABLED_MODULES` | JSON list of module names to load |
| `BOT_ADMIN_USER_IDS` | JSON list of admin Twitch user IDs |
| `BOT_MODULE__<NAME>` | JSON object with that module's own config |

Example module configuration:

```bash
BOT_MODULE__AI_CHAT='{"system_prompt":"You are helpful.","cooldown_seconds":5}'
BOT_MODULE__CHAT_LOGGER='{"batch_size":100,"flush_interval_seconds":2,"queue_maxsize":2000,"retention_days":30}'
```

Modules read it via `self.context.config` and validate it with their own
Pydantic model. No secrets should be placed here in plain text that isn't
already in the environment.

### Chat logger settings

| Key | Default | Purpose |
| --- | --- | --- |
| `batch_size` | `50` | Maximum messages per database write |
| `flush_interval_seconds` | `2.0` | Maximum time a partial batch waits |
| `queue_maxsize` | `1000` | Bounded queue; excess messages are dropped, never blocking chat |
| `retention_days` | `null` | Future cleanup policy; repository support exists, scheduling does not |

The logger enqueues normalized `ChatMessageEvent`s in `on_message()` and a
single background worker writes batches. Channel/user rows are upserted in the
same transaction, and duplicate Twitch message IDs are ignored. Each message
stores both the stable Twitch `user_id` and the chatter's `username`. All
timestamps are normalized to UTC before insertion, and every pooled database
connection uses the UTC session timezone. On shutdown the queue is drained
before the worker exits.

### Chat send pacing

All outgoing chat messages — replies from modules, `TwitchService.send_message()`,
and the user-token fallback — go through one bounded queue with a single worker.
The worker waits at least `BOT_CHAT__MIN_INTERVAL_SECONDS` (default `1.5`, roughly
twenty messages per thirty seconds) between sends, so the limit is enforced
globally across every channel instead of per handler. `429` responses are
retried with `Retry-After` when available and exponential backoff otherwise;
the queue is bounded, so bursts are dropped and counted rather than creating
unbounded tasks.

Every message is also checked against `BOT_CHAT__BANNED_PHRASES` (case-insensitive
substrings) before it is queued. A match replaces the message with
`BOT_CHAT__BANNED_PHRASE_REPLACEMENT` and logs
`banned phrase detected in: <original message>` to the console.

### Quotes settings

| Key | Default | Purpose |
| --- | --- | --- |
| `excluded_users` | `[]` | Twitch logins or numeric IDs excluded from `!rq` |

Example:

```bash
BOT_MODULE__QUOTES='{"excluded_users":["someuser","123456789"]}'
```

`!rq` returns a random message from the current channel. `!rq <username>`
resolves the login to a stable Twitch ID and picks a random message from that
user. The reply uses the `username` stored with the message, so quotes from
renamed users keep their old name. Requests for excluded or unknown users reply
`User not found.`, and excluded users are also omitted from bare `!rq` results.

All `!rq` replies are posted as a Twitch **threaded reply** to the command
message, not as a standalone chat message. The quote format is:

```
[2021-08-11 17:25:54] @Kappa123: yeah, nice socks Kappa
```

The timestamp is the message's UTC `sent_at` formatted as `YYYY-MM-DD HH:MM:SS`.

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
adds `oauth_tokens` for the bot-managed token pair; revision `0003` adds the
chatter's `username` to each logged message; revision `0004` adds
`channel_authorizations` for per-channel broadcaster grants.

### Importing legacy SQLite logs

`bot.tools.import_sqlite_logs` generates a PostgreSQL SQL script from an old
`logs.db`; it does **not** write to the database itself. Its source table must
contain `id`, `datetime`, `username` and `message`; `datetime` is
`YYYY-MM-DD HH:MM:SS` in UTC. The SQLite `id` is **ignored** and PostgreSQL
assigns a new one.

```bash
# Put logs.db in the project root first; it is gitignored.
docker compose run --rm bot python -m bot.tools.import_sqlite_logs \
  --source logs.db --user-map user_map.json --output legacy_import.sql --dry-run

# Drop --dry-run to generate the SQL file, then apply it yourself:
psql "$DATABASE_URL" -f legacy_import.sql
```

The tool asks for the channel's numeric ID and login, then asks once for the
Twitch user ID of each distinct username. Answers are cached in `user_map.json`
so reruns don't re-prompt; pass an existing map with `--user-map`. `--dry-run`
previews the SQL without writing any files.

The generated script:

- upserts the channel and user rows;
- inserts messages with `twitch_message_id` left `NULL`;
- lowercases `login` while keeping the original `display_name`;
- recomputes `first_seen_at` / `last_seen_at` from the imported messages;
- guards each message insert with `WHERE NOT EXISTS`, so applying the script
  twice does not duplicate rows (matched on UTC timestamp, username, content).

The tool needs no database credentials at all — only the SQLite file and your
answers. Review the generated SQL before running it against PostgreSQL.

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
from bot.core.event_router import ChatMessageEvent
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
        # Use ctx.reply_threaded(text) to answer inside the command's thread.

    async def on_message(self, event: ChatMessageEvent) -> None:
        # `event` is a normalized ChatMessageEvent (no TwitchIO objects).
        # Keep heavy work off the event loop: enqueue it for a worker task.
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

**Milestone 1 — foundation (done)**

- Centralized configuration with secret redaction and validation
- Core lifecycle, graceful signal shutdown, TwitchIO 3 EventSub chat subscription
- Module discovery, config-based enable/disable, isolated lifecycle failures
- Event/command router with unsubscribe support
- Async database service with pooling and readiness retries
- SQLAlchemy models, repositories and Alembic migrations (schema + OAuth tokens)
- Bot-managed Twitch OAuth with PostgreSQL persistence and automatic refresh
- Multi-stage non-root Docker image, Compose with healthchecks and volumes
- Unit + optional integration tests, Ruff configuration

**Milestone 2 — chat logger (done)**

- Normalized `ChatMessageEvent`, bounded queue, batched async writes, atomic
  channel/user upserts, username logging, UTC timestamps, graceful drain

**Milestone 3 — quotes (done)**

- `!rq` and `!rq <username>`, excluded-user configuration, threaded replies
  using the username recorded with each message

**Milestone 4 — operations (done)**

- Legacy SQLite log importer generating a reviewable SQL script
- Per-channel broadcaster OAuth so app-token sends keep the Chat Bot badge
- Shared, rate-limited outbound chat queue with 429 backoff

**Explicitly deferred to later milestones**

- AI Chat (`!chat`, `!chatsettings` allowlist, LLM adapter, cooldowns)
- Retention cleanup scheduling (repository method exists)
- LLM provider dependency and concrete adapter
- Hot-reloading

If you want a different command-dispatch strategy (native TwitchIO commands)
or external/entry-point plugins before those milestones, that change is best
made now, while the module surface is still small.
