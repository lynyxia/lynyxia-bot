# Docker support files

Database readiness is handled in two layers:

1. `db` exposes a `pg_isready` healthcheck, and Compose starts `bot` only after
   it becomes healthy (`depends_on: condition: service_healthy`).
2. `DatabaseService.connect()` additionally retries the first connection with
   backoff, so a brief race after the healthcheck cannot crash startup.

No shell "wait for DB" script is needed. This directory is reserved for future
operational helpers (backup/restore scripts, etc.).
