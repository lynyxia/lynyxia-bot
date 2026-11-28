"""Chat message templates for pyramid outcomes."""

from __future__ import annotations

from bot.modules.pyramid.detector import Outcome

# (maximum width, template); a ``None`` maximum means "everything above".
# Width bands: 3-4, 5-7, 8-11, 12-15, 16-24, 25+.
TIERS: tuple[tuple[int | None, str], ...] = (
    (
        4,
        "{user} has built a {width}-wide {emote} pyramid. "
        "That's not a pyramid, surely you can do better!",
    ),
    (
        7,
        "{user} has built a {width}-wide {emote} pyramid. "
        "Tiny pyramid detected. The Pharaoh is disappointed, {user}.",
    ),
    (
        11,
        "{user} has built a {width}-wide {emote} pyramid. "
        "A respectable pyramid. Somewhere, an archaeologist just smiled, {user}.",
    ),
    (
        15,
        "{user} has built a {width}-wide {emote} pyramid! "
        "Now THAT'S a pyramid! The Egyptians would be proud, {user}.",
    ),
    (
        24,
        "{user} has built a {width}-wide {emote} pyramid!! "
        "Now that's a BIG pyramid! The Pharaoh is considering giving you a promotion. 👑",
    ),
    (
        None,
        "{user} has built a {width}-wide {emote} pyramid!!! "
        "HOLY PYRAMIDS!!! We found the eighth wonder of the world!!!.",
    ),
)

COLLAPSE = (
    "FailFish The pyramid has collapsed thanks to {user}! You have been cursed."
)
STOLEN = "SipTime Well that was rude! {thief} just stole a {width} pyramid from {user}!"


def mention(name: str) -> str:
    """Turn a display name into a clickable Twitch @mention."""
    if not name or name.startswith("@"):
        return name
    return f"@{name}"


def reaction_text(outcome: Outcome) -> str:
    """Render the single chat message for a completion or collapse."""
    if outcome.kind == "collapse":
        return COLLAPSE.format(user=mention(outcome.breaker))
    if outcome.stolen:
        return STOLEN.format(
            thief=mention(outcome.finisher),
            width=outcome.width,
            user=mention(outcome.starter),
        )
    template = next(
        text for maximum, text in TIERS if maximum is None or outcome.width <= maximum
    )
    return template.format(
        user=mention(outcome.starter), width=outcome.width, emote=outcome.emote
    )
