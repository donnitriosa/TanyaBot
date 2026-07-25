from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

from ai_client import AIClient
from discord_handler import setup_handler
from exceptions import ConfigError

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Configure stdlib logging from LOG_LEVEL env var.

    Format: %(asctime)s [%(levelname)s] [%(platform)s] %(message)s
    Falls back to INFO if LOG_LEVEL is unset or invalid.
    """
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        stream=sys.stdout,
    )


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


def _load_handler_config() -> tuple[str, int]:
    """Read SYSTEM_PROMPT and MAX_HISTORY from env.

    Returns:
        Tuple of (system_prompt, max_history).

    Raises:
        ConfigError: If SYSTEM_PROMPT is empty or MAX_HISTORY is invalid.
    """
    system_prompt = os.getenv("SYSTEM_PROMPT", "Kamu adalah asisten AI yang membantu.")
    max_history_raw = os.getenv("MAX_HISTORY", "10")

    try:
        max_history = int(max_history_raw)
    except ValueError as exc:
        raise ConfigError(f"MAX_HISTORY bukan integer valid: {max_history_raw!r}") from exc

    if max_history < 1:
        raise ConfigError(f"MAX_HISTORY harus >= 1, dapat: {max_history}")

    return system_prompt, max_history


async def main() -> None:
    """Entry point — start Discord bot in the asyncio event loop.

    Phase 1: Discord only.
    Phase 2: akan ditambah asyncio.gather() untuk Telegram.

    Raises:
        ConfigError: If required env vars are missing or invalid.
    """
    _configure_logging()
    logger.info("[BOT] Memulai ai-bot...")

    discord_token = _load_discord_token()
    system_prompt, max_history = _load_handler_config()

    ai_client = AIClient()
    bot = setup_handler(system_prompt=system_prompt, max_history=max_history)
    bot.ai_client = ai_client  # type: ignore[attr-defined]

    loop = asyncio.get_running_loop()

    async def _shutdown(sig: signal.Signals) -> None:
        """Graceful shutdown: Discord → AIClient.

        Args:
            sig: Signal that triggered shutdown.
        """
        logger.info("[BOT] Menerima signal %s, shutdown...", sig.name)
        await bot.close()
        await ai_client.close()
        logger.info("[BOT] Shutdown selesai")

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig,
            lambda s=sig: asyncio.create_task(_shutdown(s)),
        )

    try:
        await bot.start(discord_token)
    except ConfigError:
        raise
    except Exception as exc:
        logger.error("[BOT] Error tidak tertangani: %s", exc)
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ConfigError as exc:
        # Log before stdlib logging is fully configured — print acceptable here
        sys.stderr.write(f"[BOT] Config error: {exc}\n")
        sys.exit(1)
