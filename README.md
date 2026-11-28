# Modular Twitch Chatbot

A Python 3.12+ framework for building Twitch chatbots out of small, independent
modules. The core provides lifecycle management, module discovery, configuration,
event routing, PostgreSQL/Alembic persistence and Docker deployment; the feature
modules are `chat_logger`, `quotes`, `ecount`, `lines`, `ai_chat`, `lynyxia` and
`livepass`.

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
│   ├── ai_chat/             # personas, allowlist, LLM + tools
│   ├── chat_logger/         # batched chat message persistence
│   ├── ecount/              # !ecount emote usage counts
│   ├── lines/               # !lines per-user message counts
│   ├── livepass/            # !livepass per-channel live allowlist
│   ├── lynxia/              # !lynyxia per-channel output toggle
│   ├── quotes/              # !rq commands
│   ├── trivia/              # !trivia/!hint/!pass, JSON category files
│   ├── pyramid/             # passive emote-pyramid watcher
│   ├── unused_emotes/       # !unusedemotes 7TV HTML reports
│   ├── emote_sets/          # !emotesets 7TV set comparison reports
│   └── <feature>/           # one Module subclass per package
├── services/
│   ├── database.py          # engine, session factory, pooling, retry
│   ├── live_gate.py         # per-channel live-mode command gate
│   ├── live_status.py       # cached stream state (EventSub + Helix hydration)
│   ├── output_gate.py       # cached per-channel "may we send?" state
│   ├── twitch.py            # thin client wrapper
│   ├── llm.py               # LLM provider interface
│   └── llm_openai.py        # OpenAI-compatible adapter (named endpoints)
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
- **Incoming text is sanitized once at the boundary.** `core/bot.py` removes
  every U+E0000 character (Chatterino's anti-deduplication marker) before
  building `ChatMessageEvent` or `CommandContext`, so modules, commands and
  logs only ever see clean text.
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
  while its grant is revoked). The same fallback applies to every outbound
  message, including passive module reactions such as the pyramid watcher's.
  Those messages do **not** get the Chat Bot badge; a single info log per
  channel explains how to fix that.
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
| `BOT_LLM__ENDPOINTS` | JSON object of named endpoints (`base_url`, `api_key`, optional `default_model`) |
| `BOT_LLM__DEFAULT_ENDPOINT` | Endpoint used when a channel has no explicit model (defaults to the first configured) |
| `BOT_LLM__TIMEOUT_SECONDS`, `BOT_LLM__MAX_RESPONSE_CHARS` | Shared LLM adapter settings |
| `BOT_LOGGING__LEVEL` | Root log level (e.g. `INFO`) |
| `BOT_CHAT__MIN_INTERVAL_SECONDS` | Minimum gap between outgoing chat messages (default `1.5`) |
| `BOT_CHAT__QUEUE_MAXSIZE` | Bounded outbound send queue size |
| `BOT_CHAT__MAX_RETRIES`, `BOT_CHAT__BACKOFF_BASE_SECONDS` | 429 retry/backoff settings |
| `BOT_CHAT__BANNED_PHRASES` | JSON array of case-insensitive whole-word phrases to filter |
| `BOT_CHAT__BANNED_PHRASE_REPLACEMENT` | Text sent instead of a message containing a banned phrase |
| `BOT_REPORTS` | Shared report settings (`url_prefix`, `reports_dir`, `api_base`, `timeout_seconds`) |
| `BOT_ENABLED_MODULES` | JSON list of module names to load |
| `BOT_ADMIN_USER_IDS` | JSON list of admin Twitch user IDs |
| `BOT_MODULE__<NAME>` | JSON object with that module's own config |

Example module configuration:

