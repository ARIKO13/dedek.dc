"""
ATMDS Bot Database (SQLite)
- User profile (nama, preferensi)
- Tasks/reminders (untuk scheduler)
- Conversation history (context LLM)
- Long-term memories (fakta tentang user)
"""
import os
import sqlite3
from datetime import datetime
from config import USER_NAME, ASSISTANT_NAME

# DB path configurable via env var (untuk Fly.io volume / Docker / VPS)
# Default: atmds.db di directory yang sama dengan script (local dev)
# Fly.io/Docker: set DB_PATH=/app/data/atmds.db biar persistent di volume
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "atmds.db"))

# Pastikan directory-nya ada (untuk fresh deploy di Fly.io/Docker)
db_dir = os.path.dirname(DB_PATH)
if db_dir and not os.path.exists(db_dir):
    try:
        os.makedirs(db_dir, exist_ok=True)
    except Exception:
        pass  # Gak critical, akan fail saat init_db kalau gak bisa create


def get_conn():
    """Get a new SQLite connection (thread-safe)."""
    return sqlite3.connect(DB_PATH)


def init_db():
    """Initialize database tables."""
    conn = get_conn()
    c = conn.cursor()

    # Migrate: tambah kolom account_id kalau belum ada
    try:
        c.execute("PRAGMA table_info(provider_keys)")
        columns = [col[1] for col in c.fetchall()]
        if "account_id" not in columns:
            c.execute("ALTER TABLE provider_keys ADD COLUMN account_id TEXT")
            print("✅ Migration: added account_id column to provider_keys")
    except Exception as e:
        # Tabel belum ada, akan dibuat di bawah
        pass

    # User profile
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_profile (
            id INTEGER PRIMARY KEY,
            name TEXT,
            preferences TEXT,
            created_at TEXT
        )
    """)

    # Tasks/reminders
    c.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            description TEXT NOT NULL,
            remind_at TEXT NOT NULL,
            completed INTEGER DEFAULT 0,
            fired INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)

    # Conversation history (rolling window)
    c.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TEXT
        )
    """)

    # Long-term memory (facts about user)
    c.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            created_at TEXT
        )
    """)

    # Campus configuration (URL, cookies, etc.)
    c.execute("""
        CREATE TABLE IF NOT EXISTS campus_config (
            id INTEGER PRIMARY KEY,
            url TEXT,
            cookies TEXT,
            user_agent TEXT,
            enabled INTEGER DEFAULT 0,
            last_checked TEXT,
            created_at TEXT
        )
    """)

    # Campus assignments
    c.execute("""
        CREATE TABLE IF NOT EXISTS campus_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            due_date TEXT,
            course TEXT,
            url TEXT,
            status TEXT DEFAULT 'pending',
            notified INTEGER DEFAULT 0,
            first_seen TEXT,
            updated_at TEXT
        )
    """)

    # Provider API keys (set via Discord command, override .env)
    c.execute("""
        CREATE TABLE IF NOT EXISTS provider_keys (
            provider TEXT PRIMARY KEY,
            api_key TEXT NOT NULL,
            model TEXT,
            account_id TEXT,
            status TEXT DEFAULT 'untested',
            last_tested TEXT,
            error_message TEXT,
            added_at TEXT
        )
    """)

    # Insert default user profile if not exists
    c.execute("SELECT COUNT(*) FROM user_profile WHERE id = 1")
    if c.fetchone()[0] == 0:
        c.execute(
            "INSERT INTO user_profile (id, name, preferences, created_at) VALUES (?, ?, ?, ?)",
            (1, USER_NAME, "{}", datetime.now().isoformat())
        )

    # Insert default campus config if not exists
    c.execute("SELECT COUNT(*) FROM campus_config WHERE id = 1")
    if c.fetchone()[0] == 0:
        c.execute(
            "INSERT INTO campus_config (id, url, cookies, user_agent, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (1, "", "", "", 0, datetime.now().isoformat())
        )

    conn.commit()
    conn.close()
    print(f"✅ Database initialized at {DB_PATH}")


def save_message(role, content):
    """Save a message to conversation history. Auto-trim to last 50."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO conversations (role, content, timestamp) VALUES (?, ?, ?)",
        (role, content, datetime.now().isoformat())
    )
    # Keep only last 50 messages
    c.execute("""
        DELETE FROM conversations
        WHERE id NOT IN (
            SELECT id FROM conversations ORDER BY id DESC LIMIT 50
        )
    """)
    conn.commit()
    conn.close()


def get_recent_messages(limit=10):
    """Get recent conversation messages (oldest first)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT role, content FROM conversations ORDER BY id DESC LIMIT ?",
        (limit,)
    )
    rows = c.fetchall()
    conn.close()
    return list(reversed(rows))


def clear_conversations():
    """Clear conversation history (keep memories)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM conversations")
    conn.commit()
    conn.close()


