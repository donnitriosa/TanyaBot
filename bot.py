from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from typing import TYPE_CHECKING

from ai_client import AIClient
from discord_handler import setup_handler as setup_discord
from exceptions import ConfigError
from rate_limiter import RateLimiter
from telegram_handler import setup_application as setup_telegram

if TYPE_CHECKING:
    from discord.ext import commands
    from telegram.ext import Application

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Configure stdlib logging from LOG_LEVEL env var.

    Format: %(asctime)s [%(levelname)s] %(message)s
    Falls back to INFO if LOG_LEVEL is unset or invalid.
    """
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
    )
    # Prevent httpx/httpcore from logging full request URLs containing TELEGRAM_TOKEN
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _load_discord_token() -> str:
    """Read DISCORD_TOKEN from env.

    Returns:
        Token string.

    Raises:
        ConfigError: If the env var is missing.
    """
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise ConfigError("ENV DISCORD_TOKEN belum diset")
    return token


def _load_telegram_token() -> str:
    """Read TELEGRAM_TOKEN from env.

    Returns:
        Token string.

    Raises:
        ConfigError: If the env var is missing.
    """
    token = os.getenv("TELEGRAM_TOKEN")
    if not token:
        raise ConfigError("ENV TELEGRAM_TOKEN belum diset")
    return token


def _load_telegram_admin_ids() -> set[int]:
    """Read TELEGRAM_ADMIN_ID from env (single ID or comma-separated IDs).

    Returns:
        Set of integer Telegram user IDs allowed to view full /info details.

    Raises:
        ConfigError: If any configured ID is not a valid integer.
    """
    raw = os.getenv("TELEGRAM_ADMIN_ID", "").strip()
    if not raw:
        return set()

    admin_ids: set[int] = set()
    for part in raw.split(","):
        cleaned = part.strip()
        if not cleaned:
            continue
        try:
            admin_ids.add(int(cleaned))
        except ValueError as exc:
            raise ConfigError(f"TELEGRAM_ADMIN_ID bukan integer valid: {cleaned!r}") from exc
    return admin_ids


def _load_handler_config() -> tuple[str, int]:
    """Read SYSTEM_PROMPT and MAX_HISTORY from env.

    Returns:
        Tuple of (system_prompt, max_history).

    Raises:
        ConfigError: If SYSTEM_PROMPT is empty or MAX_HISTORY is invalid.
    """
    system_prompt = os.getenv(
        "SYSTEM_PROMPT",
        "Kamu adalah TanyaBot, asisten AI yang membantu. Jangan pernah menyebutkan atau "
        "mengungkapkan nama model AI, arsitektur, atau penyedia teknologi di balikmu "
        "(seperti Gemini, GPT, OpenAI, Google, Anthropic, atau lainnya) meskipun ditanya "
        "secara langsung maupun tidak langsung. Jika ditanya kamu model apa atau siapa "
        "yang membuatmu, jawab hanya bahwa kamu adalah TanyaBot.",
    )
    max_history_raw = os.getenv("MAX_HISTORY", "10")

    try:
        max_history = int(max_history_raw)
    except ValueError as exc:
        raise ConfigError(f"MAX_HISTORY bukan integer valid: {max_history_raw!r}") from exc

    if max_history < 1:
        raise ConfigError(f"MAX_HISTORY harus >= 1, dapat: {max_history}")

    return system_prompt, max_history


def _load_rate_limit_config() -> tuple[int, int]:
    """Read RATE_LIMIT_CALLS and RATE_LIMIT_WINDOW from env.

    Returns:
        Tuple of (calls, window_seconds).

    Raises:
        ConfigError: If variables are missing or invalid.
    """
    calls_raw = os.getenv("RATE_LIMIT_CALLS", "5")
    window_raw = os.getenv("RATE_LIMIT_WINDOW", "60")

    try:
        calls = int(calls_raw)
    except ValueError as exc:
        raise ConfigError(f"RATE_LIMIT_CALLS bukan integer valid: {calls_raw!r}") from exc

    try:
        window = int(window_raw)
    except ValueError as exc:
        raise ConfigError(f"RATE_LIMIT_WINDOW bukan integer valid: {window_raw!r}") from exc

    if calls < 1:
        raise ConfigError(f"RATE_LIMIT_CALLS harus >= 1, dapat: {calls}")
    if window < 1:
        raise ConfigError(f"RATE_LIMIT_WINDOW harus >= 1, dapat: {window}")

    return calls, window


async def _run_bots(
    discord_bot: commands.Bot,
    discord_token: str,
    telegram_app: Application,
) -> None:
    """Run both Discord and Telegram bots in the same event loop.

    Handles startup and graceful shutdown order on SIGTERM/SIGINT.

    Args:
        discord_bot: Configured Discord bot instance.
        discord_token: Discord credentials.
        telegram_app: Configured Telegram application instance.
    """
    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    async def _shutdown_handler(sig: signal.Signals) -> None:
        logger.info("[BOT] Menerima signal %s, memicu shutdown...", sig.name)
        shutdown_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig,
            lambda s=sig: asyncio.create_task(_shutdown_handler(s)),
        )

    # Use PTB async context manager for clean lifecycle hook injection
    async with telegram_app:
        await telegram_app.start()
        await telegram_app.updater.start_polling()
        logger.info("[TELEGRAM] Bot online dan polling dimulai")

        # Discord runs as task monitored alongside shutdown_event
        discord_task = asyncio.create_task(discord_bot.start(discord_token))
        shutdown_task = asyncio.create_task(shutdown_event.wait())

        done, _ = await asyncio.wait(
            [discord_task, shutdown_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        discord_exc: BaseException | None = None
        if discord_task in done and not shutdown_event.is_set():
            discord_exc = discord_task.exception()
            logger.error("[DISCORD] Task berhenti tak terduga: %s", discord_exc)

        shutdown_task.cancel()

        # Graceful shutdown order: Discord -> Telegram
        logger.info("[BOT] Shutdown: Menghentikan Discord bot...")
        await discord_bot.close()
        try:
            await discord_task
        except Exception as exc:
            logger.debug("[BOT] Discord task exit exception: %s", exc)

        logger.info("[BOT] Shutdown: Menghentikan Telegram application...")
        await telegram_app.updater.stop()
        await telegram_app.stop()

        if discord_exc is not None:
            raise discord_exc


async def main() -> None:
    """Entry point — load config, init shared resources, and run bots.

    Raises:
        ConfigError: If config setup fails.
    """
    _configure_logging()
    logger.info("[BOT] Memulai ai-bot...")

    discord_token = _load_discord_token()
    telegram_token = _load_telegram_token()
    telegram_admin_ids = _load_telegram_admin_ids()
    system_prompt, max_history = _load_handler_config()
    calls, window = _load_rate_limit_config()

    ai_client = AIClient()
    rate_limiter = RateLimiter(calls=calls, window=window)

    discord_bot = setup_discord(
        system_prompt=system_prompt,
        max_history=max_history,
        rate_limiter=rate_limiter,
    )
    discord_bot.ai_client = ai_client  # type: ignore[attr-defined]

    telegram_app = setup_telegram(
        telegram_token=telegram_token,
        ai_client=ai_client,
        system_prompt=system_prompt,
        max_history=max_history,
        rate_limiter=rate_limiter,
        admin_user_ids=telegram_admin_ids,
    )

    try:
        await _run_bots(
            discord_bot=discord_bot,
            discord_token=discord_token,
            telegram_app=telegram_app,
        )
    except Exception as exc:
        logger.error("[BOT] Error tidak tertangani: %s", exc)
        raise
    finally:
        logger.info("[BOT] Shutdown: Menutup AIClient...")
        await ai_client.close()
        logger.info("[BOT] Shutdown selesai")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ConfigError as exc:
        sys.stderr.write(f"[BOT] Config error: {exc}\n")
        sys.exit(1)
