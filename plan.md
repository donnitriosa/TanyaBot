# Plan Pengembangan Discord + Telegram AI Bot

## Context
Bot Discord saat ini (`bot.py`, 81 baris) adalah bridge minimalis: user ketik `!tanya` → forward ke OpenAI-compatible API → balas ke channel. Stateless, single-file, tanpa memory, tanpa rate limiting, tanpa slash commands.

**Keputusan arsitektur:**
- Satu proses Python, dua platform (Discord + Telegram)
- Library Telegram: `python-telegram-bot`
- Conversation memory terpisah per platform
- Dijalankan via satu Docker container

---

## Struktur File Target

```
ai-bot/
├── bot.py                  # Entry point — jalankan Discord + Telegram concurrently
├── ai_client.py            # Shared LLM logic (httpx, format request, parse response)
├── discord_handler.py      # Semua logic Discord (commands, events)
├── telegram_handler.py     # Semua logic Telegram (handlers)
├── requirements.txt        # Pinned dependencies
├── Dockerfile              # Build image
├── docker-compose.yml      # Service config
├── env/ai-bot.env          # Env vars
└── README.md               # Dokumentasi
```

---

## Phase 1 — Refactor & Foundation

**Tujuan:** Pecah `bot.py` monolitik jadi modular, siap terima Telegram.

### 1.1 Buat `ai_client.py` (shared LLM logic)
- Pindahkan `httpx.AsyncClient`, request builder, response parser ke sini
- Class `AIClient`:
  ```python
  class AIClient:
      async def chat(self, messages: list[dict]) -> str: ...
      async def close(self): ...
  ```
- Baca dari env: `API_URL`, `API_KEY`, `API_MODEL`, `API_TIMEOUT`
- Handle error internal, return string (pesan error generik jika gagal)
- Retry logic: max 2 retry, backoff 2 detik untuk 5xx / timeout

### 1.2 Buat `discord_handler.py`
- Pindahkan semua kode Discord dari `bot.py` ke sini
- Terima `AIClient` instance sebagai dependency injection
- Conversation history: `dict[int, list[dict]]` keyed by Discord `user_id`
- Commands: `!tanya`, `!reset`
- Rate limiting per user

### 1.3 Buat `requirements.txt`
```
discord.py==2.3.2
httpx==0.27.0
python-telegram-bot==21.x
```

### 1.4 Update `bot.py` jadi entry point
```python
# Jalankan Discord bot dan Telegram bot secara concurrent
asyncio.run(main())  # start keduanya via asyncio.gather
```

### 1.5 Update env vars
Tambah ke `env/ai-bot.env`:
```
API_TIMEOUT=30
SYSTEM_PROMPT=Kamu adalah asisten AI yang membantu.
LOG_LEVEL=INFO
MAX_HISTORY=10
RATE_LIMIT_CALLS=5
RATE_LIMIT_WINDOW=60
TELEGRAM_TOKEN=isi_token_telegram_kamu
```

**Verifikasi Phase 1:** Discord bot masih jalan normal setelah refactor. `!tanya halo` → respons.

---

## Phase 2 — Integrasi Telegram

**Tujuan:** Telegram bot online dengan fitur setara Discord.

### 2.1 Buat `telegram_handler.py`
- Inisialisasi `python-telegram-bot` `Application`
- Terima `AIClient` instance (sama dengan Discord — shared, tidak shared memory)
- Conversation history: `dict[int, list[dict]]` keyed by Telegram `user_id` (terpisah dari Discord)
- Commands:
  - `/tanya <pertanyaan>` — forward ke LLM
  - `/reset` — hapus history user
  - `/info` — tampilkan model aktif, uptime
- Rate limiting: sama logic dengan Discord

### 2.2 Concurrency Discord + Telegram
- Discord pakai `bot.start()` (coroutine)
- Telegram pakai `application.run_polling()` atau async equivalent
- Keduanya dijalankan dalam satu `asyncio` event loop via `asyncio.gather()`
- `AIClient` instance satu, dipakai bersama (thread-safe karena async)

### 2.3 Graceful Shutdown
- Handle `SIGTERM` dari Docker
- Stop Discord bot + Telegram application + close `AIClient` dengan urutan benar

**Verifikasi Phase 2:**
- `docker compose up` → kedua bot online
- Discord: `!tanya halo` → respons
- Telegram: `/tanya halo` → respons
- History Discord dan Telegram tidak bocor satu sama lain (tanya di Discord → `/reset` di Telegram tidak affect Discord history)

---

## Phase 3 — Conversation Memory & Rate Limiting

