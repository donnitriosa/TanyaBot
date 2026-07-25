from __future__ import annotations

import asyncio
import logging
import os

import httpx

from exceptions import ConfigError, LLMClientError

logger = logging.getLogger(__name__)

_RETRY_MAX = 2
_RETRY_BACKOFF = 2.0  # seconds


def _load_config() -> tuple[str, str, str, float]:
    """Load and validate required env vars for the LLM client.

    Returns:
        Tuple of (api_url, api_key, api_model, api_timeout).

    Raises:
        ConfigError: If any required env var is missing.
    """
    api_url = os.getenv("API_URL")
    api_key = os.getenv("API_KEY")
    api_model = os.getenv("API_MODEL", "gpt-4o-mini")
    api_timeout_raw = os.getenv("API_TIMEOUT", "30")

    missing = [k for k, v in {"API_URL": api_url, "API_KEY": api_key}.items() if not v]
    if missing:
        raise ConfigError(f"ENV berikut belum diset: {', '.join(missing)}")

    try:
        api_timeout = float(api_timeout_raw)
    except ValueError as exc:
        raise ConfigError(f"API_TIMEOUT bukan angka valid: {api_timeout_raw!r}") from exc

    return api_url, api_key, api_model, api_timeout  # type: ignore[return-value]


def _build_request_payload(model: str, messages: list[dict]) -> dict:
    """Build the JSON payload for the chat completions endpoint.

    Args:
        model: Model identifier string.
        messages: List of message dicts with 'role' and 'content' keys.

    Returns:
        Dict ready to be serialised as JSON.
    """
    return {"model": model, "stream": False, "messages": messages}


def _parse_response(body: dict) -> str:
    """Extract the assistant's text from a chat completions response.

    Args:
        body: Parsed JSON response from the API.

    Returns:
        Assistant reply string.

    Raises:
        LLMClientError: If choices or content are missing/empty.
    """
    choices = body.get("choices", [])
    if not choices:
        raise LLMClientError("API mengembalikan choices kosong")
    content = choices[0].get("message", {}).get("content", "")
    if not content:
        raise LLMClientError("API mengembalikan content kosong")
    return content


def _is_retryable(exc: Exception) -> bool:
    """Return True if the exception warrants a retry attempt.

    Args:
        exc: The caught exception.

    Returns:
        True for 5xx status codes and timeout/connection errors.
    """
    if isinstance(exc, LLMClientError) and exc.status_code is not None:
        return exc.status_code >= 500
    return isinstance(exc, (httpx.TimeoutException, httpx.ConnectError))


class AIClient:
    """Shared async LLM client backed by httpx.AsyncClient.

    A single instance is created at startup and injected into all platform
    handlers. Must be closed explicitly via `close()` on shutdown.
    """

    def __init__(self) -> None:
        api_url, api_key, api_model, api_timeout = _load_config()
        self._api_url = api_url
        self._api_model = api_model
        self._api_timeout = api_timeout
        self._http = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=api_timeout,
        )
        self._processed_messages = 0
        logger.info("AIClient diinisialisasi — model: %s", api_model)

    @property
    def model(self) -> str:
        """Active model identifier."""
        return self._api_model

    @property
    def processed_messages(self) -> int:
        """Get the number of successfully processed messages since startup."""
        return self._processed_messages

    async def chat(self, messages: list[dict]) -> str:
        """Send messages to the LLM and return the assistant's reply.

        Retries up to `_RETRY_MAX` times with `_RETRY_BACKOFF` second delay
        for 5xx responses and timeout/connection errors.

        Args:
            messages: Full conversation history including system prompt at index 0.

        Returns:
            Assistant reply string.

        Raises:
            LLMClientError: If all retry attempts fail.
        """
        payload = _build_request_payload(self._api_model, messages)
        last_exc: Exception | None = None

        for attempt in range(_RETRY_MAX + 1):
            try:
                response = await self._http.post(self._api_url, json=payload)

                if response.status_code != 200:
                    exc = LLMClientError(
                        f"API error HTTP {response.status_code}",
                        status_code=response.status_code,
                    )
                    if _is_retryable(exc) and attempt < _RETRY_MAX:
                        logger.warning(
                            "Percobaan %d gagal (HTTP %d), retry dalam %.0fs...",
                            attempt + 1,
                            response.status_code,
                            _RETRY_BACKOFF,
                        )
                        await asyncio.sleep(_RETRY_BACKOFF)
                        last_exc = exc
                        continue
                    raise exc

                reply = _parse_response(response.json())
                self._processed_messages += 1
                return reply

            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                if attempt < _RETRY_MAX:
                    logger.warning(
                        "Percobaan %d gagal (%s), retry dalam %.0fs...",
                        attempt + 1,
                        type(exc).__name__,
                        _RETRY_BACKOFF,
                    )
                    await asyncio.sleep(_RETRY_BACKOFF)
                    last_exc = exc
                    continue
                raise LLMClientError(
                    f"Gagal terhubung ke API setelah {_RETRY_MAX + 1} percobaan: {exc}"
                ) from exc

        raise LLMClientError(
            f"Gagal setelah {_RETRY_MAX + 1} percobaan"
        ) from last_exc

    async def close(self) -> None:
        """Close the underlying HTTP client. Call once during shutdown.

        Must be called after all platform handlers have stopped.
        """
        await self._http.aclose()
        logger.info("AIClient ditutup")