def add_task(description, remind_at):
    """Add a new task/reminder. remind_at should be ISO format string."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO tasks (description, remind_at, created_at) VALUES (?, ?, ?)",
        (description, remind_at, datetime.now().isoformat())
    )
    task_id = c.lastrowid
    conn.commit()
    conn.close()
    return task_id


def get_pending_tasks():
    """Get all incomplete and unfired tasks."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id, description, remind_at FROM tasks WHERE completed = 0 AND fired = 0")
    rows = c.fetchall()
    conn.close()
    return rows


def mark_task_fired(task_id):
    """Mark a task as fired (notification sent)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE tasks SET fired = 1 WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()


def mark_task_done(task_id):
    """Mark a task as completed."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE tasks SET completed = 1 WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()


def list_all_tasks():
    """List all tasks (for status command)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT id, description, remind_at, completed, fired FROM tasks ORDER BY id DESC LIMIT 10"
    )
    rows = c.fetchall()
    conn.close()
    return rows


def add_memory(content):
    """Save a long-term memory (fact about user)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO memories (content, created_at) VALUES (?, ?)",
        (content, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def get_memories(limit=10):
    """Get long-term memories."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT content FROM memories ORDER BY id DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]


def update_user_name(new_name):
    """Update user's preferred name."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE user_profile SET name = ? WHERE id = 1", (new_name,))
    conn.commit()
    conn.close()


def get_user_name():
    """Get user's name."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT name FROM user_profile WHERE id = 1")
    row = c.fetchone()
    conn.close()
    return row[0] if row else USER_NAME


# =========== CAMPUS ===========

def get_campus_config():
    """Get campus configuration."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT url, cookies, user_agent, enabled, last_checked FROM campus_config WHERE id = 1")
    row = c.fetchone()
    conn.close()
    if row:
        return {
            "url": row[0] or "",
            "cookies": row[1] or "",
            "user_agent": row[2] or "",
            "enabled": bool(row[3]),
            "last_checked": row[4] or "",
        }
    return {"url": "", "cookies": "", "user_agent": "", "enabled": False, "last_checked": ""}


def set_campus_url(url):
    """Set campus URL and enable checking."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE campus_config SET url = ?, enabled = 1 WHERE id = 1", (url,))
    conn.commit()
    conn.close()


def set_campus_cookies(cookies):
    """Set campus cookies (for authenticated access)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE campus_config SET cookies = ? WHERE id = 1", (cookies,))
    conn.commit()
    conn.close()


def set_campus_user_agent(user_agent):
    """Set custom user agent for campus requests."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE campus_config SET user_agent = ? WHERE id = 1", (user_agent,))
    conn.commit()
    conn.close()


def set_campus_enabled(enabled):
    """Enable/disable campus checking."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE campus_config SET enabled = ? WHERE id = 1", (1 if enabled else 0,))
    conn.commit()
    conn.close()


def update_campus_last_checked():
    """Update last checked timestamp."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE campus_config SET last_checked = ? WHERE id = 1", (datetime.now().isoformat(),))
    conn.commit()
    conn.close()


def upsert_assignment(title, due_date=None, course=None, url=None):
    """Insert or update an assignment. Returns (id, is_new)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id, status, notified FROM campus_assignments WHERE title = ?", (title,))
    row = c.fetchone()
    now = datetime.now().isoformat()
    if row:
        # Update existing
        c.execute(
            "UPDATE campus_assignments SET due_date = ?, course = ?, url = ?, updated_at = ? WHERE id = ?",
            (due_date, course, url, now, row[0])
        )
        conn.commit()
        conn.close()
        return row[0], False
    else:
        # Insert new
        c.execute(
            "INSERT INTO campus_assignments (title, due_date, course, url, status, notified, first_seen, updated_at) VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)",
            (title, due_date, course, url, now, now)
        )
        new_id = c.lastrowid
        conn.commit()
        conn.close()
        return new_id, True


def mark_assignment_done(assignment_id):
    """Mark an assignment as done."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE campus_assignments SET status = 'done' WHERE id = ?", (assignment_id,))
    conn.commit()
    conn.close()


def mark_assignment_notified(assignment_id):
    """Mark an assignment as notified (reminder sent)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE campus_assignments SET notified = 1 WHERE id = ?", (assignment_id,))
    conn.commit()
    conn.close()


def get_pending_assignments():
    """Get all pending assignments (not done)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT id, title, due_date, course, url FROM campus_assignments WHERE status = 'pending' ORDER BY due_date ASC"
    )
    rows = c.fetchall()
    conn.close()
    return rows


def get_unnotified_pending_assignments():
    """Get pending assignments that haven't been notified yet."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT id, title, due_date, course, url FROM campus_assignments WHERE status = 'pending' AND notified = 0 ORDER BY due_date ASC"
    )
    rows = c.fetchall()
    conn.close()
    return rows


