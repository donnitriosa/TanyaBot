# AI-BOT — Agent Instructions

Build a clean, modular, production-ready AI chatbot bridge for **Discord + Telegram**. Single Python process, dual platform (`discord.py` + `python-telegram-bot`), shared LLM client via `httpx`, per-platform conversation memory, single Docker container deployment.

---

## Workflow Rules

1. Read `TASKS.md` before every session to orient to current state.
2. Never rework items marked `[x]` in `TASKS.md` unless explicitly instructed.
3. Update `TASKS.md` immediately after testing a feature.
4. Never attempt to write the entire codebase in a single response — build phase by phase per `plan.md`, one phase per session unless told otherwise.
5. Create `TASKS.md` and `README.md` during project init. `plan.md` is the phase-by-phase source of truth; do not deviate from its scope without asking.

## Skills & Caveman Mode

- **GLOBAL:** All prompts processed as if `"Use caveman mode full"` is injected.
- Before ANY coding task, invoke and read: `using-superpowers`, `karpathy-guidelines`, `caveman`.
- Use `using-superpowers` to route to other relevant skills per task.

---

## Architecture Decisions (locked)

- **One process, two platforms.** Discord and Telegram bots run in the same `asyncio` event loop via `asyncio.gather()`. Never spawn separate processes/containers for each platform.
- **Shared LLM client, isolated memory.** One `AIClient` instance is injected into both handlers. Conversation history is **never** shared or cross-readable between Discord and Telegram — separate `dict[int, list[dict]]` keyed by platform-native `user_id`.
- **Telegram library:** `python-telegram-bot` (async API, `Application.run_polling()` or its async-context equivalent — never the sync/blocking variant inside the shared loop).
- **Deployment:** one Docker container, one `docker-compose.yml` service.

---

## Development Phases & Feature Hierarchy

Follow `plan.md` phase order. Do not start a later phase until the prior phase's verification steps pass.

### Phase 1 — Refactor & Foundation
- Extract shared LLM logic out of monolithic `bot.py` into `ai_client.py` (`AIClient.chat()`, `AIClient.close()`).
- Env-driven config: `API_URL`, `API_KEY`, `API_MODEL`, `API_TIMEOUT`.
- Retry: max 2 retries, 2s backoff, only on 5xx / timeout.
- Move existing Discord logic into `discord_handler.py`, injected with `AIClient`. Commands: `!tanya`, `!reset`. Conversation history keyed by Discord `user_id`.
- `bot.py` becomes the entry point only: `asyncio.run(main())`.
- **Verify:** Discord bot unchanged in behavior after refactor. `!tanya halo` → response.

### Phase 2 — Telegram Integration
- `telegram_handler.py`: `Application` init, same `AIClient` instance (shared client, **not** shared memory).
- Conversation history keyed by Telegram `user_id`, isolated from Discord's dict.
- Commands: `/tanya <question>`, `/reset`, `/info`.
- Both bots started under one `asyncio.gather()` in the same loop.
- Graceful shutdown on `SIGTERM`: stop order is Discord bot → Telegram application → `AIClient.close()`.
- **Verify:** `docker compose up` brings both bots online; `!tanya` and `/tanya` both respond; resetting one platform's history never affects the other's.

### Phase 3 — Conversation Memory & Rate Limiting
- System prompt always injected at index `0` of every request; never evicted.
- `MAX_HISTORY` caps stored messages per user (message count, not tokens) — FIFO eviction of oldest non-system message.
- `!reset` / `/reset` clears only the calling user's history on that platform.
- Shared `RateLimiter` class (in `ai_client.py` or a dedicated utils module), used identically by both handlers.
- **Verify:** 4–5 sequential questions retain context; reset clears context; spamming past the limit triggers the rejection message; behavior is identical across both platforms.

### Phase 4 — UX Improvements
- Discord: register `/tanya`, `/reset` as slash commands via `discord.app_commands`, `await bot.tree.sync()` in `on_ready()`. Keep the `!` prefix working alongside slash commands.
- Discord: wrap AI responses in `discord.Embed` (footer = active model name); fall back to plain text if content exceeds 4096 chars.
- Telegram: `parse_mode=ParseMode.MARKDOWN`; chunk outgoing messages that exceed Telegram's 4096-char limit.
- `/info` (both platforms): active model, bot uptime, messages processed since startup.
- **Verify:** slash command autocomplete works, embeds render with footer, Telegram markdown renders cleanly, `/info` correct on both platforms.

### Phase 5 — Ops & Production Hardening
- Replace all `print()` with stdlib `logging`. Format: `%(asctime)s [%(levelname)s] [%(platform)s] %(message)s`. Level from `LOG_LEVEL` env var.
- Docker healthcheck (`docker-compose.yml`) as specified in `plan.md`.
- `README.md`: setup (both bot tokens), run instructions, command reference, troubleshooting.
- **Verify:** `docker compose ps` shows `healthy`; logs are labeled `[DISCORD]` / `[TELEGRAM]`; a cold reader can set up the project from `README.md` alone.

---

## Critical Constraints

### Python Ecosystem
- `.venv` mandatory. Never touch global Python.
- Package install: `pip` with pinned versions in `requirements.txt`. No unpinned deps in production code paths.
- Secrets (`API_KEY`, `TELEGRAM_TOKEN`, Discord bot token) live only in `env/ai-bot.env` — never hardcoded, never logged.

