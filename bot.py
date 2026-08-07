"""
🎮 Free Fire Like Bot 🤖
Modern Telegram bot for sending Free Fire profile likes.
- Admin can add accounts via /addaccounts (pasted account list)
- Bot stores accounts persistently and generates JWT tokens itself
- Sends likes directly to the Free Fire API

👑 Owner & Developer: @YourPOPPY42
🇮🇳 Indian-Only Free Fire Like Bot

Deployment: Render (long-running service)
Dependencies: bot_requirements.txt
"""

import os
import re
import json
import logging
import asyncio
import signal
import binascii
import aiohttp
from aiohttp import web
from datetime import datetime

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from supabase import create_client

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    filters,
    MessageHandler,
)

import like_pb2
import like_count_pb2
import uid_generator_pb2
from google.protobuf.json_format import MessageToJson
from google.protobuf.message import DecodeError

# ============================================================
# CONFIGURATION
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")  # From @BotFather
API_BASE_URL = os.getenv("API_BASE_URL", "").rstrip("/")  # Optional fallback API
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

# Supabase persistent storage (survives Render restarts)
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")  # e.g. https://xyz.supabase.co
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")  # service_role key
SUPABASE_ACCOUNTS_TABLE = os.getenv("SUPABASE_ACCOUNTS_TABLE", "ff_accounts")
SUPABASE_TOKENS_TABLE = os.getenv("SUPABASE_TOKENS_TABLE", "ff_tokens")

# File storage (fallback local cache)
ACCOUNTS_FILE = os.getenv("ACCOUNTS_FILE", "accounts.json")
TOKENS_FILE = os.getenv("TOKENS_FILE", "bot_tokens.json")

# Like settings
LIKES_PER_REQUEST = os.getenv("LIKES_PER_REQUEST", "1000")
try:
    LIKES_PER_REQUEST = int(LIKES_PER_REQUEST)
except ValueError:
    LIKES_PER_REQUEST = 1000

TOKEN_TTL = 3600  # 1 hour cache for tokens before refresh
REFRESH_INTERVAL = int(os.getenv("REFRESH_INTERVAL", "2700"))  # 45 min auto-refresh

# Proxy settings
# Priority: 1) Stored proxies (from /addproxies), 2) Environment variable
PROXY_LIST = []  # Will be populated from storage or env vars
PROXY_FILE = os.getenv("PROXY_FILE", "proxies.json")  # Persistent proxy storage

# Debug mode: set DEBUG=true in Render env vars for verbose logs
DEBUG_MODE = os.getenv("DEBUG", "").lower() in ("1", "true", "yes", "on")
LOG_LEVEL = logging.DEBUG if DEBUG_MODE else logging.INFO

# ============================================================
# JWT CONVERTER APIS (tried in order)
# ============================================================

JWT_APIS = [
    {
        "name": "lovable",
        "url": "https://ff-jwt-gen-api.lovable.app/api/public/token",
        "params": {"uid": "{uid}", "password": "{password}"},
        "token_fields": ["token", "jwt_token", "jwt"],
        "uid_fields": ["uid", "game_uid", "account_uid"],
        "region_field": "region",
        "access_fields": ["token_access", "access_token"],
        "open_id_fields": ["open_id", "oPeN_iD"],
    },
    {
        "name": "generator",
        "url": "https://jwt-generator-eight.vercel.app/jwt",
        "params": {"uid": "{uid}", "password": "{password}"},
        "token_fields": ["jwt", "token"],
        "uid_fields": ["game_uid", "uid", "account_uid"],
        "region_field": "region",
        "access_fields": ["access_token", "token_access"],
        "open_id_fields": ["open_id"],
    },
    {
        "name": "vaibhav",
        "url": "https://jwt.vaibhavapis.pro/Bmw",
        "params": {"uid": "{uid}", "password": "{password}"},
        "token_fields": ["jwt_token", "token"],
        "uid_fields": ["uid", "account_uid", "game_uid"],
        "region_field": "region",
        "access_fields": ["access_token", "token_access"],
        "open_id_fields": ["open_id"],
    },
    {
        "name": "vaibhav_ip",
        "url": "http://187.127.175.208:5001/Bmw",
        "params": {"uid": "{uid}", "password": "{password}"},
        "token_fields": ["JwT_ToKeN", "jwt_token", "token"],
        "uid_fields": ["UiD", "uid", "account_uid"],
        "region_field": "ReGioN",
        "access_fields": ["AccEss_ToKeN", "access_token"],
        "open_id_fields": ["oPeN_iD", "open_id"],
    },
]

# ============================================================
# HELPERS — SUPABASE & DATA STORAGE
# ============================================================

# Lazy singleton Supabase client
_supabase = None


def get_supabase():
    """Get the Supabase client (lazy init). Returns None if not configured."""
    global _supabase
    if _supabase is not None:
        return _supabase
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        return _supabase
    except Exception as e:
        logging.error(f"Supabase init error: {e}")
        return None


