"""Shared auth fallback for outbound chat sends.

Command replies (``BotClient``) and passive module messages
(``TwitchService``, used by the pyramid watcher) must behave identically: send
with the app token first, then retry with the bot user token when Twitch
answers ``401`` because the channel has not granted ``channel:bot``.

All other errors propagate to the caller so the shared queue can apply its
429 backoff, or the caller can log them as before.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

# One delivery attempt. ``token_for=None`` means the app access token;
# ``token_for=bot_id`` means the bot account's user token.
TokenDelivery = Callable[[str | None], Awaitable[None]]


async def send_with_token_fallback(
    delivery: TokenDelivery,
    *,
    bot_id: str,
    has_user_token: Callable[[], bool],
    on_no_token: Callable[[], None] | None = None,
    on_fallback: Callable[[], None] | None = None,
    on_fallback_error: Callable[[Exception], None] | None = None,
) -> None:
    """Try the app token, then the bot user token on HTTP 401.

    Non-401 errors are re-raised so the chat queue can retry rate limits. A
    failed fallback is reported through ``on_fallback_error`` and swallowed:
    there is no third credential to try.
    """
    try:
        await delivery(None)
        return
    except Exception as exc:
        if getattr(exc, "status", None) != 401:
            raise

    if not has_user_token():
        if on_no_token is not None:
            on_no_token()
        return

    try:
        await delivery(bot_id)
    except Exception as exc:
        if on_fallback_error is not None:
            on_fallback_error(exc)
        return

    if on_fallback is not None:
        on_fallback()
