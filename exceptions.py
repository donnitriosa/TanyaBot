from __future__ import annotations


class BotError(Exception):
    """Base exception for all ai-bot errors.

    Args:
        message: Human-readable description of the error.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ConfigError(BotError):
    """Raised when required configuration or env vars are missing or invalid.

    Args:
        message: Description of the missing/invalid config key.
    """


class LLMClientError(BotError):
    """Raised when the LLM API call fails after exhausting retries.

    Args:
        message: Description of the failure.
        status_code: HTTP status code from the last attempt, if available.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RateLimitExceeded(BotError):
    """Raised when a user exceeds the configured rate limit.

    Args:
        message: Rejection message shown to the user.
        retry_after: Seconds until the window resets.
    """

    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class PlatformError(BotError):
    """Raised when a platform-specific (Discord/Telegram) operation fails.

    Args:
        message: Description of the failure.
        platform: Platform name, e.g. 'discord' or 'telegram'.
    """

    def __init__(self, message: str, platform: str) -> None:
        super().__init__(message)
        self.platform = platform