def load_accounts():
    """
    Load stored accounts. Primary: Supabase. Fallback: local JSON file.
    Returns dict: {REGION: [account_dict, ...]}
    """
    sb = get_supabase()
    if sb:
        try:
            response = sb.table(SUPABASE_ACCOUNTS_TABLE).select("*").execute()
            rows = response.data if response and response.data else []
            data = {}
            for row in rows:
                region = row.get("region", "").upper()
                if not region:
                    continue
                acc = {
                    "uid": str(row.get("uid", "")),
                    "password": row.get("password", ""),
                    "acc_id": str(row.get("acc_id", "") or ""),
                    "name": row.get("name", "") or "",
                    "region": region,
                }
                data.setdefault(region, []).append(acc)
            return data
        except Exception as e:
            logging.error(f"Supabase load_accounts error: {e}")
    # Fallback to file
    if os.path.exists(ACCOUNTS_FILE):
        try:
            with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading accounts from file: {e}")
    return {}


def save_accounts(data):
    """
    Save stored accounts. Primary: Supabase (upsert all). Fallback: local file.
    """
    sb = get_supabase()
    if sb:
        try:
            # Clear existing rows then insert all
            sb.table(SUPABASE_ACCOUNTS_TABLE).delete().gt("id", 0).execute()
            rows = []
            for region, accounts in data.items():
                for acc in accounts:
                    rows.append({
                        "uid": acc.get("uid", ""),
                        "password": acc.get("password", ""),
                        "acc_id": acc.get("acc_id", ""),
                        "name": acc.get("name", ""),
                        "region": acc.get("region", region),
                    })
            if rows:
                sb.table(SUPABASE_ACCOUNTS_TABLE).insert(rows).execute()
            return True
        except Exception as e:
            logging.error(f"Supabase save_accounts error: {e}")
            # fall through to file
    # Fallback to file
    try:
        with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logging.error(f"Error saving accounts to file: {e}")
        return False


