from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from exceptions import ConfigError, LLMClientError, PlatformError

if TYPE_CHECKING:
    from ai_client import AIClient

logger = logging.getLogger(__name__)

# Conversation history keyed by Telegram user_id.
# Index 0 is always the system prompt — never evicted, never shared with Discord.
_history: dict[int, list[dict]] = {}

_SYSTEM_PROMPT: str | None = None
_MAX_HISTORY: int = 10
_startup_time: datetime = datetime.now(tz=timezone.utc)


def _get_system_prompt() -> str:
    """Return cached system prompt from module state.

    Returns:
        System prompt string.

    Raises:
        ConfigError: If setup_application() was never called.
    """
    if _SYSTEM_PROMPT is None:
        raise ConfigError("telegram_handler belum diinisialisasi — panggil setup_application() dulu")
    return _SYSTEM_PROMPT


def _ensure_history(user_id: int) -> None:
    """Initialise history for a user if not yet present.

    Always inserts the system prompt at index 0.

    Args:
        user_id: Telegram user ID.
    """
    if user_id not in _history:
        _history[user_id] = [{"role": "system", "content": _get_system_prompt()}]


def _append_and_evict(user_id: int, role: str, content: str) -> None:
    """Append a message and evict oldest non-system message if over limit.

    Args:
        user_id: Telegram user ID.
        role: 'user' or 'assistant'.
        content: Message text.
    """
    _history[user_id].append({"role": role, "content": content})
    # Index 0 is system prompt — count only indices 1+
    while len(_history[user_id]) - 1 > _MAX_HISTORY:
        _history[user_id].pop(1)


async def _cmd_tanya(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /tanya <pertanyaan> — forward ke LLM dan balas.

    Args:
        update: Incoming Telegram update.
        context: PTB context carrying bot_data with ai_client.
    """
    if not update.message or not update.effective_user:
        return

    if not context.args:
        await update.message.reply_text(
            "Kasih pertanyaannya dong! Contoh: `/tanya apa itu AI?`",
            parse_mode="Markdown",
        )
        return

    pertanyaan = " ".join(context.args)
    user_id = update.effective_user.id

    ai_client: AIClient = context.bot_data["ai_client"]
    _ensure_history(user_id)
    _append_and_evict(user_id, "user", pertanyaan)

    await update.message.chat.send_action("typing")

    try:
        jawaban = await ai_client.chat(_history[user_id])
    except LLMClientError as exc:
        logger.error("[TELEGRAM] LLM error untuk user %d: %s", user_id, exc)
        _history[user_id].pop()
        await update.message.reply_text("Waduh, gagal dapat respons dari AI. Coba lagi nanti.")
        return

    _append_and_evict(user_id, "assistant", jawaban)
    logger.info("[TELEGRAM] Menjawab user %d (%d chars)", user_id, len(jawaban))

    # Chunk jika melebihi batas 4096 char Telegram
    if len(jawaban) > 4096:
        for i in range(0, len(jawaban), 4096):
            await update.message.reply_text(jawaban[i : i + 4096])
    else:
        await update.message.reply_text(jawaban)


async def _cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /reset — hapus conversation history user yang memanggil.

    Args:
        update: Incoming Telegram update.
        context: PTB context (unused).
    """
    if not update.message or not update.effective_user:
        return

    user_id = update.effective_user.id
    _history.pop(user_id, None)
    logger.info("[TELEGRAM] History user %d direset", user_id)
    await update.message.reply_text("History percakapan kamu sudah direset.")


async def _cmd_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /info — tampilkan model aktif dan uptime.

    Args:
        update: Incoming Telegram update.
        context: PTB context carrying bot_data with ai_client.
    """
    if not update.message:
        return

    ai_client: AIClient = context.bot_data["ai_client"]
    uptime = datetime.now(tz=timezone.utc) - _startup_time
    hours, remainder = divmod(int(uptime.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)

    teks = (
        f"🤖 *AI Bot Info*\n"
        f"Model: `{ai_client.model}`\n"
        f"Uptime: `{hours:02d}:{minutes:02d}:{seconds:02d}`"
    )
    await update.message.reply_text(teks, parse_mode="Markdown")


def setup_application(
    telegram_token: str,
    ai_client: AIClient,
    system_prompt: str,
    max_history: int,
) -> Application:
    """Create and configure the PTB Application instance.

    The Application is returned but NOT started — the caller manages lifecycle
    via async context manager pattern for shared event loop compatibility.

    Args:
        telegram_token: Bot token from BotFather.
        ai_client: Shared LLM client instance.
        system_prompt: Text injected as system message for every user.
        max_history: Maximum non-system messages stored per user.

    Returns:
        Configured telegram.ext.Application instance.

    Raises:
        ConfigError: If arguments are invalid.
        PlatformError: If the token is rejected by Telegram.
    """
    global _SYSTEM_PROMPT, _MAX_HISTORY, _startup_time  # noqa: PLW0603

    if not telegram_token:
        raise ConfigError("TELEGRAM_TOKEN tidak boleh kosong")
    if not system_prompt:
        raise ConfigError("SYSTEM_PROMPT tidak boleh kosong")
    if max_history < 1:
        raise ConfigError(f"MAX_HISTORY harus >= 1, dapat: {max_history}")

    _SYSTEM_PROMPT = system_prompt
    _MAX_HISTORY = max_history
    _startup_time = datetime.now(tz=timezone.utc)

    try:
        application = Application.builder().token(telegram_token).build()
    except Exception as exc:
        raise PlatformError(f"Gagal inisialisasi Telegram Application: {exc}", platform="telegram") from exc

    application.bot_data["ai_client"] = ai_client

    application.add_handler(CommandHandler("tanya", _cmd_tanya))
    application.add_handler(CommandHandler("reset", _cmd_reset))
    application.add_handler(CommandHandler("info", _cmd_info))

    logger.info("[TELEGRAM] Application dikonfigurasi")
    return application
