# 🎮 Free Fire Like Bot 🇮🇳

> **🇮🇳 Indian-Only Free Fire Profile Like Bot** — Automatically boost Free Fire profile likes using your own accounts!

## 📌 Credits

**👑 Owner & Developer: [@YourPOPPY42](https://t.me/YourPOPPY42)**

---

## ✨ About

This is a **Telegram bot** that automatically sends **Free Fire profile likes** (IND server) using purchased/private Free Fire accounts. Simply add your accounts, and the bot generates JWT tokens and sends likes directly to the Free Fire API — completely automated!

### Features
- 🔥 **Auto Likes** — Sends **1000 likes per request** to any IND Free Fire profile
- 🗄️ **Persistent Storage** — Accounts & tokens saved in **Supabase**, survive every restart/redeploy
- 🔄 **Auto Token Refresh** — Regenerates tokens automatically **every 45 minutes** (no manual work)
- 📋 **Easy Account Management** — Paste your account list with `/addaccounts`, deduplication built-in
- 🇮🇳 **IND-Only** — Designed for the Indian server, all accounts forced to IND
- ⚡ **Concurrent Liking** — Uses async requests in batches for maximum speed
- 🛡️ **Admin-Protected** — Only the bot owner can manage accounts & refresh tokens

---

## 🤖 Commands

| Command | Description | Access |
|---------|-------------|--------|
| `/start` | Welcome message | Everyone |
| `/help` | Show all commands | Everyone |
| `/like <uid> [amount]` | Send likes (default 1000, or custom amount) to an IND profile | Everyone |
| `/status` | Bot uptime & stats | Everyone |
| `/addaccounts <list>` | Add accounts (paste list) | 👑 Admin only |
| `/accounts` | View stored account count | 👑 Admin only |
| `/refresh` | Force-refresh IND tokens now | 👑 Admin only |

### /addaccounts Format
```
• UID: 6246091984
• PWD: POPPY_POPPY_X_EMPIRE_0Y6Zbvgw
• AccID: 16854671696 (optional)
• Name: POPPY_⁶⁶⁹⁷ (optional)
```

> 💡 Region is auto-set to **IND** — no need to include `Region: IND` (it's forced anyway)

---

## 🚀 Deployment (Render)

### 1. Supabase Setup
1. Create a free project at [supabase.com](https://supabase.com)
2. Open **SQL Editor** and run the contents of [`supabase_setup.sql`](supabase_setup.sql) — creates the `ff_accounts` and `ff_tokens` tables
3. Copy from **Project Settings → API**:
   - `SUPABASE_URL` (Project URL)
   - `SUPABASE_KEY` (service_role key — keep secret!)

### 2. Render Deployment
Deploy a **background worker / long-running service** with:
- Build command: `pip install -r bot_requirements.txt`
- Start command: `python bot.py`

### 3. Environment Variables
| Variable | Description | Required |
|----------|-------------|----------|
| `BOT_TOKEN` | Your bot token from @BotFather | ✅ Yes |
| `ADMIN_IDS` | Your Telegram numeric user ID | ✅ Yes |
| `SUPABASE_URL` | Supabase project URL | ✅ Yes |
| `SUPABASE_KEY` | Supabase service_role key | ✅ Yes |
| `REFRESH_INTERVAL` | Auto-refresh seconds (default `2700` = 45 min) | Optional |
| `LIKES_PER_REQUEST` | Likes per /like (default `1000`) | Optional |

---

## 🧠 How It Works

1. **Add accounts** → `/addaccounts` parses your pasted account list and stores them (Supabase)
2. **Auto token generation** → The bot generates JWT tokens from your account credentials using multiple converter APIs (tried in order until success)
3. **Auto refresh** → Every 45 minutes, the bot regenerates fresh tokens for all accounts and notifies the admin
4. **Send likes** → `/like <uid> [amount]`:
   - Reads accounts + tokens from Supabase
   - Encrypts the target UID via AES + protobuf
   - Sends likes concurrently to the Free Fire API (default 1000, or your custom amount)
   - Reports before/after like counts & nickname

---

## 📁 Project Structure

```
poppy-like-apiiii/
├── bot.py                  # 🤖 Main Telegram bot
├── bot_requirements.txt    # 📦 Dependencies
├── like_pb2.py             # Protobuf: like message
├── like_count_pb2.py       # Protobuf: player info response
├── uid_generator_pb2.py    # Protobuf: UID generator
├── render.yaml             # 🚀 Render deployment config
├── supabase_setup.sql      # 🗄️ Supabase tables setup
├── README.md
└── junk/                   # 🗑️ Old/irrelevant files (deletable)
```

---

## 🛠️ Tech Stack

- **Python 3.13**
- **python-telegram-bot 21.x** — Telegram bot framework
- **Supabase (PostgreSQL)** — Persistent storage
- **aiohttp** — Async HTTP for token generation + likes
- **PyCryptodome** — AES encryption
- **Protobuf** — Free Fire API message encoding

---

## ⚠️ Disclaimer

This project is for educational purposes only. Use at your own risk. Free Fire is a product of Garena. This bot is not affiliated with or endorsed by Garena.

---

## 👑 Created with ❤️ by [@YourPOPPY42](https://t.me/YourPOPPY42)

**POPPY-X-EMPIRE** 🔥 | Indian Free Fire Like Bot 🇮🇳