```bash
BOT_MODULE__AI_CHAT='{"default_persona":"generic.md","cooldown_seconds":5}'
BOT_MODULE__CHAT_LOGGER='{"batch_size":100,"flush_interval_seconds":2,"queue_maxsize":2000,"retention_days":30}'
BOT_MODULE__ECOUNT='{"default_days":365,"max_days":3650}'
BOT_MODULE__LINES='{"date_format":"%Y-%m-%d %H:%M:%S"}'
BOT_MODULE__TRIVIA='{"directory":"trivia","timeout_seconds":60,"hint_delay_seconds":10,"similarity_threshold":0.9}'
BOT_MODULE__PYRAMID='{"enabled":true}'
BOT_REPORTS='{"url_prefix":"https://reports.example.com","reports_dir":"reports"}'
BOT_MODULE__UNUSED_EMOTES='{"default_cutoff":"365d","default_threshold":10}'
BOT_MODULE__EMOTE_SETS='{"filename":"emotesets.html","max_sets":10}'
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
stores both the stable Twitch `user_id` and the chatter's `username`, recorded
with the display-name capitalization Twitch supplied (matching
`users.display_name`; the lowercase login is only a fallback). All
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

Every message is also checked against `BOT_CHAT__BANNED_PHRASES` before it is
queued. Matching is **case-insensitive and whole-word only**: `con` matches
`con` or `CON`, but not `console` or `scon`. A match replaces the message with
`BOT_CHAT__BANNED_PHRASE_REPLACEMENT` and logs
`blocklisted phrase detected in: <original message>` to the console.

### Per-channel output control

| Command | Permission | Purpose |
| --- | --- | --- |
| `!lynyxia disable` | bot admin / channel owner | Stop all chat output in this channel |
| `!lynyxia enable` | bot admin / channel owner | Resume chat output in this channel |
| `!lynyxia` | bot admin / channel owner | Show the current state |

The bot stays in the channel and keeps logging messages while output is
disabled — it simply stops sending anything to chat. The check lives at the
send layer, so it covers module replies, `!chat`/mention replies, AI answers and
`TwitchService.send_message()` alike. Only this command's own confirmation is
allowed through, so an admin can never be locked out of re-enabling output.
The state is stored per channel in PostgreSQL (`channel_states`) and survives
restarts; channels without a stored row are enabled by default. Unauthorized
users are ignored silently.

### Live-mode command gating

While a channel's streamer is **offline**, everyone may use the bot normally.
While the streamer is **live**, only users on that channel's live pass list may
run bot commands — this prevents random viewers from flooding chat with
commands during a stream. Messages are logged in both states: the gate sits at
command dispatch in `EventRouter`, after the `"message"` event has already been
sent to the chat logger, so no chat message is ever skipped.

| Command | Permission | Purpose |
| --- | --- | --- |
| `!livepass add <username\|id>` | bot admin / channel owner | Allow a user while the stream is live |
| `!livepass list` | bot admin / channel owner | Show the channel's live pass list |
| `!livepass remove <username\|id>` | bot admin / channel owner | Remove a user from the list |

Names are resolved to stable Twitch user IDs the same way as the AI chat
allowlist. The channel owner and bot admins (`BOT_ADMIN_USER_IDS`) are always
allowed while live, and `!livepass`, `!lynyxia` and `!chatset` are exempt from
the gate so the bot can still be managed. `BOT_LIVE__ENABLED=false` turns the
feature off globally; `BOT_LIVE__EXEMPT_COMMANDS` overrides the exempt list.

Live state is tracked per channel via EventSub `stream.online` /
`stream.offline` subscriptions and cached in memory. Because EventSub only
reports transitions, current state is hydrated from Helix at startup; API
failures fail open (channels are treated as offline), so a Twitch outage never
locks users out. Pass lists are stored in PostgreSQL (`live_passes`) and
survive restarts. Blocked users are ignored silently.

### AI chat settings

The AI chat module is configured through `BOT_MODULE__AI_CHAT` (see
`.env.example`). Personas are plain `*.md` files in `./personas`, mounted
read-only into the container. Like trivia questions, persona files are re-read
on each `!chatset prompt` command and each AI reply, so adding, editing or
removing a file works without a restart. Only simple filenames are accepted —
no subdirectories or path traversal.

| Command | Permission | Purpose |
| --- | --- | --- |
| `!chatset prompt <persona>.md` | bot owner / channel owner | Select the channel's system prompt |
| `!chatset model <endpoint>/<model>` | bot owner / channel owner | Select the channel's endpoint and model |
| `!chatset model <model>` | bot owner / channel owner | Set a model on the default endpoint |
| `!chatset model` | bot owner / channel owner | Show current selection and available endpoints |
| `!chatset allow <username\|id>` | bot owner / channel owner | Add someone to the channel allowlist |
| `!chatset list` | bot owner / channel owner | List the allowlist |
| `!chatset remove <username\|id>` | bot owner / channel owner | Remove someone |
| `!chat [question]` / `!claude` / `!ai` | allowlisted users | Ask the LLM; a leading `@botname` works too. All three names are equivalent aliases. |

The channel owner is detected by comparing the sender's ID with `ctx.channel_id`;
bot owners come from `BOT_ADMIN_USER_IDS`.

Endpoints are named in `BOT_LLM__ENDPOINTS`; the model is no longer in `.env` as
the active setting. Each endpoint may declare a `default_model` fallback, which
is used until `!chatset model` is run for a channel. Endpoint names are
case-insensitive and validated against the configured names; API keys are never
shown in chat or logs.

Persona files may use `{date}`, `{time}`, `{username}` and `{user_id}`
placeholders. They are replaced before the system prompt is sent (`date` is
`YYYY-MM-DD`, `time` is `HH:MM:SS` in UTC). The module also appends an explicit
instruction naming the requester, so `@name` mentions of *other* users in the
question are not mistaken for the sender. A leading `@botname` is stripped
from the question itself.

A mention only activates the bot when it is the **first thing the chatter typed**
(`@botname hello`); a mid-sentence `@botname` is ignored. Twitch's automatically
added `@<thread user>` prefix on replies is skipped, so leading mentions still
work inside threads — on replies the first `@name` token (Twitch's generated
thread prefix) is skipped unless it is the bot itself. Activations from users
not on the allowlist are silently ignored — no reply is sent.

Set `BOT_LOGGING__LEVEL=INFO` to log every incoming chat message as
`INFO bot.core.bot: Chat message: ...`, including its channel, author, message
ID, reply/thread IDs, mention IDs and raw text — useful for diagnosing why a
mention did or did not activate the module.

`!chat`, its aliases `!claude` and `!ai`, and leading mentions start a fresh
context. Replies in the same Twitch
thread reuse stored context from `ai_chat_threads`. The history, tool rounds and
response length are all capped by config, and per-user cooldowns limit abuse.
All replies are threaded and pass through the shared send queue and blocklist.

Tools are enabled with the `tools` config array:

- `web_search` — queries a SearXNG JSON endpoint (`BOT_MODULE__AI_CHAT` →
  `searxng_url`, timeout and max results).
- `chat_messages` — reads recent messages for a mentioned user from
  `chat_messages`, capped by `max_context_messages`.
- `list_chatters` — lists the channel's active chatters with message counts
  (last 30 days by default), most active first.
- `sample_chatter_messages` — reads a few recent or random messages from many
  chatters in one call, so questions like "rank everyone from funniest to least
  funniest" don't need one lookup per person.
- `search_chat` — searches this channel's history for a word or phrase,
  optionally limited to one chatter (e.g. who other people called "cute").

The last three only ever read the **current channel's** history, exclude the
bot's own messages and cap their output, so they cannot pull data from other
channels or flood the model's context. The model judges subjective questions
from that evidence — none of these tools compute a "funniness score". For long
rankings, raise `max_response_tokens` (default 500, so the reply stays short).

Requests, model reasoning (when the endpoint provides it), tool calls/results
and final responses are written to `logs/ai.log` via a rotating logger. Docker
mounts a `logs` volume at `/app/logs`. API keys are never logged.

### Message counter settings

`!lines <username|id>` reports how many messages a user has sent in the current
channel and when their first message was posted, e.g.
`chatter has sent 1,234 messages since 2021-08-11 17:25:54.`

| Key | Default | Purpose |
| --- | --- | --- |
| `date_format` | `%Y-%m-%d %H:%M:%S` | `strftime` format for the "since" date (UTC) |

Bare `!lines` prints `Usage: !lines <username>`. A leading `@` is ignored.
Logins are resolved case-insensitively to a stable Twitch ID, so a renamed
chatter's history stays together; numeric user IDs are also accepted (a missing
ID falls back to a login lookup). The count and earliest timestamp are fetched
in a single `COUNT(*), MIN(sent_at)` query scoped to the current channel. No
schema migration is needed, and replies are threaded and pass through the shared
send queue and blocklist.

### Emote counter settings

`!ecount <emote> [timeframe]` reports how many **messages** in the current
channel contained that emote. For example:

```
!ecount Kappa
!ecount waga 6m
!ecount PogChamp 2w
```

| Key | Default | Purpose |
| --- | --- | --- |
| `default_days` | `365` | Window used when no timeframe is given |
| `max_days` | `3650` | Hard cap on any requested timeframe |

Timeframes use the legacy `<number><y|w|d|m>` syntax (`y` = 365 days,
`m` = 30 days, `w` = 7 days, `d` = 1 day). Unparseable values fall back to
`default_days`, and oversized ones are clamped to `max_days`.

Matching is case-sensitive and uses the legacy whole-word boundary set
`[a-zA-Z0-9_.]`, so `KappaLUL` does not count as `Kappa`. Emote names are
restricted to `[A-Za-z0-9_.]` (up to 64 characters); both the matching regex and
the `!ecount <emote>` exclusion regex are passed to PostgreSQL as bound
parameters. A message containing the emote multiple times counts once, and
`!ecount <emote>` command invocations are excluded so the command cannot inflate
its own result. No schema migration is needed — the count is computed on demand
from `chat_messages`. Replies are threaded and pass through the shared send
queue and blocklist.

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

### Trivia settings

| Key | Default | Purpose |
| --- | --- | --- |
| `directory` | `trivia` | Directory containing the category files (relative to `/app` in Docker) |
| `file_prefix` | `category_` | Filename prefix that marks a trivia file |
| `file_suffix` | `.json` | Filename suffix for trivia files |
| `timeout_seconds` | `60` | Seconds before the round times out and reveals the answer |
| `hint_delay_seconds` | `10` | Seconds to wait before `!hint` reveals the options |
| `similarity_threshold` | `0.9` | Minimum fuzzy-match score for an accepted answer |

Example:

```bash
BOT_MODULE__TRIVIA='{"directory":"trivia","timeout_seconds":60,"hint_delay_seconds":10}'
```

Each file in `./trivia` is one category: `category_Anime-Manga.json` becomes the
`Anime-Manga` category. Files are JSON arrays of objects with `type`
(`multiple` or `boolean`), `question`, `correct_answer` and `incorrect_answers`
(plus optional `category`/`difficulty`). Files are re-read on every `!trivia`
command, so adding a file to the mounted directory works without a restart.

| Command | Permission | Purpose |
| --- | --- | --- |
| `!trivia` | anyone (when commands are allowed) | Start a round from a random category |
| `!trivia <category>` | anyone (when commands are allowed) | Start a round from a named category |
| `!trivia categories` | anyone (when commands are allowed) | List available categories |
| `!hint` | anyone | Show the four options after the delay (not available for True/False) |
| `!pass` | the user who started the round | Skip and reveal the correct answer |

Answers are accepted from any chat message in the same channel (so viewers do
not need a command). Matching is case-insensitive and fuzzy, so minor typos are
accepted. **Every string from the JSON is HTML-unescaped** with `html.unescape()`
before it is shown in chat — `&quot;Dino Run&#039;s Invasion&quot;` appears as
`"Dino Run's Invasion"`, and entities such as `&eacute;` are decoded too. The
same normalization is applied to guesses, so typing `Andr&eacute;s` also works.
Sessions are per channel, time out via async timers, and all output passes
through the shared send queue and the per-channel output gate.

