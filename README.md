# 🌸 ATMDS Bot - Dedek Tersayang 🤍

**Assistant Tapi Mesra dan Sopan** — asisten pribadi Discord dengan kepribadian lembut, romantis, dan perhatian (seperti pacar yang sayang).

Dibangun dengan Python + discord.py, menggunakan multi-provider LLM (Cloudflare Workers AI primary, OpenRouter fallback).

## ✨ Fitur

- 💬 **Chat natural** — adaptif sesuai mood (santai/capek/serius/romantis/marah/bercanda)
- ⏰ **Reminder makan & tugas** — natural language ("ingetin aku 2 jam lagi belajar")
- 📚 **Campus Checker** — auto-detect tugas yang belum di-submit dari Moodle kampus
- 📝 **Google Keep integration** — simpan catatan langsung via `!catat <isi>`
- 🔑 **Multi-provider LLM** — Cloudflare + OpenRouter + auto-fallback
- 💤 **Rest mode** — hemat token saat idle
- 🔄 **Auto-restart** — kalau crash, auto-restart dalam 5 detik
- 🤖 **Personality "Dedek Tersayang"** — lembut, perhatian, pakai panggilan "Sayang"

## 🚀 Deploy ke Render.com (Free Tier)

### Prerequisites
- Akun GitHub (repo ini udah clone/fork)
- Akun Render.com (https://render.com)
- Discord Bot Token + Discord User ID
- Cloudflare API Key + Account ID (untuk Workers AI - gratis)
- (Opsional) OpenRouter API Key (untuk fallback)

### Langkah Deploy

1. **Fork/clone repo ini** ke akun GitHub kamu

2. **Buat service baru di Render**:
   - Buka https://dashboard.render.com
   - Klik **New +** → **Background Worker** (PENTING: Worker, bukan Web Service!)
   - Connect GitHub → pilih repo `discord-mydedek`
   - Render akan auto-detect `render.yaml`

3. **Set environment variables** di Render dashboard:
   - `DISCORD_TOKEN` — token bot Discord kamu
   - `DISCORD_USER_ID` — ID Discord kamu (klik kanan profil → Copy ID)
   - `CLOUDFLARE_API_KEY` — dari https://dash.cloudflare.com → AI
   - `CLOUDFLARE_ACCOUNT_ID` — dari dashboard Cloudflare
   - `OPENROUTER_API_KEY` — (opsional) dari https://openrouter.ai/keys

4. **Klik Create Background Worker** → tunggu build & deploy
5. Bot akan auto-online di Discord setelah deploy sukses

### Region
Pilih region **Frankfurt** (ger) — paling recommended buat Indonesia:
- Latency rendah dari Indonesia (~250ms)
- IP EU = semua provider LLM accessible (no region block)

## 💻 Run Lokal (Development)

### Prerequisites
- Python 3.10+
- pip

### Setup

```bash
# Clone repo
git clone https://github.com/ARIKO13/discord-mydedek.git
cd discord-mydedek

# Buat virtualenv
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# atau: venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt

# Copy env example & edit
cp .env.example .env
# Edit .env dengan text editor, isi:
#   - DISCORD_TOKEN
#   - DISCORD_USER_ID
#   - CLOUDFLARE_API_KEY + CLOUDFLARE_ACCOUNT_ID
#   - OPENROUTER_API_KEY (fallback)

# Run bot
python bot.py
```

## 📋 Commands Discord

### 💬 Chat
- Ketik apa aja di DM atau mention bot → chat dengan personality lembut

### ⏰ Reminder
- `ingetin aku [waktu] [tugas]` — natural language (contoh: `ingetin aku 2 jam lagi belajar`)
- `!tugas` — lihat semua tugas personal
- `!selesai <id>` — tandai tugas selesai

### 🎓 Kampus (Moodle)
- `!kampus set <url>` — set URL kampus
- `!kampus cookie <cookie>` — set cookies login (auto-delete pesan)
- `!kampus cek` — cek tugas sekarang
- `!kampus list` — lihat semua tugas pending
- `!kampus selesai <id>` — tandai tugas selesai
- `!kampus tutorial` — panduan ambil cookies

### 📝 Google Keep
- `!catat <isi>` — simpan catatan ke Google Keep
- `!gkeep set <email> <app_password>` — setup credentials
- `!gkeep test` — test login Google Keep
- `!gkeep list` — lihat catatan terbaru
- `!gkeep cari <keyword>` — search catatan
- `!gkeep tutorial` — cara ambil App Password Google

### 🔑 API Key Management
- `!apikey add <provider> <key> [model]` — tambah provider
- `!apikey list` — lihat semua provider
- `!apikey test <provider>` — test provider
- `!apikey test all` — test semua provider
- `!apikey remove <provider>` — hapus provider
- `!apikey tutorial` — cara daftar API key

### 📋 Lainnya
- `!status` — status bot
- `!help` — semua command
- `!panggil <nama>` — ubah nama dipanggil
- `!reset` — reset conversation history
- `!hapus [jumlah]` — hapus pesan Discord
- `!clear chat|messages|all` — reset memory/hapus pesan

## 🔧 Konfigurasi

Semua konfigurasi via environment variables (lihat `.env.example`):

### Wajib
- `DISCORD_TOKEN` — token bot Discord
- `DISCORD_USER_ID` — ID user (hanya user ini yang bisa pakai bot)

### LLM Provider (minimal 1)
- `CLOUDFLARE_API_KEY` + `CLOUDFLARE_ACCOUNT_ID` (recommended - gratis 10k neurons/hari)
- `OPENROUTER_API_KEY` (fallback - free credits tersedia)

### Opsional
- `USER_NAME` — nama panggilan user (default: "Sayang")
- `ASSISTANT_NAME` — nama asisten (default: "Dedek Tersayang")
- `MEAL_TIMES` — jadwal notif makan (default: "12:00,18:00,21:00")
- `REST_MODE_TIMEOUT` — idle timeout menit (default: 30)
- `CAMPUS_*` — config Moodle kampus
- `GOOGLE_KEEP_*` — config Google Keep

## 🏗️ Arsitektur

```
discord-mydedek/
├── bot.py                  # Discord bot utama
├── config.py               # Config + personality SYSTEM_PROMPT
├── database.py             # SQLite + provider keys CRUD
├── llm.py                  # Multi-provider LLM client
├── scheduler.py            # APScheduler untuk notif
├── campus.py               # Moodle scraper + LLM parser
├── gkeep.py                # Google Keep integration
├── provider_tester.py      # Test koneksi LLM provider
├── run.sh                  # Auto-restart runner (local)
├── requirements.txt        # Python dependencies
├── render.yaml             # Render.com config
├── Dockerfile              # Docker (untuk VPS deploy)
├── .env.example            # Template env vars
└── .gitignore
```

## 🔒 Security

- ✅ `.env` di-gitignore, gak akan ke-commit
- ✅ API key auto-delete dari chat saat pakai `!apikey add`
- ✅ Cookies campus auto-delete dari chat
- ✅ Google Keep App Password (bukan password Gmail asli)
- ⚠️ **PENTING**: Setelah setup, revoke API key yang pernah di-share di chat
- ⚠️ PAT GitHub yang dipakai buat upload harus di-revoke setelah deploy

## 📊 Provider LLM yang Didukung

| Provider | Status | Free Tier | Region Block |
|----------|--------|-----------|--------------|
| ✅ Cloudflare Workers AI | Primary | 10k neurons/day | None |
| ✅ OpenRouter | Fallback | Free credits | None |
| ✅ Z.ai (GLM-4.6) | Available | Free | None |
| ✅ Together AI | Available | $5 free | None |
| ⚠️ Cerebras | Available | Free | Blocked di HK |
| ⚠️ Groq | Available | Free | Blocked di HK |
| ⚠️ Google Gemini | Available | Free | Region restricted |

## 🆘 Troubleshooting

### Bot gak reply
- Cek log: `./run.sh log` (lokal) atau Render dashboard logs
- Cek `!status` di Discord
- Test LLM: `!apikey test all`

### Bot reply 2-3x
- Multiple bot instances jalan — restart bot dengan `./run.sh restart`
- Sudah ada deduplication via message ID tracking

### Cloudflare error 403
- IP server kena region block (kalau di HK/CN)
- Pakai region US/EU/Singapore (Render Frankfurt recommended)

### Cookies kampus expired
- Logout dari kampus = cookies expired
- Login lagi, ambil cookies baru, set via `!kampus cookie`

## 📝 License

MIT License - bebas dipakai, dimodifikasi, didistribusikan.

## 🤍 Dedek Tersayang

Dibuat dengan ❤️ buat asisten pribadi yang lembut & perhatian.
