"""AI chat module: personas, allowlists and LLM-powered replies."""

from __future__ import annotations

import asyncio
import json
import re
import time

from bot.core.event_router import CommandContext
from bot.database.repositories.ai_chat import (
    AiChatAllowlistRepository,
    AiChatChannelRepository,
    AiChatThreadRepository,
)
from bot.database.repositories.chat_message import ChatMessageRepository
from bot.database.repositories.user import UserRepository
from bot.modules.ai_chat.ai_logger import AILogger
from bot.modules.ai_chat.config import AIChatConfig
from bot.modules.ai_chat.personas import (
    format_system_prompt,
    is_valid_persona_name,
    load_personas,
)
from bot.modules.ai_chat.tools import (
    ChannelHistoryTool,
    ChatMessagesTool,
    ChatterSamplesTool,
    ListChattersTool,
    SearchChatTool,
    Tool,
    WebSearchTool,
)
from bot.modules.base import Module, ModuleContext
from bot.services.llm import LLMMessage, LLMProvider, ToolDefinition

USAGE = (
    "Usage: !chatset prompt <persona>.md | model <endpoint/model> | "
    "allow|remove <username|id> | list"
)
# All names that start an AI conversation. Kept in one place so registration
# and prompt stripping cannot drift apart.
CHAT_COMMANDS = ("chat", "claude", "ai")
MODEL_PATTERN = re.compile(r"[A-Za-z0-9._:/-]{1,128}")