### Pyramid watcher

The passive `pyramid` module watches for emote pyramids — repeated copies of a
single emote forming widths `1, 2, ..., W` and back down to `1`, one line per
message — and posts one reaction when the pyramid finishes:

| Peak width | Reaction |
| --- | --- |
| 3–4 | `That's not a pyramid, surely you can do better!` |
| 5–7 | `Tiny pyramid detected. The Pharaoh is disappointed, <user>.` |
| 8–11 | `A respectable pyramid. Somewhere, an archaeologist just smiled, <user>.` |
| 12–15 | `Now THAT'S a pyramid! The Egyptians would be proud, <user>.` |
| 16–24 | `Now that's a BIG pyramid! The Pharaoh is considering giving you a promotion. 👑` |
| 25+ | `HOLY PYRAMIDS!!! We found the eighth wonder of the world!!!.` |

`<user>` in the templates above is rendered as a clickable `@username` mention.

Rules:

- A line counts only if the **whole message** is whitespace-separated copies
  of the **same** token; matching is case-sensitive (`Kappa` ≠ `kappa`).
- The pyramid must start at width 1 and descend all the way back to 1. The
  reaction fires on the final line; `width` is the peak.
- Other people may contribute in the middle. It only counts as **stolen** when
  the *last* line is from a different user (by Twitch ID) than the *first*:
  `SipTime Well that was rude! <thief> just stole a <width> pyramid from <user>!`
