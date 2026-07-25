# TanyaBot — AI Discord & Telegram Chatbot Bridge

TanyaBot adalah chatbot bridge multi-platform terpadu untuk **Discord** dan **Telegram** yang ditenagai oleh model OpenAI-compatible API. Bot ini berjalan dalam satu proses Python, berbagi client API yang sama secara asinkron, namun menjaga memori percakapan dan rate limiter tetap terisolasi di tiap-tiap platform.

---

## Fitur Utama

- 🧠 **Shared LLM Client**: Menggunakan `httpx` async client tunggal dengan retry logic + exponential backoff 2 detik untuk kegagalan 5xx/timeout.
- 💾 **Isolated Conversation Memory**: Riwayat pesan per user disimpan mandiri dan tidak saling bocor antar platform. Mendukung pembatasan riwayat pesan (`MAX_HISTORY`) dengan metode FIFO eviction.
- ⏱️ **Sliding Window Rate Limiter**: Pembatasan spam pesan per-user per-platform. Jika terlampaui, mengembalikan pesan penolakan yang ramah.
- ⚡ **Dual Platform Concurrency**: Discord (`discord.py`) dan Telegram (`python-telegram-bot` v22+) berjalan secara asinkron di bawah satu Event Loop `asyncio`.
- 🔌 **UX Nyaman**: Mendukung Slash Commands di Discord serta rendering HTML bersih di Telegram (menghindari error parsing markdown dari API Telegram).

---

## Command Reference

### Discord Bot
- `!tanya <pertanyaan>` atau `/tanya <pertanyaan>`: Tanya sesuatu ke AI.
- `!reset` atau `/reset`: Hapus riwayat chat kamu di Discord.
- `!info` atau `/info`: Menampilkan model aktif, uptime bot, dan jumlah pesan diproses.

### Telegram Bot
- `/tanya <pertanyaan>`: Tanya sesuatu ke AI.
- `/reset`: Hapus riwayat chat kamu di Telegram.
- `/info`: Menampilkan info bot, uptime, dan total pesan sukses diproses.

---

## Konfigurasi Lingkungan (Environment Variables)

Salin `env/ai-bot.env.example` ke `env/ai-bot.env` dan sesuaikan nilainya:

| Variabel | Deskripsi | Default |
|---|---|---|
| `DISCORD_TOKEN` | Token bot Discord dari Developer Portal | *Wajib* |
| `TELEGRAM_TOKEN` | Token bot Telegram dari @BotFather | *Wajib* |
| `API_URL` | Endpoint URL LLM API (compatible OpenAI) | *Wajib* |
| `API_KEY` | Kunci otorisasi LLM API | *Wajib* |
| `API_MODEL` | ID model LLM yang aktif digunakan | `gpt-4o-mini` |
| `API_TIMEOUT` | Durasi maksimal request LLM (detik) | `30` |
| `SYSTEM_PROMPT` | Perintah dasar/peran asisten AI | `"Kamu adalah asisten AI yang membantu."` |
| `MAX_HISTORY` | Jumlah maksimal pesan riwayat yang disimpan | `10` |
| `RATE_LIMIT_CALLS`| Jumlah toleransi panggilan dalam window | `5` |
| `RATE_LIMIT_WINDOW`| Durasi jendela rate limit (detik) | `60` |
| `LOG_LEVEL` | Level pencatatan log (DEBUG/INFO/WARNING/ERROR) | `INFO` |

---

## Cara Menjalankan

### Cara 1: Menggunakan Docker Compose (Direkomendasikan)
1. Isi environment variables di dashboard deploy Dokploy / server host kamu.
2. Build dan jalankan service:
   ```bash
   docker compose up --build -d
   ```
3. Monitor log bot:
   ```bash
   docker compose logs -f
   ```

### Cara 2: Menjalankan Secara Lokal (Development)
1. Pastikan terinstall Python 3.11+.
2. Buat virtual environment dan install dependencies:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Load env dan jalankan bot:
   ```bash
   set -a && source env/ai-bot.env && set +a
   python bot.py
   ```

---

## Arsitektur Berkas

```
ai-bot/
├── bot.py                  # Entry point (asyncio concurrency loop)
├── ai_client.py            # HTTP Client LLM (httpx, counter, retry)
├── discord_handler.py      # Handler Discord (hybrid commands, embeds)
├── telegram_handler.py     # Handler Telegram (HTML sanitizer, chunking)
├── rate_limiter.py         # Shared Sliding Window Rate Limiter
├── exceptions.py           # Custom exception hierarchy
├── requirements.txt        # Pinned dependencies
├── Dockerfile               # Production Build image
├── docker-compose.yml      # Service docker definition
├── env/
│   ├── ai-bot.env.example  # Contoh / template env
│   └── ai-bot.env          # File env lokal (ter-ignore)
├── TASKS.md                # Tracker project milestones
└── README.md                # Dokumentasi utama
```
