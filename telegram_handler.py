from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import TYPE_CHECKING

import markdown
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes

from exceptions import ConfigError, LLMClientError, PlatformError

if TYPE_CHECKING:
    from ai_client import AIClient
    from rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

_MAX_USERS = 1000

# Conversation history keyed by Telegram user_id.
# Index 0 is always the system prompt — never evicted, never shared with Discord.
_history: dict[int, list[dict]] = {}
_user_locks: dict[int, asyncio.Lock] = {}
_reset_seq: dict[int, int] = {}

_SYSTEM_PROMPT: str | None = None
_MAX_HISTORY: int = 10
_rate_limiter: RateLimiter | None = None
_admin_user_ids: set[int] = set()
_startup_time: datetime = datetime.now(tz=timezone.utc)


class TelegramHTMLSanitizer(HTMLParser):
    """Sanitises raw HTML to conform strictly to Telegram's HTML styling support.

    Whitelisted tags: <b>, <i>, <code>, <pre>, <a>, <u>, <s>.
    All other tags are stripped, but their text content is preserved.
    """

    def __init__(self) -> None:
        super().__init__()
        self.result: list[str] = []
        self._whitelist = {"b", "strong", "i", "em", "code", "pre", "a", "u", "ins", "s", "strike", "del"}
        self._list_item_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # Convert strong/em to b/i for simplicity
        if tag == "strong":
            tag = "b"
        elif tag == "em":
            tag = "i"
        elif tag == "ins":
            tag = "u"
        elif tag in {"strike", "del"}:
            tag = "s"

        if tag in self._whitelist:
            attr_str = ""
            # Only allow href attribute for <a> tags
            if tag == "a":
                href = next((val for name, val in attrs if name == "href"), None)
                if href:
                    attr_str = f' href="{html.escape(href, quote=True)}"'
            self.result.append(f"<{tag}{attr_str}>")
        elif tag == "li":
            self.result.append("\n• ")
        elif tag in {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "br"}:
            self.result.append("\n")
        elif tag in {"ul", "ol"}:
            self.result.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "strong":
            tag = "b"
        elif tag == "em":
            tag = "i"
        elif tag == "ins":
            tag = "u"
        elif tag in {"strike", "del"}:
            tag = "s"

        if tag in self._whitelist:
            self.result.append(f"</{tag}>")
        elif tag in {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.result.append("\n")

    def handle_data(self, data: str) -> None:
        self.result.append(html.escape(data, quote=False))

    def get_html(self) -> str:
        raw = "".join(self.result)
        # Normalize line breaks
        cleaned = re.sub(r"\n\n+", "\n\n", raw)
        return cleaned.strip()


def _md_to_html(text: str) -> str:
    """Translate standard Markdown elements to safe Telegram HTML formatting.

    Uses python-markdown library and filters output through HTML sanitizer
    to guarantee zero parsing crashes on Telegram API.

    Args:
        text: Raw Markdown text.

    Returns:
        Formatted HTML string matching Telegram constraints.
    """
    # Convert MD to HTML using python-markdown
    html_raw = markdown.markdown(text, extensions=["fenced_code"])

    # Sanitise HTML
    parser = TelegramHTMLSanitizer()
    parser.feed(html_raw)
    return parser.get_html()


def _split_long_lines(lines: list[str], limit: int) -> list[str]:
    """Split any single line exceeding limit into smaller slices.

    Args:
        lines: Input lines.
        limit: Max characters per line.

    Returns:
        List of lines where each non-fence line is at most `limit` chars.
    """
    expanded: list[str] = []
    for line in lines:
        if len(line) <= limit or line.strip().startswith("```"):
            expanded.append(line)
        else:
            for i in range(0, len(line), limit):
                expanded.append(line[i : i + limit])
    return expanded


def _chunk_markdown(text: str, limit: int = 3500) -> list[str]:
    """Split markdown text into chunks, keeping code blocks intact where possible.

    Fences (```) are added to split code blocks to prevent syntax errors.

    Args:
        text: Raw Markdown text.
        limit: Max character length per chunk.

    Returns:
        List of chunks.
    """
    lines = _split_long_lines(text.split("\n"), limit)
    chunks: list[str] = []
    current_chunk: list[str] = []
    current_len = 0
    in_code = False
    code_lang = ""

    for line in lines:
        is_fence = line.strip().startswith("```")

        # Check if we must append closing fence to current chunk anyway
        if is_fence and in_code:
            current_chunk.append(line)
            current_len += len(line) + 1
            in_code = False
            code_lang = ""
            continue

        # If adding exceeds limit, split
        if current_len + len(line) + 1 > limit:
            if in_code:
                # Close code block in current chunk
                current_chunk.append("```")
                chunks.append("\n".join(current_chunk))
                # Start new chunk and reopen code block
                current_chunk = [f"```{code_lang}", line]
                current_len = len(current_chunk[0]) + len(line) + 2
            else:
                if current_chunk:
                    chunks.append("\n".join(current_chunk))
                current_chunk = [line]
                current_len = len(line)
                if is_fence:
                    in_code = True
                    code_lang = line.strip()[3:]
        else:
            current_chunk.append(line)
            current_len += len(line) + 1
            if is_fence:
                in_code = True
                code_lang = line.strip()[3:]

    if current_chunk:
        chunks.append("\n".join(current_chunk))
    return chunks


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


def _get_user_lock(user_id: int) -> asyncio.Lock:
    """Return a per-user asyncio.Lock, bounding total stored locks.

    Args:
        user_id: Telegram user ID.

    Returns:
        asyncio.Lock for the given user.
    """
    lock = _user_locks.get(user_id)
    if lock is not None:
        return lock

    if len(_user_locks) >= _MAX_USERS:
        for old_uid, old_lock in list(_user_locks.items()):
            if not old_lock.locked():
                _user_locks.pop(old_uid, None)
                _reset_seq.pop(old_uid, None)
                break

    lock = asyncio.Lock()
    _user_locks[user_id] = lock
    return lock


def _ensure_history(user_id: int) -> None:
    """Initialise history for a user if not yet present, bounding total users.

    Always inserts the system prompt at index 0.

    Args:
        user_id: Telegram user ID.
    """
    if user_id not in _history:
        if len(_history) >= _MAX_USERS:
            oldest_uid = next(iter(_history))
            _history.pop(oldest_uid, None)
        _history[user_id] = [{"role": "system", "content": _get_system_prompt()}]


def _append_and_evict(user_id: int, role: str, content: str) -> None:
    """Append a message and evict oldest non-system message if over limit.

    Args:
        user_id: Telegram user ID.
        role: 'user' or 'assistant'.
        content: Message text.
    """
    _ensure_history(user_id)
    _history[user_id].append({"role": role, "content": content})
    # Index 0 is system prompt — count only indices 1+
    while len(_history[user_id]) - 1 > _MAX_HISTORY:
        _history[user_id].pop(1)


async def _send_telegram_chunk(update: Update, raw_chunk: str) -> None:
    """Send a single markdown chunk rendered as safe HTML with plain-text fallback.

    Args:
        update: Incoming Telegram update with message.
        raw_chunk: Markdown chunk string.
    """
    if not update.message:
        return

    html_chunk = _md_to_html(raw_chunk)
    if len(html_chunk) <= 4096:
        try:
            await update.message.reply_text(html_chunk, parse_mode=ParseMode.HTML)
            return
        except BadRequest as exc:
            logger.warning("[TELEGRAM] Fallback ke plain text karena error HTML: %s", exc)

    for i in range(0, len(raw_chunk), 4096):
        await update.message.reply_text(raw_chunk[i : i + 4096])


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
            "Kasih pertanyaannya dong! Contoh: <code>/tanya apa itu AI?</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    ai_client: AIClient = context.bot_data["ai_client"]
    if _rate_limiter is None:
        raise PlatformError("RateLimiter belum dikonfigurasi di telegram bot", platform="telegram")

    user_id = update.effective_user.id

    allowed, retry_after = _rate_limiter.is_allowed("telegram", user_id)
    if not allowed:
        await update.message.reply_text(
            f"Sabar ya, kamu sudah terlalu banyak bertanya. Coba lagi dalam {retry_after} detik."
        )
        return

    pertanyaan = " ".join(context.args)
    await update.message.chat.send_action("typing")

    async with _get_user_lock(user_id):
        seq_before = _reset_seq.get(user_id, 0)
        _ensure_history(user_id)
        request_messages = [*_history[user_id], {"role": "user", "content": pertanyaan}]

        try:
            jawaban = await ai_client.chat(request_messages)
        except LLMClientError as exc:
            logger.error("[TELEGRAM] LLM error untuk user %d: %s", user_id, exc)
            await update.message.reply_text("Waduh, gagal dapat respons dari AI. Coba lagi nanti.")
            return

        if _reset_seq.get(user_id, 0) == seq_before:
            _append_and_evict(user_id, "user", pertanyaan)
            _append_and_evict(user_id, "assistant", jawaban)

    logger.info("[TELEGRAM] Menjawab user %d (%d chars)", user_id, len(jawaban))

    # Chunk the raw markdown first, then translate each chunk to HTML
    chunks = _chunk_markdown(jawaban, limit=3500)
    for chunk in chunks:
        await _send_telegram_chunk(update, chunk)


async def _cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /reset — hapus conversation history user yang memanggil.

    Args:
        update: Incoming Telegram update.
        context: PTB context (unused).
    """
    if not update.message or not update.effective_user:
        return

    user_id = update.effective_user.id
    _reset_seq[user_id] = _reset_seq.get(user_id, 0) + 1
    _history.pop(user_id, None)
    logger.info("[TELEGRAM] History user %d direset", user_id)
    await update.message.reply_text("History percakapan kamu sudah direset.")


async def _cmd_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /info — tampilkan info detail untuk admin ID, atau info umum untuk user lain.

    Args:
        update: Incoming Telegram update.
        context: PTB context carrying bot_data with ai_client.
    """
    if not update.message or not update.effective_user:
        return

    if update.effective_user.id not in _admin_user_ids:
        public_info = (
            "I am TanyaBot, a helpful AI assistant designed to assist you with a wide variety "
            "of tasks and conversations.\n\n"
            "Here are a few things I can help you with:\n\n"
            "• Answering questions: From general knowledge, history, and science to everyday curiosities.\n\n"
            "• Writing & editing: Drafting emails, essays, creative stories, summaries, or polishing your own writing.\n\n"
            "• Brainstorming: Generating creative ideas, planning projects, or organizing thoughts.\n\n"
            "• Learning & problem-solving: Explaining complex concepts in simple terms, assisting with coding/technical topics, and language learning.\n\n"
            "Is there a specific topic you're curious about or a project you're currently working on? I'd be happy to help!"
        )
        await update.message.reply_text(public_info)
        return

    ai_client: AIClient = context.bot_data["ai_client"]
    uptime = datetime.now(tz=timezone.utc) - _startup_time
    hours, remainder = divmod(int(uptime.total_seconds()), 3600)
    minutes, seconds = divmod(remainder, 60)

    uptime_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    processed = ai_client.processed_messages
    safe_model = html.escape(ai_client.model)

    teks = (
        f"🤖 <b>AI Bot Info</b>\n\n"
        f"• Model: <code>{safe_model}</code>\n"
        f"• Uptime: <code>{uptime_str}</code>\n"
        f"• Pesan Diproses: <code>{processed}</code>"
    )
    await update.message.reply_text(teks, parse_mode=ParseMode.HTML)


def setup_application(
    telegram_token: str,
    ai_client: AIClient,
    system_prompt: str,
    max_history: int,
    rate_limiter: RateLimiter,
    admin_user_ids: set[int] | None = None,
) -> Application:
    """Create and configure the PTB Application instance.

    The Application is returned but NOT started — the caller manages lifecycle
    via async context manager pattern for shared event loop compatibility.

    Args:
        telegram_token: Bot token from BotFather.
        ai_client: Shared LLM client instance.
        system_prompt: Text injected as system message for every user.
        max_history: Maximum non-system messages stored per user.
        rate_limiter: RateLimiter instance shared across platforms.
        admin_user_ids: Allowed Telegram user IDs that can view full /info details.

    Returns:
        Configured telegram.ext.Application instance.

    Raises:
        ConfigError: If arguments are invalid.
        PlatformError: If the token is rejected by Telegram.
    """
    global _SYSTEM_PROMPT, _MAX_HISTORY, _rate_limiter, _admin_user_ids, _startup_time  # noqa: PLW0603

    if not telegram_token:
        raise ConfigError("TELEGRAM_TOKEN tidak boleh kosong")
    if not system_prompt:
        raise ConfigError("SYSTEM_PROMPT tidak boleh kosong")
    if max_history < 1:
        raise ConfigError(f"MAX_HISTORY harus >= 1, dapat: {max_history}")

    _SYSTEM_PROMPT = system_prompt
    _MAX_HISTORY = max_history
    _rate_limiter = rate_limiter
    _admin_user_ids = set(admin_user_ids) if admin_user_ids else set()
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