- A deviation **on the way up** resets the candidate silently. Only a
  deviation **after the peak** produces
  `FailFish The pyramid has collapsed thanks to <user>! You have been cursed.`
- A 1-wide line never collapses a pyramid: it either completes the descent or
  quietly starts a new candidate. Pyramids below width 3 get no reaction.
- State is per channel and in memory; there is no time window or width cap —
  a breaking message resets the candidate.
- There are no commands. Reactions go through the shared send queue, so
  blocklist and `!lynyxia` still apply, and they are suppressed while the
  channel is live. `BOT_MODULE__PYRAMID='{"enabled":false}'` disables it
  without editing `BOT_ENABLED_MODULES`.

### Unused emotes report

`!unusedemotes [cutoff] [threshold]` is restricted to **bot admins and the
channel owner**; everyone else is ignored. It scans the requesting channel's
personal 7TV emote set and counts how often each emote appeared in that
channel's logged messages. Arguments use the same syntax as `!ecount`:

| Argument | Default | Meaning |
| --- | --- | --- |
| `cutoff` | `365d` | Look-back window: `<number><y\|w\|d\|m>` (`m` = 30 days, `y` = 365 days) |
| `threshold` | `10` | Emotes used fewer times than this are listed |

Invalid arguments get a usage reply instead of a silent fallback. Counts are
whole-word, case-sensitive, scoped to the current channel, exclude `!ecount
<emote>` command invocations, and use the same `chat_messages` table as the
other modules — no schema change.