class AIChatModule(Module):
    name = "ai_chat"

    def __init__(self, context: ModuleContext) -> None:
        super().__init__(context)
        self.config = AIChatConfig.model_validate(context.config)
        self._personas: dict[str, str] = {}
        self._channels: AiChatChannelRepository | None = None
        self._allowlist: AiChatAllowlistRepository | None = None
        self._threads: AiChatThreadRepository | None = None
        self._users: UserRepository | None = None
        self._messages: ChatMessageRepository | None = None
        self._llm: LLMProvider | None = context.services.llm
        self._ai = AILogger(self.config.log_path)
        self._tools: list[Tool] = []
        self._tool_map: dict[str, Tool] = {}
        self._cooldowns: dict[tuple[str, str], float] = {}

    async def setup(self) -> None:
        database = self.context.services.database
        self._channels = AiChatChannelRepository(database)
        self._allowlist = AiChatAllowlistRepository(database)
        self._threads = AiChatThreadRepository(database)
        self._users = UserRepository(database)
        self._messages = ChatMessageRepository(database)

        await self._reload_personas()
        self.log.info(
            "Loaded %s persona(s) from %s", len(self._personas), self.config.personas_dir
        )

        if "web_search" in self.config.tools:
            tool = WebSearchTool(
                self.config.searxng_url,
                timeout=self.config.searxng_timeout_seconds,
                max_results=self.config.searxng_max_results,
            )
            self._register_tool(tool)
        if "chat_messages" in self.config.tools:
            self._register_tool(
                ChatMessagesTool(
                    self._users,
                    self._messages,
                    max_messages=self.config.max_context_messages,
                )
            )
        bot_id = self.context.settings.twitch.bot_id
        if "list_chatters" in self.config.tools:
            self._register_tool(
                ListChattersTool(self._messages, bot_id=bot_id)
            )
        if "sample_chatter_messages" in self.config.tools:
            self._register_tool(
                ChatterSamplesTool(self._users, self._messages, bot_id=bot_id)
            )
        if "search_chat" in self.config.tools:
            self._register_tool(
                SearchChatTool(self._users, self._messages, bot_id=bot_id)
            )
        if "channel_history" in self.config.tools:
            self._register_tool(
                ChannelHistoryTool(self._users, self._messages, bot_id=bot_id)
            )

        self.subscribe(self.context.router.on_command("chatset", self.on_chatset))
        for command in CHAT_COMMANDS:
            self.subscribe(self.context.router.on_command(command, self.on_chat))

    async def _reload_personas(self) -> dict[str, str]:
        """Re-read persona files so edits and new files need no bot restart.

        Mirrors the trivia module: file I/O is offloaded to a worker thread and
        the fresh result replaces the cached mapping on every call.
        """
        self._personas = await asyncio.to_thread(load_personas, self.config.personas_dir)
        return self._personas

    def _register_tool(self, tool: Tool) -> None:
        self._tools.append(tool)
        self._tool_map[tool.name] = tool
        self.log.info("Enabled AI tool: %s", tool.name)

    async def start(self) -> None:
        for tool in self._tools:
            starter = getattr(tool, "start", None)
            if starter is not None:
                await starter()

    async def stop(self) -> None:
        for tool in self._tools:
            closer = getattr(tool, "stop", None)
            if closer is not None:
                await closer()
        self._ai.close()
        await super().stop()

    # ------------------------------------------------------------------
    # Settings commands
    # ------------------------------------------------------------------
    async def on_chatset(self, ctx: CommandContext) -> None:
        if not self.context.services.permissions.can_manage_channel(ctx):
            await ctx.reply("Only the bot owner or channel owner can do that.")
            return
        if not ctx.args:
            await ctx.reply(USAGE)
            return

        action = ctx.args[0].lower()
        if action == "prompt":
            await self._set_prompt(ctx, ctx.args[1:])
        elif action == "model":
            await self._set_model(ctx, ctx.args[1:])
        elif action == "allow":
            await self._change_allowlist(ctx, ctx.args[1:], add=True)
        elif action == "remove":
            await self._change_allowlist(ctx, ctx.args[1:], add=False)
        elif action == "list":
            await self._list_allowlist(ctx)
        else:
            await ctx.reply(USAGE)

    async def _set_prompt(self, ctx: CommandContext, args: list[str]) -> None:
        # Reload on each command: editing a file or adding a new one in the
        # mounted directory is enough, no bot restart needed.
        await self._reload_personas()
        if (
            len(args) != 1
            or not is_valid_persona_name(args[0])
            or args[0] not in self._personas
        ):
            names = ", ".join(sorted(self._personas)) or "none"
            await ctx.reply(f"Unknown persona. Available: {names}")
            return
        assert self._channels is not None
        await self._channels.set_persona(ctx.channel_id, args[0])
        await ctx.reply(f"Persona set to {args[0]} for this channel.")

    async def _current_model(self, channel_id: str) -> tuple[str | None, str | None]:
        """Return the channel's endpoint/model, falling back to env defaults."""
        assert self._channels is not None
        settings = self.context.settings.llm
        record = await self._channels.get(channel_id)
        if (
            record is not None
            and record.endpoint in settings.endpoints
            and record.model
        ):
            return record.endpoint, record.model
        endpoint = settings.default_endpoint
        if endpoint in settings.endpoints:
            return endpoint, settings.endpoints[endpoint].default_model
        return None, None

    async def _set_model(self, ctx: CommandContext, args: list[str]) -> None:
        settings = self.context.settings.llm
        endpoints = settings.endpoints
        assert self._channels is not None

        if not args:
            endpoint, model = await self._current_model(ctx.channel_id)
            current = f"{endpoint}/{model}" if endpoint and model else "not configured"
            available = ", ".join(sorted(endpoints)) or "none"
            await ctx.reply(f"Model: {current}. Available endpoints: {available}")
            return

        if len(args) != 1:
            await ctx.reply(USAGE)
            return

        value = args[0].strip()
        if "/" in value:
            endpoint, model = (part.strip() for part in value.split("/", 1))
            endpoint = endpoint.lower()
            if endpoint not in endpoints:
                available = ", ".join(sorted(endpoints)) or "none"
                await ctx.reply(f"Unknown endpoint. Available: {available}")
                return
        else:
            endpoint = settings.default_endpoint or ""
            model = value
            if not endpoint:
                await ctx.reply("No LLM endpoints are configured.")
                return

        if not MODEL_PATTERN.fullmatch(model):
            await ctx.reply("Invalid model name.")
            return

        await self._channels.set_model(ctx.channel_id, endpoint, model)
        await ctx.reply(f"Model set to {endpoint}/{model} for this channel.")

    async def _resolve_user(self, name: str) -> tuple[str, str] | None:
        target = name.strip().lstrip("@")
        if not target:
            return None
        if target.isdigit():
            return target, target
        assert self._users is not None
        user = await self._users.get_by_login(target)
        if user is None:
            return None
        return user.id, user.login

    async def _change_allowlist(
        self, ctx: CommandContext, args: list[str], *, add: bool
    ) -> None:
        if len(args) != 1:
            await ctx.reply(USAGE)
            return
        resolved = await self._resolve_user(args[0])
        if resolved is None:
            await ctx.reply("User not found.")
            return
        user_id, login = resolved
        assert self._allowlist is not None
        if add:
            await self._allowlist.add(ctx.channel_id, user_id, login)
            await ctx.reply(f"Added @{login} to the chat allowlist.")
        else:
            removed = await self._allowlist.remove(ctx.channel_id, user_id)
            await ctx.reply(
                f"Removed @{login} from the chat allowlist."
                if removed
                else f"@{login} was not on the chat allowlist."
            )

    async def _list_allowlist(self, ctx: CommandContext) -> None:
        assert self._allowlist is not None
        entries = await self._allowlist.list(ctx.channel_id)
        if not entries:
            await ctx.reply("The chat allowlist is empty.")
            return
        names = ", ".join(f"@{entry.login or entry.user_id}" for entry in entries)
        await ctx.reply(f"Chat allowlist: {names}")

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------
    async def on_chat(self, ctx: CommandContext) -> None:
        assert self._allowlist is not None and self._threads is not None
        if not await self._allowlist.contains(ctx.channel_id, ctx.author_id):
            # Silently ignore non-allowlisted users; don't advertise the feature.
            return
        endpoint, model = await self._current_model(ctx.channel_id)
        if self._llm is None or not endpoint or not model:
            await ctx.reply(
                "AI chat is not configured. An admin can set it with "
                "!chatset model <endpoint/model>."
            )
            return

        cooldown_key = (ctx.channel_id, ctx.author_id)
        now = time.monotonic()
        if now - self._cooldowns.get(cooldown_key, 0) < self.config.cooldown_seconds:
            return
        self._cooldowns[cooldown_key] = now

        # A persona may have been edited since the last reply; re-read it so
        # changes take effect immediately, like trivia questions do.
        await self._reload_personas()
        persona_name, persona_template = await self._current_persona(ctx.channel_id)
        requester = ctx.author_login or ctx.author_name or ctx.author_id
        system_prompt = (
            format_system_prompt(
                persona_template, username=requester, user_id=ctx.author_id
            )
            + "\n\nThe person sending this message is "
            + f"@{requester} (Twitch ID {ctx.author_id}). "
            "Any other @mentions in their message refer to other users, not them."
        )

        thread_id = ctx.thread_id or ctx.content
        record = await self._threads.get(thread_id)
        history = [
            LLMMessage(role=entry["role"], content=entry["content"])
            for entry in (record.messages if record else [])
        ]
        prompt_text = ctx.content
        # Strip whichever alias was typed. Using the dispatched command name
        # avoids hardcoding a prefix length (which differs per alias) and
        # leaves mention activations, which have no leading "!", untouched.
        command = ctx.command.lower()
        if command in CHAT_COMMANDS:
            prefix = f"!{command}"
            if prompt_text.lower().startswith(prefix) and (
                len(prompt_text) == len(prefix) or prompt_text[len(prefix)].isspace()
            ):
                prompt_text = prompt_text[len(prefix) :].strip()
        bot_login = self.context.settings.twitch.bot_username
        prompt_text = re.sub(
            rf"^@{re.escape(bot_login)}\b",
            "",
            prompt_text,
            flags=re.IGNORECASE,
        ).strip()
        prompt_text = prompt_text or "Hello!"

        messages = [
            LLMMessage(role="system", content=system_prompt),
            *history,
            LLMMessage(role="user", content=prompt_text),
        ]
        tool_definitions = [
            ToolDefinition(
                name=tool.name, description=tool.description, parameters=tool.parameters
            )
            for tool in self._tools
        ]

        final: str | None = None
        for _round in range(self.config.max_tool_rounds):
            self._ai.request(messages)
            try:
                response = await self._llm.complete_with_tools(
                    messages,
                    endpoint=endpoint,
                    model=model,
                    tools=tool_definitions,
                    max_tokens=self.config.max_response_tokens,
                )
            except Exception as exc:
                self._ai.error(f"LLM request failed: {type(exc).__name__}")
                await ctx.reply("Sorry, the AI service is unavailable right now.")
                return

            self._ai.reasoning(response.reasoning)
            if not response.tool_calls:
                final = response.content
                break

            messages.append(
                LLMMessage(
                    role="assistant",
                    content=response.content,
                    tool_calls=[
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.arguments),
                            },
                        }
                        for call in response.tool_calls
                    ],
                )
            )
            for call in response.tool_calls:
                tool = self._tool_map.get(call.name)
                result = (
                    await tool.execute(call.arguments, ctx)
                    if tool is not None
                    else "Unknown tool."
                )
                self._ai.tool(call.name, call.arguments, result)
                messages.append(
                    LLMMessage(
                        role="tool", tool_call_id=call.id, content=result
                    )
                )

        final = (final or "I could not generate a response.").strip()
        self._ai.response(final)

        previous = list(record.messages) if record else []
        updated = previous + [
            {"role": "user", "content": prompt_text},
            {"role": "assistant", "content": final},
        ]
        await self._threads.save(
            thread_id,
            ctx.channel_id,
            ctx.author_id,
            persona_name,
            updated[-self.config.max_thread_messages :],
        )
        await ctx.reply_threaded(final)

    async def _current_persona(self, channel_id: str) -> tuple[str, str]:
        assert self._channels is not None
        record = await self._channels.get(channel_id)
        name = (
            record.persona
            if record and record.persona in self._personas
            else self.config.default_persona
        )
        return name, self._personas.get(name, "You are a helpful Twitch chat assistant.")