def list_all_assignments():
    """List all assignments for status command."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT id, title, due_date, course, status, notified FROM campus_assignments ORDER BY id DESC LIMIT 20"
    )
    rows = c.fetchall()
    conn.close()
    return rows


# =========== PROVIDER KEYS ===========

# Provider definitions (name, default model, base_url, supports OpenAI SDK)
PROVIDER_DEFS = {
    "openrouter": {
        "display_name": "OpenRouter",
        "default_model": "qwen/qwen-2.5-72b-instruct",
        "base_url": "https://openrouter.ai/api/v1",
        "signup_url": "https://openrouter.ai/keys",
        "supports_openai_sdk": True,
        "extra_headers": {
            "HTTP-Referer": "https://github.com/atmds-bot",
            "X-Title": "ATMDS Bot",
        },
    },
    "zai": {
        "display_name": "Z.ai (GLM-4.6)",
        "default_model": "glm-4.6",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "signup_url": "https://z.ai/",
        "supports_openai_sdk": True,
        "extra_headers": {},
    },
    "groq": {
        "display_name": "Groq",
        "default_model": "llama-3.3-70b-versatile",
        "base_url": "https://api.groq.com/openai/v1",
        "signup_url": "https://console.groq.com/keys",
        "supports_openai_sdk": True,
        "extra_headers": {},
    },
    "together": {
        "display_name": "Together AI",
        "default_model": "Qwen/Qwen2.5-72B-Instruct-Turbo",
        "base_url": "https://api.together.xyz/v1",
        "signup_url": "https://api.together.xyz/",
        "supports_openai_sdk": True,
        "extra_headers": {},
    },
    "cerebras": {
        "display_name": "Cerebras",
        "default_model": "qwen-3-32b",
        "base_url": "https://api.cerebras.ai/v1",
        "signup_url": "https://inference.cerebras.ai/apiKeys",
        "supports_openai_sdk": True,
        "extra_headers": {},
    },
    "deepseek": {
        "display_name": "DeepSeek",
        "default_model": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "signup_url": "https://platform.deepseek.com/",
        "supports_openai_sdk": True,
        "extra_headers": {},
    },
    "openai": {
        "display_name": "OpenAI",
        "default_model": "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
        "signup_url": "https://platform.openai.com/api-keys",
        "supports_openai_sdk": True,
        "extra_headers": {},
    },
    "cloudflare": {
        "display_name": "Cloudflare Workers AI",
        "default_model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        "base_url": "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
        "signup_url": "https://dash.cloudflare.com/?to=/:account/ai",
        "supports_openai_sdk": True,
        "extra_headers": {},
        "requires_account_id": True,  # Cloudflare butuh account_id selain api_key
    },
}


def add_provider_key(provider, api_key, model=None, account_id=None):
    """Add or update a provider API key (set via Discord command)."""
    if provider not in PROVIDER_DEFS:
        return False, f"Provider '{provider}' tidak dikenal. Yang tersedia: {', '.join(PROVIDER_DEFS.keys())}"

    if not model:
        model = PROVIDER_DEFS[provider]["default_model"]

    # Validate required account_id for Cloudflare
    defn = PROVIDER_DEFS[provider]
    if defn.get("requires_account_id") and not account_id:
        return False, f"Provider '{provider}' butuh account_id. Pakai: `!apikey add {provider} <key> <model> --account <account_id>` atau set via env CLOUDFLARE_ACCOUNT_ID"

    conn = get_conn()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute(
        """INSERT INTO provider_keys (provider, api_key, model, account_id, status, last_tested, error_message, added_at)
           VALUES (?, ?, ?, ?, 'untested', NULL, NULL, ?)
           ON CONFLICT(provider) DO UPDATE SET
             api_key=excluded.api_key,
             model=excluded.model,
             account_id=excluded.account_id,
             status='untested',
             last_tested=NULL,
             error_message=NULL,
             added_at=excluded.added_at""",
        (provider, api_key, model, account_id, now)
    )
    conn.commit()
    conn.close()
    extra = f" (account: {account_id[:8]}...)" if account_id else ""
    return True, f"✅ Provider '{provider}' berhasil ditambahkan dengan model '{model}'{extra}"


def remove_provider_key(provider):
    """Remove a provider API key."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM provider_keys WHERE provider = ?", (provider,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    return deleted > 0


def get_provider_key(provider):
    """Get a single provider's API key info. Returns dict or None."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT provider, api_key, model, account_id, status, last_tested, error_message FROM provider_keys WHERE provider = ?",
        (provider,)
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "provider": row[0],
        "api_key": row[1],
        "model": row[2],
        "account_id": row[3] or "",
        "status": row[4],
        "last_tested": row[5] or "",
        "error_message": row[6] or "",
    }


def get_all_provider_keys():
    """Get all provider keys from DB."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT provider, api_key, model, account_id, status, last_tested, error_message FROM provider_keys ORDER BY added_at DESC")
    rows = c.fetchall()
    conn.close()
    return [
        {
            "provider": r[0],
            "api_key": r[1],
            "model": r[2],
            "account_id": r[3] or "",
            "status": r[4],
            "last_tested": r[5] or "",
            "error_message": r[6] or "",
        }
        for r in rows
    ]


def update_provider_status(provider, status, error_message=None):
    """Update a provider's test status."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE provider_keys SET status = ?, error_message = ?, last_tested = ? WHERE provider = ?",
        (status, error_message, datetime.now().isoformat(), provider)
    )
    conn.commit()
    conn.close()
