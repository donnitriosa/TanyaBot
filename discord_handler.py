from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from exceptions import ConfigError, LLMClientError, PlatformError

if TYPE_CHECKING:
    from ai_client import AIClient
    from rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Conversation history keyed by Discord user_id.
# Index 0 is always the system prompt — never evicted.
_history: dict[int, list[dict]] = {}

_SYSTEM_PROMPT: str | None = None
_MAX_HISTORY: int = 10
_rate_limiter: RateLimiter | None = None
_startup_time: datetime = datetime.now(tz=timezone.utc)


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
    rate_limiter: RateLimiter,
) -> commands.Bot:
    """Create and configure the Discord bot instance with hybrid commands.

    Registers prefix + slash commands. The bot is returned but NOT started.

    Args:
        system_prompt: Text injected as the system message for every user.
        max_history: Maximum number of non-system messages stored per user.
        rate_limiter: RateLimiter instance shared across platforms.

    Returns:
        Configured discord.ext.commands.Bot instance.

    Raises:
        ConfigError: If arguments are invalid.
    """
    global _SYSTEM_PROMPT, _MAX_HISTORY, _rate_limiter, _startup_time  # noqa: PLW0603

    if not system_prompt:
        raise ConfigError("SYSTEM_PROMPT tidak boleh kosong")
    if max_history < 1:
        raise ConfigError(f"MAX_HISTORY harus >= 1, dapat: {max_history}")

    _SYSTEM_PROMPT = system_prompt
    _MAX_HISTORY = max_history
    _rate_limiter = rate_limiter
    _startup_time = datetime.now(tz=timezone.utc)

    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents)

    # AIClient injected at runtime via bot.ai_client attribute
    bot.ai_client = None  # type: ignore[attr-defined]

    @bot.event
    async def on_ready() -> None:
        logger.info("[DISCORD] Syncing slash commands...")
        try:
            await bot.tree.sync()
            logger.info("[DISCORD] Slash commands synced successfully")
        except Exception as exc:
            logger.error("[DISCORD] Gagal sync slash commands: %s", exc)
        logger.info("[DISCORD] Bot online sebagai %s", bot.user)

    @bot.hybrid_command(name="tanya", description="Tanya sesuatu ke AI")
    async def tanya_ai(ctx: commands.Context, *, pertanyaan: str) -> None:
        """Forward pertanyaan ke LLM dan balas di channel dengan formatting embed.

        Args:
            ctx: Discord command context.
            pertanyaan: Question text from the user.
        """
        ai_client: AIClient = bot.ai_client  # type: ignore[attr-defined]
        if ai_client is None:
            raise PlatformError("AIClient belum di-inject ke discord bot", platform="discord")
        if _rate_limiter is None:
            raise PlatformError("RateLimiter belum dikonfigurasi di discord bot", platform="discord")

        user_id = ctx.author.id

        allowed, retry_after = _rate_limiter.is_allowed("discord", user_id)
        if not allowed:
            await ctx.send(
                f"Sabar ya, kamu sudah terlalu banyak bertanya. Coba lagi dalam {retry_after} detik.",
                ephemeral=True,
            )
            return

        _ensure_history(user_id)
        _append_and_evict(user_id, "user", pertanyaan)

        # Defer response for app command / slash commands to prevent timeout (max 3s)
        if ctx.interaction:
            await ctx.interaction.response.defer()

        try:
            # Show typing indicator for prefix commands
            if not ctx.interaction:
                async with ctx.typing():
                    jawaban = await ai_client.chat(_history[user_id])
            else:
                jawaban = await ai_client.chat(_history[user_id])

        except LLMClientError as exc:
            logger.error("[DISCORD] LLM error untuk user %d: %s", user_id, exc)
            # Roll back the user message that failed
            _history[user_id].pop()
            err_msg = "Waduh, gagal dapat respons dari AI. Coba lagi nanti."
            if ctx.interaction:
                await ctx.interaction.followup.send(err_msg, ephemeral=True)
            else:
                await ctx.send(err_msg)
            return

        _append_and_evict(user_id, "assistant", jawaban)
        logger.info("[DISCORD] Menjawab user %d (%d chars)", user_id, len(jawaban))

        # Check embed limit (Discord limits embed description to 4096 characters)
        if len(jawaban) > 4096:
            # Fallback to plain text split if body > 4096
            if ctx.interaction:
                # Send first chunk in deferred response
                await ctx.interaction.followup.send(jawaban[:2000])
                for i in range(2000, len(jawaban), 2000):
                    await ctx.channel.send(jawaban[i : i + 2000])
            else:
                for i in range(0, len(jawaban), 2000):
                    await ctx.send(jawaban[i : i + 2000])
        else:
            embed = discord.Embed(
                description=jawaban,
                color=discord.Color.blue(),
            )
            embed.set_footer(text="by. TanyaBot")
            if ctx.interaction:
                await ctx.interaction.followup.send(embed=embed)
            else:
                await ctx.send(embed=embed)

    @bot.hybrid_command(name="reset", description="Reset history percakapan kamu")
    async def reset_history(ctx: commands.Context) -> None:
        """Hapus conversation history user yang memanggil.

        Args:
            ctx: Discord command context.
        """
        user_id = ctx.author.id
        _history.pop(user_id, None)
        logger.info("[DISCORD] History user %d direset", user_id)
        msg = "History percakapan kamu sudah direset."
        if ctx.interaction:
            await ctx.interaction.response.send_message(msg, ephemeral=True)
        else:
            await ctx.send(msg)

    @bot.hybrid_command(name="info", description="Tampilkan info performa bot saat ini")
    async def info_bot(ctx: commands.Context) -> None:
        """Tampilkan model aktif, uptime, dan total request diproses."""
        ai_client: AIClient = bot.ai_client  # type: ignore[attr-defined]
        if ai_client is None:
            raise PlatformError("AIClient belum di-inject ke discord bot", platform="discord")

        uptime = datetime.now(tz=timezone.utc) - _startup_time
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)

        uptime_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        processed = ai_client.processed_messages

        embed = discord.Embed(
            title="🤖 AI Bot Info",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Model", value=f"`{ai_client.model}`", inline=True)
        embed.add_field(name="Uptime", value=f"`{uptime_str}`", inline=True)
        embed.add_field(name="Pesan Diproses", value=f"`{processed}`", inline=True)

        if ctx.interaction:
            await ctx.interaction.response.send_message(embed=embed)
        else:
            await ctx.send(embed=embed)

    @bot.event
    async def on_command_error(
        ctx: commands.Context,
        error: commands.CommandError,
    ) -> None:
        # Check command context to avoid crash on hybrid commands errors
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("Kasih pertanyaannya dong! Contoh: `!tanya apa itu AI?`", ephemeral=True)
        elif isinstance(error, commands.CommandNotFound):
            pass  # Abaikan command tidak dikenal
        else:
            logger.error("[DISCORD] Command error: %s", error)
            await ctx.send("Ada error teknis. Coba lagi nanti.", ephemeral=True)

    return bot
