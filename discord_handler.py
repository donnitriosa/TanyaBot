from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from exceptions import ConfigError, LLMClientError, PlatformError

if TYPE_CHECKING:
    from ai_client import AIClient

logger = logging.getLogger(__name__)

# Conversation history keyed by Discord user_id.
# Index 0 is always the system prompt — never evicted.
_history: dict[int, list[dict]] = {}

_SYSTEM_PROMPT: str | None = None
_MAX_HISTORY: int = 10


def _get_system_prompt() -> str:
    """Return cached system prompt from module state.

    Returns:
        System prompt string.

    Raises:
        ConfigError: If setup_handler() was never called.
    """
    if _SYSTEM_PROMPT is None:
        raise ConfigError("discord_handler belum diinisialisasi — panggil setup_handler() dulu")
    return _SYSTEM_PROMPT


def _ensure_history(user_id: int) -> None:
    """Initialise history for a user if not yet present.

    Always inserts the system prompt at index 0.

    Args:
        user_id: Discord user snowflake ID.
    """
    if user_id not in _history:
        _history[user_id] = [{"role": "system", "content": _get_system_prompt()}]


def _append_and_evict(user_id: int, role: str, content: str) -> None:
    """Append a message and evict oldest non-system message if over limit.

    Args:
        user_id: Discord user snowflake ID.
        role: 'user' or 'assistant'.
        content: Message text.
    """
    _history[user_id].append({"role": role, "content": content})
    # Index 0 is system prompt — count only indices 1+
    while len(_history[user_id]) - 1 > _MAX_HISTORY:
        _history[user_id].pop(1)


def setup_handler(
    system_prompt: str,
    max_history: int,
) -> commands.Bot:
    """Create and configure the Discord bot instance.

    Registers all commands and events. The bot is returned but NOT started —
    the caller is responsible for starting it via bot.start().

    Args:
        system_prompt: Text injected as the system message for every user.
        max_history: Maximum number of non-system messages stored per user.

    Returns:
        Configured discord.ext.commands.Bot instance.

    Raises:
        ConfigError: If arguments are invalid.
    """
    global _SYSTEM_PROMPT, _MAX_HISTORY  # noqa: PLW0603

    if not system_prompt:
        raise ConfigError("SYSTEM_PROMPT tidak boleh kosong")
    if max_history < 1:
        raise ConfigError(f"MAX_HISTORY harus >= 1, dapat: {max_history}")

    _SYSTEM_PROMPT = system_prompt
    _MAX_HISTORY = max_history

    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents)

    # AIClient injected at runtime via bot.ai_client attribute
    bot.ai_client = None  # type: ignore[attr-defined]

    @bot.event
    async def on_ready() -> None:
        logger.info("[DISCORD] Bot online sebagai %s", bot.user)

    @bot.command(name="tanya")
    async def tanya_ai(ctx: commands.Context, *, pertanyaan: str) -> None:
        """Forward pertanyaan ke LLM dan balas di channel.

        Args:
            ctx: Discord command context.
            pertanyaan: Question text from the user.
        """
        ai_client: AIClient = bot.ai_client  # type: ignore[attr-defined]
        if ai_client is None:
            raise PlatformError("AIClient belum di-inject ke discord bot", platform="discord")

        user_id = ctx.author.id
        _ensure_history(user_id)
        _append_and_evict(user_id, "user", pertanyaan)

        async with ctx.typing():
            try:
                jawaban = await ai_client.chat(_history[user_id])
            except LLMClientError as exc:
                logger.error("[DISCORD] LLM error untuk user %d: %s", user_id, exc)
                # Roll back the user message that failed
                _history[user_id].pop()
                await ctx.send("Waduh, gagal dapat respons dari AI. Coba lagi nanti.")
                return

        _append_and_evict(user_id, "assistant", jawaban)
        logger.info("[DISCORD] Menjawab user %d (%d chars)", user_id, len(jawaban))

        if len(jawaban) > 2000:
            for i in range(0, len(jawaban), 2000):
                await ctx.send(jawaban[i : i + 2000])
        else:
            await ctx.send(jawaban)

    @bot.command(name="reset")
    async def reset_history(ctx: commands.Context) -> None:
        """Hapus conversation history user yang memanggil.

        Args:
            ctx: Discord command context.
        """
        user_id = ctx.author.id
        _history.pop(user_id, None)
        logger.info("[DISCORD] History user %d direset", user_id)
        await ctx.send("History percakapan kamu sudah direset.")

    @bot.event
    async def on_command_error(
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("Kasih pertanyaannya dong! Contoh: `!tanya apa itu AI?`")
        elif isinstance(error, commands.CommandNotFound):
            pass  # Abaikan command tidak dikenal
        else:
            logger.error("[DISCORD] Command error: %s", error)
            await ctx.send("Ada error teknis. Coba lagi nanti.")

    return bot