def load_bot_tokens():
    """
    Load cached tokens. Primary: Supabase. Fallback: local JSON file.
    Returns dict: {uid: token_data}
    """
    sb = get_supabase()
    if sb:
        try:
            response = sb.table(SUPABASE_TOKENS_TABLE).select("*").execute()
            rows = response.data if response and response.data else []
            data = {}
            for row in rows:
                uid = str(row.get("uid", ""))
                if not uid:
                    continue
                data[uid] = {
                    "token": row.get("token", ""),
                    "uid": uid,
                    "region": row.get("region", ""),
                    "access_token": row.get("access_token", ""),
                    "open_id": row.get("open_id", ""),
                    "fetched_at": row.get("fetched_at", 0) or 0,
                }
            return data
        except Exception as e:
            logging.error(f"Supabase load_bot_tokens error: {e}")
    if os.path.exists(TOKENS_FILE):
        try:
            with open(TOKENS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_bot_tokens(data):
    """
    Save cached tokens. Primary: Supabase (upsert all). Fallback: local file.
    """
    sb = get_supabase()
    if sb:
        try:
            sb.table(SUPABASE_TOKENS_TABLE).delete().gt("id", 0).execute()
            rows = []
            for uid, tok in data.items():
                rows.append({
                    "uid": str(uid),
                    "token": tok.get("token", ""),
                    "region": tok.get("region", ""),
                    "access_token": tok.get("access_token", ""),
                    "open_id": tok.get("open_id", ""),
                    "fetched_at": int(tok.get("fetched_at", 0) or 0),
                })
            if rows:
                sb.table(SUPABASE_TOKENS_TABLE).insert(rows).execute()
            return True
        except Exception as e:
            logging.error(f"Supabase save_bot_tokens error: {e}")
    try:
        with open(TOKENS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logging.error(f"Error saving tokens to file: {e}")


# ============================================================
# HELPERS — PROXY MANAGEMENT
# ============================================================

def load_proxies():
    """Load stored proxies from file. Returns list of proxy strings."""
    if os.path.exists(PROXY_FILE):
        try:
            with open(PROXY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return [p.strip() for p in data if p.strip()]
                elif isinstance(data, dict) and "proxies" in data:
                    return [p.strip() for p in data["proxies"] if p.strip()]
        except Exception as e:
            logging.error(f"Error loading proxies from file: {e}")
    return []


def save_proxies(proxies):
    """Save proxies to file."""
    try:
        with open(PROXY_FILE, "w", encoding="utf-8") as f:
            json.dump({"proxies": proxies}, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logging.error(f"Error saving proxies to file: {e}")
        return False


def add_proxies_to_storage(new_proxies):
    """Add proxies to storage, deduplicating."""
    existing = load_proxies()
    added = 0
    skipped = 0
    for proxy in new_proxies:
        proxy = proxy.strip()
        if not proxy:
            continue
        # Normalize proxy format (add http:// if no scheme)
        if not proxy.startswith(("http://", "https://", "socks4://", "socks5://")):
            proxy = "http://" + proxy
        if proxy in existing:
            skipped += 1
            continue
        existing.append(proxy)
        added += 1
    save_proxies(existing)
    # Reload global PROXY_LIST
    global PROXY_LIST
    PROXY_LIST = existing
    return added, skipped


def init_proxies():
    """Initialize PROXY_LIST from storage or environment variable."""
    global PROXY_LIST
    # Priority: 1) File storage, 2) Environment variable
    stored = load_proxies()
    if stored:
        PROXY_LIST = stored
    else:
        # Fallback to env var
        env_proxies = [p.strip() for p in os.getenv("PROXY_LIST", "").split(",") if p.strip()]
        if env_proxies:
            PROXY_LIST = env_proxies
            save_proxies(PROXY_LIST)


# ============================================================
# HELPERS — ACCOUNT PARSING
# ============================================================

# Line patterns for the account format:
# • UID: 6246091984
# • PWD: POPPY_POPPY_X_EMPIRE_0Y6Zbvgw
# • AccID: 16854671696
# • Name: POPPY_⁶⁶⁹⁷
# • Region: IND
UID_LINE = re.compile(r"[•*\-]\s*UID\s*[:=]\s*(\d+)", re.IGNORECASE)
PWD_LINE = re.compile(r"[•*\-]\s*PWD\s*[:=]\s*(.+)", re.IGNORECASE)
ACCID_LINE = re.compile(r"[•*\-]\s*AccID\s*[:=]\s*(\d+)", re.IGNORECASE)
NAME_LINE = re.compile(r"[•*\-]\s*Name\s*[:=]\s*(.+)", re.IGNORECASE)


def parse_accounts(text):
    """
    Parse account blocks from pasted text by scanning line-by-line.
    Returns list of dicts: {uid, password, acc_id, name, region}
    """
    accounts = []
    current = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            # Empty line = end of current account block, save it if complete
            if current and current.get("uid") and current.get("password"):
                accounts.append(current)
                current = None
            continue

        # Detect account UID line
        uid_match = UID_LINE.search(line)
        if uid_match and current is None:
            # Start a new account if UID is found and we don't have a pending one
            # Indian-only like bot: all accounts forced to IND region
            current = {"uid": uid_match.group(1), "password": "", "acc_id": "", "name": "", "region": "IND"}
            continue

        if current is None:
            continue

        # Fill in fields for the current account
        pwd_match = PWD_LINE.search(line)
        if pwd_match and not current["password"]:
            current["password"] = pwd_match.group(1).strip()
            continue

        accid_match = ACCID_LINE.search(line)
        if accid_match:
            current["acc_id"] = accid_match.group(1).strip()
            continue

        name_match = NAME_LINE.search(line)
        if name_match:
            current["name"] = name_match.group(1).strip()
            continue

        # All accounts are IND (Indian-only like bot)

    # Save the last pending account if complete
    if current and current.get("uid") and current.get("password"):
        accounts.append(current)

    return accounts


def add_accounts_to_storage(accounts):
    """Add parsed accounts to storage, deduplicating by UID."""
    data = load_accounts()
    added = 0
    skipped = 0
    for acc in accounts:
        region = acc["region"]
        if region not in data:
            data[region] = []
        # Deduplicate by UID
        existing = [a for a in data[region] if a["uid"] == acc["uid"]]
        if existing:
            skipped += 1
            continue
        data[region].append(acc)
        added += 1
    save_accounts(data)
    return added, skipped


def get_region_accounts(region):
    """Get all stored accounts for a region."""
    data = load_accounts()
    return data.get(region.upper(), [])


# ============================================================
# HELPERS — TOKEN GENERATION
# ============================================================

def _get_ci(data, key):
    """Case-insensitive dict lookup."""
    if not data or not key:
        return None
    if key in data:
        return data[key]
    key_lower = key.lower()
    for k, v in data.items():
        if k.lower() == key_lower:
            return v
    return None


def normalize_token(api, data):
    """Extract token fields from API response case-insensitively."""
    def find(data, fields):
        for f in fields:
            v = _get_ci(data, f)
            if v:
                return v
        return None

    token = find(data, api["token_fields"])
    if not token:
        return None

    uid = find(data, api["uid_fields"])
    region = _get_ci(data, api.get("region_field", "region")) or ""
    access = find(data, api.get("access_fields", ["access_token"])) or ""
    open_id = find(data, api.get("open_id_fields", ["open_id"])) or ""

    return {
        "token": token,
        "uid": str(uid) if uid else "",
        "region": region,
        "access_token": access,
        "open_id": open_id,
    }


async def fetch_token_async(uid, password):
    """Fetch JWT token from APIs (async). First success wins."""
    for api in JWT_APIS:
        try:
            params = {}
            for k, v in api["params"].items():
                params[k] = v.replace("{uid}", uid).replace("{password}", password)
            async with aiohttp.ClientSession() as session:
                async with session.get(api["url"], params=params, timeout=30) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        normalized = normalize_token(api, data)
                        if normalized:
                            return normalized
        except Exception as e:
            logging.error(f"Token API {api['name']} error: {e}")
            continue
    return None


async def get_token_for_account(account, force=False):
    """
    Get a valid token for an account. Cached in bot_tokens.json (unless force).
    Returns (token_data, is_new).
    """
    token_cache = load_bot_tokens()
    uid = account["uid"]
    cached = token_cache.get(uid)
    now = int(datetime.now().timestamp())  # integer seconds (BIGINT compatible)
    if not force and cached and cached.get("fetched_at", 0) >= now - TOKEN_TTL:
        return cached, False

    token_data = await fetch_token_async(uid, account["password"])
    if token_data:
        token_data["fetched_at"] = now
        token_cache[uid] = token_data
        save_bot_tokens(token_cache)
        return token_data, True
    return None, False


# ============================================================
# HELPERS — ENCRYPTION & PROTOBUF (mirrors app.py)
# ============================================================

AES_KEY = b'Yg&tc%DEuh6%Zc^8'
AES_IV = b'6oyZDr22E3ychjM%'


def encrypt_message(plaintext):
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
    padded = pad(plaintext, AES.block_size)
    encrypted = cipher.encrypt(padded)
    return binascii.hexlify(encrypted).decode('utf-8')


def create_like_protobuf(user_id, region):
    message = like_pb2.like()
    message.uid = int(user_id)
    message.region = region
    return message.SerializeToString()


def create_uid_protobuf(uid):
    message = uid_generator_pb2.uid_generator()
    message.saturn_ = int(uid)
    message.garena = 1
    return message.SerializeToString()


def enc(uid):
    protobuf_data = create_uid_protobuf(uid)
    return encrypt_message(protobuf_data)

# ============================================================
# HELPERS — FREE FIRE API REQUESTS
# ============================================================

def get_endpoint(server_name, action):
    """Get the Free Fire endpoint for a server and action."""
    if action == "info":
        if server_name == "IND":
            return "https://client.ind.freefiremobile.com/GetPlayerPersonalShow"
        elif server_name in {"BR", "US", "SAC", "NA"}:
            return "https://client.us.freefiremobile.com/GetPlayerPersonalShow"
        else:
            return "https://clientbp.ggpolarbear.com/GetPlayerPersonalShow"
    elif action == "like":
        if server_name == "IND":
            return "https://client.ind.freefiremobile.com/LikeProfile"
        elif server_name in {"BR", "US", "SAC", "NA"}:
            return "https://client.us.freefiremobile.com/LikeProfile"
        else:
            return "https://clientbp.ggpolarbear.com/LikeProfile"
    return None


def build_headers(token):
    return {
        'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
        'Connection': "Keep-Alive",
        'Accept-Encoding': "gzip",
        'Authorization': f"Bearer {token}",
        'Content-Type': "application/x-www-form-urlencoded",
        'Expect': "100-continue",
        'X-Unity-Version': "2018.4.11f1",
        'X-GA': "v1 1",
        'ReleaseVersion': "OB54",
    }


async def get_player_info(encrypted_uid, server_name, token):
    """Fetch player info (like count). Async — non-blocking."""
    logger = logging.getLogger(__name__)
    url = get_endpoint(server_name, "info")
    edata = bytes.fromhex(encrypted_uid)
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, data=edata, headers=build_headers(token)) as response:
                if response.status != 200:
                    logger.warning(f"get_player_info: HTTP {response.status} for {server_name}")
                    return None
                content = await response.read()
        try:
            items = like_count_pb2.Info()
            items.ParseFromString(content)
            return items
        except DecodeError:
            logger.error(f"get_player_info decode error: {content[:100]}")
            return None
    except Exception as e:
        logger.error(f"get_player_info error: {e}")
        return None


async def send_single_like(session, encrypted_uid, token, url, proxy=None):
    """Send a single like request. Returns (status, body_text).

    IMPORTANT: The response body MUST be read (like the original app.py's
    send_request does with response.text()). Not reading the body means the
    HTTP keep-alive connection is released without consuming the response,
    so the Garena server never fully commits the like.
    """
    logger = logging.getLogger(__name__)
    edata = bytes.fromhex(encrypted_uid)
    try:
        kwargs = {}
        if proxy:
            kwargs['proxy'] = proxy
        async with session.post(url, data=edata, headers=build_headers(token), **kwargs) as response:
            status = response.status
            body = await response.text()  # consume the body — REQUIRED for the like to commit
            if status != 200:
                logger.debug(f"Like request returned HTTP {status}: {body[:100]}")
            return status, body
    except Exception as e:
        logger.warning(f"Like request error: {e}")
        return None, ""


async def send_likes(target_uid, server_name, tokens, like_amount=None):
    """Send likes using multiple requests per credential with proxy rotation.
    
    The like_amount parameter represents the NUMBER OF CREDENTIALS to use.
    Each credential sends REQUESTS_PER_CREDENTIAL (500) concurrent requests.
    Each credential gets a unique proxy (rotated) to avoid IP blocking.
    The server registers only 1 like per credential, but flooding with
    500 requests per credential maximizes the chance of success.
    
    Example: /like (uid) 1000 = use 1000 credentials × 500 requests = 500,000 total requests
    
    Returns number of 200 HTTP responses.
    """
    url = get_endpoint(server_name, "like")
    protobuf_message = create_like_protobuf(target_uid, server_name)
    encrypted_uid = encrypt_message(protobuf_message)
    count_200 = 0
    body_samples = []
    logger = logging.getLogger(__name__)
    
    # Each credential sends 500 requests to maximize chance of 1 like landing
    REQUESTS_PER_CREDENTIAL = 500
    requested_credentials = like_amount or LIKES_PER_REQUEST
    
    # Use up to requested_credentials, limited by available tokens
    used_credentials = min(len(tokens), requested_credentials)
    
    proxy_info = f"with {len(PROXY_LIST)} proxies" if PROXY_LIST else "without proxies (direct)"
    logger.debug(f"send_likes: target={target_uid} server={server_name} tokens={len(tokens)} using={used_credentials} credentials x {REQUESTS_PER_CREDENTIAL} reqs each = {used_credentials * REQUESTS_PER_CREDENTIAL} total requests ({proxy_info})")
    
    timeout = aiohttp.ClientTimeout(total=120)
    
    # Create one session per credential with its own proxy
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # For each credential, send REQUESTS_PER_CREDENTIAL concurrent requests
        all_tasks = []
        for cred_idx in range(used_credentials):
            token = tokens[cred_idx]
            # Assign a proxy to this credential (cycle through proxy list)
            proxy = PROXY_LIST[cred_idx % len(PROXY_LIST)] if PROXY_LIST else None
            # Send 500 concurrent requests with this token + proxy
            for _ in range(REQUESTS_PER_CREDENTIAL):
                all_tasks.append(send_single_like(session, encrypted_uid, token, url, proxy))
        
        # Execute all requests concurrently
        results = await asyncio.gather(*all_tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, tuple) and r[0] == 200:
                count_200 += 1
                if len(body_samples) < 3:
                    body_samples.append(r[1])
    
    if body_samples:
        logger.info(f"send_likes LikeProfile response sample: {body_samples}")
    logger.debug(f"send_likes: total OK = {count_200}")
    return count_200


# ============================================================
# BOT COMMAND HANDLERS
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "✨ *Welcome to Free Fire Like Bot!* ✨\n\n"
        "🔥 Boost Free Fire profile likes automatically!\n\n"
        "🎯 *Send likes:*\n"
        "`/like <uid>`\n\n"
        "📥 *Add accounts (admin):*\n"
        "`/addaccounts <pasted list>`\n\n"
        "🖥️ *Server:* `IND` 🇮🇳\n\n"
        "Use /help for full commands! 👇",
        parse_mode=ParseMode.MARKDOWN,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 *Available Commands:*\n\n"
        "🎯 `/like <uid> [amount]`\n"
        "   → Send likes (default amount or custom)\n\n"
        "📥 `/addaccounts` *(admin)*\n"
        "   → Paste accounts list after this command\n\n"
        "📊 `/accounts` *(admin)*\n"
        "   → View stored account count\n\n"
        "🔌 `/addproxies` *(admin)*\n"
        "   → Add proxies via text or .txt file\n\n"
        "📋 `/proxies` *(admin)*\n"
        "   → View stored proxy count\n\n"
        "🔍 `/checkproxies` *(admin)*\n"
        "   → Test proxies and keep only fast working ones\n\n"
        "🔄 `/refresh` *(admin)*\n"
        "   → Refresh IND tokens now\n"
        "   → (auto-refreshes every 45 min)\n\n"
        "🏥 `/status`\n"
        "   → Bot uptime & account stats\n\n"
        "ℹ️ `/help`\n"
        "   → Show this menu\n\n"
        "━━━━━━━━━━━━━━━\n"
        "🖥️ *Server:* `IND` 🇮🇳\n"
        "💡 *Proxies:* Send .txt file or use `/addproxies`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def addaccounts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /addaccounts - admin adds pasted account list."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "🚫 *Access Denied!*\n\n"
            "Only the admin can add accounts.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    text = update.message.text or ""
    # Extract everything after /addaccounts
    parts = text.split(" ", 1)
    payload = parts[1].strip() if len(parts) > 1 else ""

    if not payload:
        await update.message.reply_text(
            "⚠️ *No account data provided!*\n\n"
            "Usage:\n"
            "`/addaccounts` followed by your pasted account list.\n\n"
            "📌 *Format required:*\n"
            "```\n"
            "• UID: 6246091984\n"
            "• PWD: POPPY_...\n"
            "• AccID: 16854671696 (optional)\n"
            "• Name: POPPY_⁶⁶⁹⁷ (optional)\n"
            "• Region: IND\n"
            "```",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Parse accounts
    accounts = parse_accounts(payload)
    if not accounts:
        await update.message.reply_text(
            "❌ *Could not parse any accounts!*\n\n"
            "Make sure the format matches:\n"
            "```\n"
            "• UID: 123456\n"
            "• PWD: PASSWORD\n"
            "• Region: IND\n"
            "```",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    added, skipped = add_accounts_to_storage(accounts)

    await update.message.reply_text(
        f"✅ *Accounts Added!*\n\n"
        f"➕ *Added:* {added}\n"
        f"⏭️ *Skipped (duplicates):* {skipped}\n"
        f"🌍 *Region:* IND 🇮🇳\n\n"
        f"Tokens auto-refresh every 45 min! 🚀",
        parse_mode=ParseMode.MARKDOWN,
    )


async def accounts_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /accounts - view stored account stats (IND only)."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "🚫 *Access Denied!* Only admin can view accounts.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    data = load_accounts()
    if not data:
        await update.message.reply_text(
            "📭 *No accounts stored yet.*\n\n"
            "Use /addaccounts to add accounts.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    total = sum(len(accounts) for accounts in data.values())
    lines = ["📊 *Stored Accounts:*\n"]
    lines.append(f"💾 *Total:* {total}")
    for region, accounts in data.items():
        lines.append(f"{emoji_for(region)} *{region}*: {len(accounts)} accounts")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def addproxies_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /addproxies - admin adds proxy list via text or .txt file."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "🚫 *Access Denied!*\n\n"
            "Only the admin can add proxies.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Check if a document was sent
    if update.message.document:
        # Download the .txt file
        document = update.message.document
        if not document.file_name.endswith('.txt'):
            await update.message.reply_text(
                "❌ *Invalid file format!*\n\n"
                "Please send a `.txt` file.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        
        try:
            file = await document.get_file()
            content = await file.download_as_bytearray()
            text = content.decode('utf-8', errors='ignore')
        except Exception as e:
            await update.message.reply_text(
                f"❌ *Failed to download file!*\n\n"
                f"Error: {str(e)}",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
    else:
        # Get text from command
        text = update.message.text or ""
        parts = text.split(" ", 1)
        text = parts[1].strip() if len(parts) > 1 else ""
        
        if not text:
            await update.message.reply_text(
                "⚠️ *No proxy data provided!*\n\n"
                "Usage:\n"
                "1. Send `/addproxies` followed by proxy list (one per line)\n"
                "2. OR send a `.txt` file with proxies\n\n"
                "📌 *Format:*\n"
                "```\n"
                "http://ip:port\n"
                "socks5://ip:port\n"
                "ip:port\n"
                "```\n"
                "One proxy per line. Scheme (http://) optional.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

    # Parse proxies (one per line)
    proxy_lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not proxy_lines:
        await update.message.reply_text(
            "❌ *No valid proxies found!*\n\n"
            "Make sure the file or text contains proxy addresses.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    added, skipped = add_proxies_to_storage(proxy_lines)

    await update.message.reply_text(
        f"✅ *Proxies Added!*\n\n"
        f"➕ *Added:* {added}\n"
        f"⏭️ *Skipped (duplicates):* {skipped}\n"
        f"📊 *Total Proxies:* {len(PROXY_LIST)}\n\n"
        f"Proxies will be used in /like commands! 🚀",
        parse_mode=ParseMode.MARKDOWN,
    )


async def proxies_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /proxies - view stored proxy count (admin only)."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "🚫 *Access Denied!* Only admin can view proxies.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    total = len(PROXY_LIST)
    if total == 0:
        await update.message.reply_text(
            "📭 *No proxies stored.*\n\n"
            "Use /addproxies to add proxies.\n\n"
            "You can send:\n"
            "• `/addproxies` followed by proxy list\n"
            "• OR a `.txt` file with proxies",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    lines = ["📊 *Stored Proxies:*\n"]
    lines.append(f"🔌 *Total:* {total} proxies")
    
    # Show sample of proxies (first 10)
    sample_size = min(10, total)
    lines.append(f"\n📋 *Sample (first {sample_size}):*")
    for i in range(sample_size):
        proxy = PROXY_LIST[i]
        lines.append(f"{i+1}. `{escape_md(proxy)}`")
    
    if total > 10:
        lines.append(f"\n... and {total - 10} more")
    
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def check_single_proxy(proxy, test_url="https://httpbin.org/ip", timeout=5):
    """Check if a proxy is working and fast. Returns (is_working, response_time_ms)."""
    start_time = datetime.now()
    try:
        timeout_obj = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=timeout_obj) as session:
            async with session.get(test_url, proxy=proxy, timeout=timeout) as response:
                if response.status == 200:
                    elapsed = (datetime.now() - start_time).total_seconds() * 1000
                    return True, int(elapsed)
    except Exception:
        pass
    return False, 0


async def checkproxies_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /checkproxies - test all proxies and keep only working fast ones."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "🚫 *Access Denied!* Only admin can check proxies.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if not PROXY_LIST:
        await update.message.reply_text(
            "📭 *No proxies to check!*\n\n"
            "Use /addproxies to add proxies first.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    total_proxies = len(PROXY_LIST)
    status_msg = await update.message.reply_text(
        f"🔍 *Checking Proxies...*\n\n"
        f"📊 Total: {total_proxies}\n"
        f"⏳ Testing... 0/{total_proxies}",
        parse_mode=ParseMode.MARKDOWN,
    )

    working_proxies = []
    working_count = 0
    failed_count = 0
    fast_threshold_ms = 3000  # Consider "fast" if < 3 seconds

    # Test each proxy
    for idx, proxy in enumerate(PROXY_LIST):
        is_working, response_time = await check_single_proxy(proxy)
        
        if is_working:
            working_proxies.append(proxy)
            working_count += 1
            
            # Save immediately after each successful proxy
            save_proxies(working_proxies)
            global PROXY_LIST
            PROXY_LIST = working_proxies.copy()
            
            # Update status every 10 proxies or on fast ones
            if working_count % 10 == 0 or response_time < fast_threshold_ms:
                try:
                    await status_msg.edit_text(
                        f"🔍 *Checking Proxies...*\n\n"
                        f"📊 Total: {total_proxies}\n"
                        f"✅ Working: {working_count}\n"
                        f"❌ Failed: {failed_count}\n"
                        f"⏳ Testing... {idx + 1}/{total_proxies}\n"
                        f"⚡ Last: {response_time}ms",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception:
                    pass  # Ignore edit errors (rate limits)
        else:
            failed_count += 1

    # Final update
    try:
        await status_msg.edit_text(
            f"✅ *Proxy Check Complete!*\n\n"
            f"📊 *Total Tested:* {total_proxies}\n"
            f"✅ *Working:* {working_count}\n"
            f"❌ *Failed:* {failed_count}\n"
            f"💾 *Saved:* {len(PROXY_LIST)} fast proxies\n\n"
            f"⚡ Only working proxies kept!",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass


async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle document uploads (for .txt proxy files)."""
    if not update.message.document:
        return
    
    # Check if user is admin
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "🚫 *Access Denied!*\n\n"
            "Only admin can upload proxy files.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    
    document = update.message.document
    if document.file_name.endswith('.txt'):
        # Treat as proxy file
        await addproxies_command(update, context)


async def refresh_ind_tokens(context: ContextTypes.DEFAULT_TYPE) -> tuple:
    """
    Generate fresh tokens for all IND accounts.
    Used by manual /refresh and the 45-min auto-refresh job.
    Returns (success, failed).
    """
    accounts = get_region_accounts("IND")
    if not accounts:
        return 0, 0

    success = 0
    failed = 0
    for acc in accounts:
        token_data, _ = await get_token_for_account(acc, force=True)
        if token_data:
            success += 1
        else:
            failed += 1
    return success, failed


async def auto_refresh_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Auto-refresh IND tokens every REFRESH_INTERVAL (default 45 min)."""
    logger = logging.getLogger(__name__)
    success, failed = await refresh_ind_tokens(context)
    logger.info(f"Auto-refresh complete: {success} success, {failed} failed")
    # Notify admins about the refresh result
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    f"🔄 *Auto Token Refresh* 🔄\n\n"
                    f"✅ *Success:* {success}\n"
                    f"❌ *Failed:* {failed}\n\n"
                    f"⏱️ Next refresh in {REFRESH_INTERVAL // 60} min"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")


async def refresh_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /refresh - generate tokens for IND accounts now."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "🚫 *Access Denied!* Only admin can refresh tokens.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    accounts = get_region_accounts("IND")
    if not accounts:
        await update.message.reply_text(
            "❌ No accounts stored for IND.\n\n"
            "Add accounts with /addaccounts first.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    msg = await update.message.reply_text(
        f"🔄 *Generating tokens for IND* 🇮🇳...\n\n"
        f"📦 Accounts: {len(accounts)}\n"
        f"⏳ Please wait...",
        parse_mode=ParseMode.MARKDOWN,
    )

    success, failed = await refresh_ind_tokens(context)

    if success > 0:
        await msg.edit_text(
            f"✅ *Token Generation Complete!*\n\n"
            f"✅ *Success:* {success}\n"
            f"❌ *Failed:* {failed}\n\n"
            f"🔥 Ready to send likes! Use /like <uid>",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await msg.edit_text(
            f"❌ *Token Generation Failed!*\n\n"
            f"All {len(accounts)} accounts failed.\n"
            f"Check credentials or JWT API status.",
            parse_mode=ParseMode.MARKDOWN,
        )


async def like_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /like <uid> [amount] - send likes to an IND profile."""
    if len(context.args) not in (1, 2):
        await update.message.reply_text(
            "⚠️ *Invalid format!*\n\n"
            "Usage: `/like <uid> [amount]`\n\n"
            "Examples:\n"
            "`/like 6246091984`  → default amount\n"
            "`/like 6246091984 5000`  → custom amount",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    server = "IND"
    target_uid = context.args[0]

    if not target_uid.isdigit():
        await update.message.reply_text(
            f"❌ *Invalid UID:* `{target_uid}`\n\n"
            "UID must be numeric.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Determine custom like amount (default = LIKES_PER_REQUEST)
    like_amount = LIKES_PER_REQUEST
    if len(context.args) == 2:
        amount_arg = context.args[1]
        if not amount_arg.isdigit() or int(amount_arg) <= 0:
            await update.message.reply_text(
                f"❌ *Invalid amount:* `{amount_arg}`\n\n"
                "Amount must be a positive number.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        like_amount = int(amount_arg)

    # Get tokens for IND accounts
    accounts = get_region_accounts(server)
    if not accounts:
        await update.message.reply_text(
            "❌ No accounts stored for IND.\n\n"
            f"Admin needs to /addaccounts first.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Ensure we have tokens (refresh if needed)
    tokens = []
    for acc in accounts:
        token_data, _ = await get_token_for_account(acc)
        if token_data:
            # The JWT bearer token is the correct credential for both
            # LikeProfile and GetPlayerPersonalShow (Garena expects it in
            # the Authorization: Bearer header). Using the access_token
            # (a hex string) instead triggers "401 token contains an
            # invalid number of segments".
            tokens.append(token_data["token"])
    if not tokens:
        await update.message.reply_text(
            "❌ No valid tokens for IND.\n\n"
            f"Admin needs to /refresh first.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    msg = await update.message.reply_text(
        f"⏳ *Sending {like_amount} likes...*\n\n"
        f"🎯 *Target:* `{target_uid}` {emoji_for(server)}\n"
        f"🖥️ *Server:* {server}\n"
        f"🔑 *Tokens:* {len(tokens)}",
        parse_mode=ParseMode.MARKDOWN,
    )

    # Get before like count
    encrypted_uid = enc(target_uid)
    before_info = await get_player_info(encrypted_uid, server, tokens[0])
    before_like = 0
    if before_info:
        try:
            json_before = json.loads(MessageToJson(before_info))
            before_like = int(json_before.get('AccountInfo', {}).get('Likes', 0))
        except Exception:
            before_like = 0

    # Send likes
    like_count = await send_likes(target_uid, server, tokens, like_amount)

    # Get after like count
    after_info = await get_player_info(encrypted_uid, server, tokens[0])
    after_like = 0
    nickname = "Unknown"
    player_uid = target_uid
    if after_info:
        try:
            json_after = json.loads(MessageToJson(after_info))
            after_like = int(json_after.get('AccountInfo', {}).get('Likes', 0))
            player_uid = str(json_after.get('AccountInfo', {}).get('UID', target_uid))
            nickname = str(json_after.get('AccountInfo', {}).get('PlayerNickname', 'Unknown'))
        except Exception:
            pass

    # like_count is the number of HTTP 200 responses from LikeProfile.
    # This is the primary success signal because the Garena API returns
    # HTTP 200 even when a like is rejected (cooldown, already-liked, etc.).
    # The before/after like diff is shown as a secondary diagnostic only.
    if like_count > 0:
        status_text = "✅ Success"
    else:
        status_text = "⚠️ No likes registered (cooldown or invalid tokens?)"

    await msg.edit_text(
        "✅ *Likes Delivered!* ✅\n\n"
        f"👤 *Player:* {escape_md(nickname)}\n"
        f"🆔 *UID:* {escape_md(player_uid)}\n\n"
        f"❤️ *Likes Sent:* {like_count}\n"
        f"📊 *Before:* {before_like} likes\n"
        f"📈 *After:* {after_like} likes\n\n"
        f"🔥 *Status:* {status_text}",
        parse_mode=ParseMode.MARKDOWN,
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status - show bot status & account stats (IND)."""
    data = load_accounts()
    total_accounts = sum(len(v) for v in data.values())
    token_data = load_bot_tokens()
    total_tokens = len(token_data)

    uptime_seconds = int((datetime.now() - start_time).total_seconds())
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    lines = [
        "🏥 *Bot Status:*\n",
        f"⏱️ *Uptime:* {hours}h {minutes}m {seconds}s",
        f"🤖 *Bot:* Online ✅",
        f"📦 *IND Accounts:* {total_accounts}",
        f"🔑 *Cached Tokens:* {total_tokens}",
        f"🔄 *Auto-Refresh:* every {REFRESH_INTERVAL // 60} min\n",
        "━━━━━━━━━━━━━━━",
        f"🇮🇳 *IND:* {total_accounts} accounts",
    ]

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🤔 Unknown command!\n\nUse /help to see available commands.",
        parse_mode=ParseMode.MARKDOWN,
    )


# ============================================================
# HELPERS — MISC
# ============================================================

start_time = datetime.now()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def emoji_for(region: str) -> str:
    return "🇮🇳"


def escape_md(text: str) -> str:
    """Escape Markdown special chars in user content (e.g. nicknames)."""
    if not text:
        return ""
    s = str(text)
    for ch in "\\*_`[#":
        s = s.replace(ch, "\\" + ch)
    return s

# ============================================================
# MAIN
# ============================================================

async def main() -> None:
    if not BOT_TOKEN:
        logging.error("BOT_TOKEN environment variable is not set!")
        return

    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=LOG_LEVEL,
    )
    logger = logging.getLogger(__name__)
    logger.info("Starting Free Fire Like Bot...")
    logger.info(f"DEBUG_MODE={DEBUG_MODE} (set DEBUG=true in Render env vars for verbose logs)")

    # concurrent_updates=True lets multiple updates be processed at once,
    # so a slow /like request never blocks other commands.
    application = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()

    # Schedule auto token refresh every 45 min (REFRESH_INTERVAL = 2700s)
    application.job_queue.run_repeating(
        auto_refresh_job,
        interval=REFRESH_INTERVAL,
        first=REFRESH_INTERVAL,  # first run after 45 min, then every 45 min
    )
    logger.info(f"Auto token refresh scheduled every {REFRESH_INTERVAL // 60} min")

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("like", like_command))
    application.add_handler(CommandHandler("addaccounts", addaccounts_command))
    application.add_handler(CommandHandler("accounts", accounts_command))
    application.add_handler(CommandHandler("addproxies", addproxies_command))
    application.add_handler(CommandHandler("proxies", proxies_command))
    application.add_handler(CommandHandler("checkproxies", checkproxies_command))
    application.add_handler(CommandHandler("refresh", refresh_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(MessageHandler(filters.Document.TXT, document_handler))
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    # Initialize proxies from storage or environment
    init_proxies()
    logger.info(f"Proxies loaded: {len(PROXY_LIST)} proxies")
    
    logger.info("Bot is running. Press Ctrl+C to stop.")

    # Start a tiny HTTP health server so Render marks the service as Live.
    # Render web services expect the process to bind to $PORT (health checks).
    port = int(os.getenv("PORT", "8000"))
    health_app = web.Application()

    async def health_check(request):
        return web.Response(text="OK", status=200)

    health_app.router.add_get("/", health_check)
    health_app.router.add_get("/health", health_check)
    health_runner = web.AppRunner(health_app)
    await health_runner.setup()
    health_site = web.TCPSite(health_runner, "0.0.0.0", port)
    await health_site.start()
    logger.info(f"Health server listening on port {port}")

    # Manual lifecycle (compatible with Python 3.14+ where asyncio has no
    # implicit event loop in the main thread; PTB 21.x has no Application.idle)
    await application.initialize()
    await application.start()
    await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)

    # Keep running until SIGINT/SIGTERM (works on Linux/Render; gracefully
    # falls back to sleeping forever on platforms without signal handlers)
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            pass
    await stop_event.wait()

    # Clean shutdown
    await application.updater.stop()
    await application.stop()
    await application.shutdown()
    await health_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
