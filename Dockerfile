# syntax=docker/dockerfile:1

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install .

FROM python:3.12-slim AS runtime

ARG BOT_UID=1000
ARG BOT_GID=1000

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Match the usual host user so bind-mounted ./logs is writable.
RUN groupadd -g "${BOT_GID}" bot && \
    useradd -u "${BOT_UID}" -g "${BOT_GID}" --home-dir /app --shell /usr/sbin/nologin bot

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app
COPY alembic.ini ./alembic.ini
# Pre-create writable log directory and read-only personas/trivia mount points.
RUN mkdir -p /app/logs /app/personas /app/trivia && chown -R bot:bot /app

USER bot

EXPOSE 8000

CMD ["python", "-m", "bot.main"]
