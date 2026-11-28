"""Watch chat for emote pyramids and react when they complete or collapse.

This is a passive module: it has no commands. It registers a ``message``
handler, keeps one detector per channel in memory, and sends reactions through
the shared Twitch service so the outbound queue, blocklist, ``!lynyxia`` output
gate, live-mode suppression and app-token/user-token fallback all apply.
"""

from __future__ import annotations

from bot.core.event_router import ChatMessageEvent
from bot.modules.base import Module, ModuleContext
from bot.modules.pyramid.config import PyramidConfig
from bot.modules.pyramid.detector import PyramidDetector, parse_chat_line
from bot.modules.pyramid.messages import reaction_text


class PyramidModule(Module):
    name = "pyramid"

    def __init__(self, context: ModuleContext) -> None:
        super().__init__(context)
        self.config = PyramidConfig.model_validate(context.config)
        self._detectors: dict[str, PyramidDetector] = {}

    async def setup(self) -> None:
        if self.config.enabled:
            self.subscribe(self.context.router.on_event("message", self.on_message))

    async def on_message(self, event: ChatMessageEvent) -> None:
        detector = self._detectors.setdefault(event.channel_id, PyramidDetector())
        outcome = detector.feed(
            parse_chat_line(
                event.content,
                event.author_name or event.author_login,
                event.author_id,
            )
        )
        if outcome.kind == "none":
            return

        status = self.context.services.live_status
        if status is not None and status.is_live(event.channel_id):
            # Pyramids are fun, but they are not bot commands: stay quiet while
            # the streamer is live so chat stays focused on the stream.
            self.log.info(
                "Pyramid reaction suppressed in live channel %s", event.channel_id
            )
            return

        twitch = self.context.services.twitch
        if twitch is None:
            return
        await twitch.send_message(event.channel_id, reaction_text(outcome))
