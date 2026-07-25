# Tasks - AI Bot Project

> Source of truth: `plan.md` (phase order) + `AGENTS.md` (non-negotiables).
> Baca file ini sebelum setiap sesi. Jangan ulangi item `[x]` kecuali diperintah.

---

## Phase 1 — Refactor & Foundation

<!-- setup -->
- [ ] Buat skeleton folder sesuai Directory Tree di `AGENTS.md` (file kosong, `env/` sudah ada) [[phase-1-skeleton]]

<!-- implementasi -->
- [x] Buat `exceptions.py` — custom hierarchy: `BotError` → `ConfigError`, `LLMClientError`, `RateLimitExceeded`, `PlatformError` [[phase-1-exceptions]]
- [x] Buat `ai_client.py` — `AIClient.chat()`, `AIClient.close()`, baca env (`API_URL`, `API_KEY`, `API_MODEL`, `API_TIMEOUT`), retry max 2x backoff 2s untuk 5xx/timeout [[phase-1-ai-client]]
- [x] Buat `requirements.txt` — pinned deps (`discord.py`, `httpx`, `python-telegram-bot`) [[phase-1-requirements]]
- [x] Buat `discord_handler.py` — pindah logic dari `bot.py` lama, commands `!tanya` + `!reset`, memory `dict[int, list[dict]]` keyed `user_id`, terima `AIClient` via DI [[phase-1-discord-handler]]
- [x] Update `bot.py` — entry point `asyncio.run(main())` saja, tidak ada logic platform [[phase-1-bot-entrypoint]]
- [x] Update `env/ai-bot.env` — tambah: `API_TIMEOUT`, `SYSTEM_PROMPT`, `LOG_LEVEL`, `MAX_HISTORY`, `RATE_LIMIT_CALLS`, `RATE_LIMIT_WINDOW`, `TELEGRAM_TOKEN` [[phase-1-env-vars]]

<!-- verifikasi -->
- [x] ✅ `!tanya halo` → bot merespons (perilaku Discord tidak berubah setelah refactor)

---

## Phase 2 — Telegram Integration

<!-- implementasi -->
- [x] Buat `telegram_handler.py` — inisialisasi `Application`, commands `/tanya`, `/reset`, `/info`, terima `AIClient` via DI, memory `dict[int, list[dict]]` keyed Telegram `user_id` (terpisah dari Discord) [[phase-2-telegram-handler]]
- [x] Update `bot.py` — gabung Discord + Telegram via `asyncio.gather()` dalam satu event loop [[phase-2-concurrency]]
- [x] Implementasi graceful shutdown `SIGTERM` — urutan: stop Discord → stop Telegram → `await AIClient.close()` [[phase-2-shutdown]]

<!-- verifikasi -->
- [x] ✅ `docker compose up` → kedua bot online tanpa error startup
- [x] ✅ Discord `!tanya halo` → respons
- [x] ✅ Telegram `/tanya halo` → respons
- [x] ✅ `!reset` Discord tidak affect history Telegram (dan sebaliknya) — isolasi terbukti

---

## Phase 3 — Conversation Memory & Rate Limiting

<!-- implementasi -->
- [x] Memory: system prompt selalu index 0, tidak dihitung terhadap `MAX_HISTORY`, tidak pernah di-evict [[phase-3-system-prompt]]
- [x] Memory: FIFO eviction pesan tertua (non-system) saat `MAX_HISTORY` terlampaui [[phase-3-fifo-eviction]]
- [x] `!reset` / `/reset` — hapus hanya history user yang memanggil di platform tersebut [[phase-3-reset]]
- [x] `RateLimiter` class (di `ai_client.py` atau `utils.py`) — counter per-user per-platform; pesan penolakan: `"Sabar ya, kamu sudah terlalu banyak bertanya. Coba lagi dalam X detik."` [[phase-3-rate-limiter]]
- [x] Terapkan `RateLimiter` di `discord_handler.py` dan `telegram_handler.py` [[phase-3-apply-rate-limiter]]

<!-- verifikasi -->
- [x] ✅ Tanya 4–5x berurutan → bot ingat konteks
- [x] ✅ `!reset` → tanya lagi → fresh (tidak ada konteks sebelumnya)
- [x] ✅ Spam melebihi rate limit → dapat pesan penolakan Indonesian
- [x] ✅ Perilaku memory + rate limit identik di Discord dan Telegram

---

## Phase 4 — UX Improvements

<!-- implementasi -->
- [x] Discord: register `/tanya`, `/reset` via `discord.app_commands`, `await bot.tree.sync()` di `on_ready()`, prefix `!` tetap jalan [[phase-4-discord-slash]]
- [x] Discord: wrap respons AI dalam `discord.Embed` (warna biru, footer = nama model aktif); fallback plain text jika konten >4096 char [[phase-4-discord-embed]]
- [x] Telegram: `parse_mode=ParseMode.MARKDOWN`; chunk pesan jika >4096 char [[phase-4-telegram-format]]
- [x] `/info` (kedua platform): model aktif, uptime bot, jumlah pesan diproses sejak startup [[phase-4-info]]

<!-- verifikasi -->
- [x] ✅ Discord: `/tanya` muncul di slash command autocomplete
- [x] ✅ Discord: respons tampil dalam embed dengan footer nama model
- [x] ✅ Telegram: respons dengan markdown rendering rapi
- [x] ✅ `/info` tampil benar di Discord dan Telegram

---

## Phase 5 — Ops & Production Hardening

<!-- implementasi -->
- [x] Ganti semua `print()` dengan `logging` stdlib — format: `%(asctime)s [%(levelname)s] [%(platform)s] %(message)s`, level dari env `LOG_LEVEL` [[phase-5-logging]]
- [x] Tambah Docker healthcheck di `docker-compose.yml`: `test: ["CMD", "python", "-c", "import sys; sys.exit(0)"]`, interval 30s, timeout 10s, retries 3 [[phase-5-healthcheck]]
- [x] Buat `README.md` — setup (Discord + Telegram token), cara run, daftar commands, troubleshooting; bisa setup dari nol hanya dari README [[phase-5-readme]]

<!-- verifikasi -->
- [x] ✅ `docker compose ps` → status `healthy`
- [x] ✅ Log tampil rapi dengan label `[DISCORD]` / `[TELEGRAM]`
- [x] ✅ Cold reader bisa setup project dari `README.md` saja