**Tujuan:** Bot ingat konteks, tidak bisa di-spam.

### 3.1 Conversation Memory (kedua platform)
- System prompt disisipkan di index 0 setiap request
- Max history: `MAX_HISTORY` pesan (bukan token) per user
- Jika melebihi limit → buang pesan paling lama (FIFO), system prompt selalu dipertahankan
- `!reset` / `/reset` → hapus history user yang memanggil

### 3.2 Rate Limiting
- Implementasi `RateLimiter` class di `ai_client.py` atau file utils terpisah
- Dipakai oleh kedua handler
- Pesan penolakan: `"Sabar ya, kamu sudah terlalu banyak bertanya. Coba lagi dalam X detik."`

**Verifikasi Phase 3:**
- Tanya 5x berturut → bot masih ingat konteks
- `!reset` → tanya lagi → bot mulai fresh
- Spam > rate limit → dapat pesan penolakan
- Perilaku sama di Discord dan Telegram

---

## Phase 4 — UX Improvements

### 4.1 Discord Slash Commands
- Daftarkan `/tanya` dan `/reset` via `discord.app_commands`
- `await bot.tree.sync()` di `on_ready()`
- Pertahankan prefix `!` untuk kompatibilitas

### 4.2 Discord Embeds
- Wrap respons AI dalam `discord.Embed` (warna biru, footer: nama model)
- Fallback ke plain text jika > 4096 char

### 4.3 Telegram Formatting
- Gunakan `parse_mode=ParseMode.MARKDOWN` untuk format kode dan bold
- Handle Telegram message length limit (4096 char) → chunk jika perlu

### 4.4 `/info` Command (kedua platform)
- Tampilkan: model aktif, uptime bot, jumlah message diproses sejak startup

**Verifikasi Phase 4:**
- Discord: `/tanya` muncul di slash command autocomplete
- Discord: respons dalam embed dengan footer nama model
- Telegram: respons dengan markdown formatting rapi
- `/info` tampil di kedua platform

---

## Phase 5 — Ops & Production Hardening

### 5.1 Structured Logging
- Ganti semua `print()` dengan `logging` stdlib
- Format: `%(asctime)s [%(levelname)s] [%(platform)s] %(message)s`
- Level dari env `LOG_LEVEL`

### 5.2 Health Check Docker
```yaml
healthcheck:
  test: ["CMD", "python", "-c", "import sys; sys.exit(0)"]
  interval: 30s
  timeout: 10s
  retries: 3
```

### 5.3 README.md
- Deskripsi bot, cara setup env (Discord + Telegram token), cara jalankan, daftar commands, troubleshooting

**Verifikasi Phase 5:**
- `docker compose ps` → status `healthy`
- Log tampil rapi dengan label platform `[DISCORD]` / `[TELEGRAM]`
- Baca README → bisa setup dari nol

---

## File yang Dimodifikasi / Dibuat

| File | Action |
|---|---|
| `bot.py` | Refactor jadi entry point (Phase 1) |
| `ai_client.py` | **Buat baru** — shared LLM logic (Phase 1) |
| `discord_handler.py` | **Buat baru** — pindah + extend dari bot.py (Phase 1) |
| `telegram_handler.py` | **Buat baru** — Telegram integration (Phase 2) |
| `requirements.txt` | **Buat baru** (Phase 1) |
| `env/ai-bot.env` | Tambah env vars (Phase 1, 2) |
| `docker-compose.yml` | Tambah healthcheck (Phase 5) |
| `Dockerfile` | Pakai `requirements.txt` (Phase 1) |
| `README.md` | **Buat baru** (Phase 5) |

---

## Urutan Eksekusi

1. Phase 1 dulu — refactor tanpa tambah fitur, pastikan Discord tetap jalan
2. Phase 2 — integrasikan Telegram, test keduanya jalan concurrent
3. Phase 3 — memory + rate limiting (kedua platform sekaligus)
4. Phase 4 & 5 bisa paralel setelah Phase 3 stabil

---

## Verifikasi End-to-End

1. `docker compose build && docker compose up -d`
2. `docker compose logs -f` → tidak ada error startup, kedua bot online
3. Discord: `/tanya Siapa kamu?` → respons sesuai system prompt dalam embed
4. Telegram: `/tanya Siapa kamu?` → respons rapi dengan markdown
5. Tanya 4-5x di Discord → bot ingat konteks
6. `!reset` Discord → tanya lagi → fresh (history Telegram tidak terpengaruh)
7. Spam > rate limit → pesan penolakan
8. `docker compose ps` → status `healthy`
