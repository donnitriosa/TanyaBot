from __future__ import annotations

import logging
import time
from collections import defaultdict

from exceptions import ConfigError

logger = logging.getLogger(__name__)

_MAX_TRACKED_USERS = 5000


class RateLimiter:
    """Sliding window rate limiter for multi-platform users.

    Tracks request timestamps per platform and user_id. Thread-safe for async
    use since operations are synchronous in-memory state mutations.
    """

    def __init__(self, calls: int, window: int) -> None:
        """Initialise the rate limiter.

        Args:
            calls: Number of allowed calls in the window.
            window: Time window size in seconds.

        Raises:
            ConfigError: If configuration values are invalid.
        """
        if calls < 1:
            raise ConfigError(f"RATE_LIMIT_CALLS harus >= 1, dapat: {calls}")
        if window < 1:
            raise ConfigError(f"RATE_LIMIT_WINDOW harus >= 1, dapat: {window}")

        self._calls = calls
        self._window = window
        # Storage: dict[platform, dict[user_id, list[timestamps]]]
        self._requests: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))

    def is_allowed(self, platform: str, user_id: int) -> tuple[bool, int]:
        """Check if a user request is allowed and update history.

        Cleans up expired timestamps outside the sliding window.

        Args:
            platform: Platform key (e.g. 'discord', 'telegram').
            user_id: Unique user identifier for that platform.

        Returns:
            Tuple of (allowed: bool, retry_after: int). retry_after is 0 if allowed.
        """
        now = time.time()
        platform_requests = self._requests[platform]
        cutoff = now - self._window

        user_history = platform_requests.get(user_id)
        if user_history is not None:
            while user_history and user_history[0] < cutoff:
                user_history.pop(0)
            if not user_history:
                platform_requests.pop(user_id, None)
                user_history = None

        if user_history is None:
            if len(platform_requests) >= _MAX_TRACKED_USERS:
                oldest_uid = next(iter(platform_requests))
                platform_requests.pop(oldest_uid, None)
            user_history = platform_requests[user_id]

        # Check limit
        if len(user_history) >= self._calls:
            # retry_after is the seconds until the oldest active timestamp falls out of the window
            oldest_active = user_history[0]
            retry_after = max(1, int(oldest_active + self._window - now))
            logger.warning(
                "[%s] Rate limit terlampaui untuk user %d. Retry after %d detik.",
                platform.upper(),
                user_id,
                retry_after,
            )
            return False, retry_after

        # Record new request
        user_history.append(now)
        return True, 0