### Platform Bridge
- Single `asyncio` event loop for the whole process; both platforms started via `asyncio.gather()`.
- `AIClient` is instantiated once and passed by dependency injection into `discord_handler.py` and `telegram_handler.py`. Never instantiate a second client.
- Conversation memory dicts are platform-local module state — no cross-import of one platform's history store into the other.

### Configuration
- All tunables come from `env/ai-bot.env`: `API_URL`, `API_KEY`, `API_MODEL`, `API_TIMEOUT`, `SYSTEM_PROMPT`, `LOG_LEVEL`, `MAX_HISTORY`, `RATE_LIMIT_CALLS`, `RATE_LIMIT_WINDOW`, `TELEGRAM_TOKEN`, plus the Discord token.
- No magic numbers for history size, rate limits, or timeouts inside handler code — read from config/env only.

### Conversation Memory
- Structure: `dict[int, list[dict]]`, keyed by platform-native user id, one dict per platform.
- Index `0` is always the system prompt; it is never counted against `MAX_HISTORY` and never evicted.
- Eviction is FIFO on the oldest non-system message once `MAX_HISTORY` is exceeded.

### Rate Limiting
- One `RateLimiter` implementation shared by both handlers (same class/instance semantics, per-user counters kept separate per platform).
- Rejection message (Indonesian, per `plan.md`): `"Sabar ya, kamu sudah terlalu banyak bertanya. Coba lagi dalam X detik."`

### Graceful Shutdown
- `SIGTERM` handler required. Shutdown order: stop Discord bot → stop Telegram application → `await AIClient.close()`. Do not close the shared client before both platform handlers have stopped using it.

---

## Python Standards

- **Type hints:** Every function fully annotated. `X | None` (not `Optional`). `from __future__ import annotations` at the top of every module.
- **Pydantic / dataclasses:** Structured config and API payloads should not be raw dicts once they leave the boundary they were parsed at.
- **Exceptions:** Custom hierarchy (e.g. in a shared `exceptions.py`): `BotError` → `ConfigError`, `LLMClientError`, `RateLimitExceeded`, `PlatformError`. No bare `except:`. Chain with `raise X from Y`.
- **Logging:** stdlib `logging` only, configured once at startup from `LOG_LEVEL`. No `print()`. Always include the `[platform]` label per the Phase 5 log format.
- **HTTP:** `httpx.AsyncClient`, reused (not recreated per call), closed explicitly on shutdown.
- **Async:** No blocking calls inside handler coroutines — anything CPU-bound or blocking-I/O goes through `asyncio.to_thread()`.
- **Function design:** Max ~40 lines. One function = one thing. Guard clauses, max 3 nesting levels. No boolean flags changing core behavior.
- **Docstrings:** Google-style (`Args:`, `Returns:`, `Raises:`). Comments explain *why*, not *what*.
- **Naming:** modules `snake_case`, classes `PascalCase`, functions `verb_noun`, constants `SCREAMING_SNAKE`, private `_prefix`.

---

## Non-Negotiable Rules

1. **No stubs.** Never use `# ... rest of the code`, bare `pass`, or `# TODO`. Every file must be complete, production-ready, fully typed.
2. **Never auto-run the pipeline.** Provide exact commands to test (`docker compose up -d`, `docker compose logs -f`, in-chat test messages) and wait for user feedback before proceeding.
3. **No guessing** on Discord/Telegram API architecture, embed/markdown formatting, retry/backoff behavior, or event-loop composition. Describe the ambiguity + options, await decision.
4. **Never leak conversation memory or rate-limit state across platforms.** This is the single most important invariant of the whole project — verify it explicitly at the end of every phase that touches memory or limiting.
5. **Never block the shared event loop.** Anything that could stall must be offloaded via `asyncio.to_thread()` or an async-native call.

---

## Directory Tree

```
ai-bot/
├── bot.py                  # Entry point — start Discord + Telegram concurrently
├── ai_client.py            # Shared LLM logic (httpx, request/response, retry, RateLimiter)
├── discord_handler.py      # All Discord logic (commands, events, embeds)
├── telegram_handler.py     # All Telegram logic (handlers, formatting)
├── exceptions.py           # Custom error hierarchy (BotError → ...)
├── requirements.txt        # Pinned dependencies
├── Dockerfile               # Build image
├── docker-compose.yml      # Service config + healthcheck
├── env/
│   └── ai-bot.env          # Env vars (tokens, tunables)
├── TASKS.md                # Task tracking backlog
├── plan.md                 # Phase-by-phase development plan (source of truth)
└── README.md                # Setup, commands, troubleshooting
```

---

## References

| File | Purpose |
|---|---|
| `plan.md` | Phase-by-phase development plan — read before every session |
| `TASKS.md` | Current project state — read before every session |
| `ai_client.py` | Shared LLM client, retry logic, `RateLimiter` |
| `discord_handler.py` | Discord commands, events, embed formatting |
| `telegram_handler.py` | Telegram handlers, markdown formatting |
| `exceptions.py` | Custom error hierarchy (`BotError` → domain errors) |
| `env/ai-bot.env` | Secrets and tunables (never commit real values) |
| `docker-compose.yml` | Service definition + healthcheck |
| `README.md` | Setup instructions (both bot tokens), command reference, troubleshooting |