The HTML report is written to `reports/<channel-login>/unusedemotes.html` under
the writable `./reports` Docker mount. Each channel gets its own folder, the
file is replaced atomically, and one report per channel can run at a time.
7TV failures are reported in chat as `Error generating report.` and logged;
7TV emote names, image URLs and the channel name are HTML-escaped before they
reach the report.

The public prefix is shared with `!emotesets` in `.env` (no trailing slash
needed):

```bash
BOT_REPORTS='{"url_prefix":"https://reports.example.com"}'
```

The completion message is `Report generated, view at {url}/{channel}/
unusedemotes.html`, with `{url}` replaced by that prefix and `{channel}` by the
requesting channel's configured Twitch login. If no prefix is configured the
command replies `Report URL is not configured.` without generating a file.

### Emote set comparison report

`!emotesets` takes no arguments and is restricted to **bot admins and the
channel owner**. It compares the channel's owned 7TV emote sets (from
`user.emote_sets`) pairwise, listing emotes present in one set but missing from
the other in two side-by-side columns. Emote details (`Animated`, tag count or
`Static`) are included, and every dynamic value in the HTML is escaped.

Sets are sorted by name and capped at `max_sets` (default 10) to keep the
pairwise comparison bounded; a note appears in the report when the list was
truncated. Fewer than two owned sets produces the reply `Fewer than two 7TV
emote sets found.` and no file.

The report is written to `reports/<channel-login>/emotesets.html`, using the
same writable `./reports` mount and shared `BOT_REPORTS` URL prefix as
`!unusedemotes`. The two report types have separate in-flight locks, so they
can run in parallel in the same channel.

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
- `./personas` and `./trivia` are mounted read-only at `/app/personas` and
  `/app/trivia`; `./logs` is bind-mounted at `/app/logs`, so `ai.log` is visible
  on the host. The image's non-root user uses UID/GID `1000` by default
  (override with `BOT_UID`/`BOT_GID` build args if your host user differs).

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
`channel_authorizations` for per-channel broadcaster grants; revision `0005`
adds the AI chat settings, allowlist and thread tables; revision `0006` adds
`channel_states` for the per-channel output toggle; revision `0007` adds
`live_passes` for the per-channel live allowlist; revision `0008` adds per-channel
endpoint/model selection to `ai_chat_channels`.

### Importing legacy SQLite logs

`bot.tools.import_sqlite_logs` generates a PostgreSQL SQL script from an old
`logs.db`; it does **not** write to the database itself. Its source table must
contain `id`, `datetime`, `username` and `message`; `datetime` is
`YYYY-MM-DD HH:MM:SS` in UTC. The SQLite `id` is **ignored** and PostgreSQL
assigns a new one. The file is read in two streaming passes and the SQL is
written incrementally, so memory use stays bounded even for multi-million-row
logs.

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
  The same guard also skips duplicates present in the source itself, so the
  tool does not need to hold a set of every message in memory.

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
BOT_ENABLED_MODULES='["chat_logger","quotes","ecount","lines","ai_chat","greeter"]'
```

`settings.enabled_modules` determines what loads; discovery itself is
automatic. Removed or misspelled names are silently skipped with a log line.

### Available services

Inside a module, `self.context.services` provides:

- `database` — `DatabaseService`; use `async with self.context.services.database.session() as session:` or an injected repository.
- `twitch` — `await services.twitch.send_message(channel_id, text)` (sends to a specific channel by numeric ID).
- `permissions` — `services.permissions.is_admin(ctx.author_id)` for admin commands.
- `llm` — an `LLMProvider` (the OpenAI-compatible adapter) used by the AI chat module.

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

**Milestone 5 — AI chat (done)**

- Persona markdown files, per-channel persona selection and allowlists
- OpenAI-compatible adapter, tool calling, threaded context and `ai.log`
- Web search and chat-message tools, enabled via config

**Milestone 6 — emote counter (done)**

- `!ecount <emote> [timeframe]` with legacy `<number>y|w|d|m` timeframes
- Per-channel, case-sensitive whole-word counts from `chat_messages`, with
  command invocations excluded and bounded by a configurable lookback window

**Milestone 7 — message counter (done)**

- `!lines <username|id>` with stable-ID resolution and configurable date format
- Single-query, per-channel count plus first-message timestamp

**Milestone 8 — per-channel output control (done)**

- `!lynyxia disable|enable` for admins/channel owners, persisted in PostgreSQL
- Send-layer gate that silences all chat output while logging continues

**Milestone 9 — live-mode command gating (done)**

- EventSub stream status with startup Helix hydration, cached per channel
- `!livepass add|list|remove` allowlist; commands gated while the stream is live
- All chat messages are still logged, live or offline

**Milestone 10 — named LLM endpoints (done)**

- Multiple named OpenAI-compatible endpoints configured via `BOT_LLM__ENDPOINTS`
- `!chatset model <endpoint>/<model>` selects endpoint and model per channel

**Milestone 11 — trivia (done)**

- `!trivia`/`!hint`/`!pass` with per-channel sessions and async timeout timers
- JSON category files supplied through the read-only `./trivia` volume
- All question/answer text is HTML-unescaped before appearing in chat

**Milestone 12 — pyramid watcher (done)**

- Pure, unit-tested detector state machine with per-channel candidates
- Tiered completion reactions, theft detection and post-peak collapse curses
- Live-mode suppression and all existing send-safety gates

**Milestone 13 — unused emotes reports (done)**

- Admin/owner-only `!unusedemotes` with strict cutoff and threshold arguments
- Async 7TV lookups, per-channel HTML reports on a writable Docker mount
- HTML escaping, URL-safe channel folders and in-flight deduplication

**Milestone 14 — emote set reports (done)**

- Admin/owner-only `!emotesets` comparing owned 7TV sets pairwise
- Shared `BOT_REPORTS` config and report services used by both commands
- Capped set count, escaped HTML and per-report-type in-flight locks

**Explicitly deferred to later milestones**

- Retention cleanup scheduling (repository method exists)
- Hot-reloading

If you want a different command-dispatch strategy (native TwitchIO commands)
or external/entry-point plugins before those milestones, that change is best
made now, while the module surface is still small.
