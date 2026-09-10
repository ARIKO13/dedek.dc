"""
ATMDS Bot LLM Client (Multi-Provider with Fallback)
- Supports: Cerebras, Z.ai (GLM-4.6), Groq, OpenRouter, Together
- Auto-fallback if primary provider fails
- Rest mode: tidak panggil LLM saat idle
- Hanya panggil LLM untuk chat response
- Reminder pakai template (hemat token)
"""
import os
import time
import random
from config import (
    CEREBRAS_API_KEY, CEREBRAS_MODEL,
    USER_NAME, REST_MODE_TIMEOUT, USE_LLM_FOR_REMINDERS,
    MEAL_REMINDER_TEMPLATES, TASK_REMINDER_TEMPLATES, SYSTEM_PROMPT,
)
from database import get_recent_messages, get_memories, get_all_provider_keys, PROVIDER_DEFS

# Load providers from env
ZAI_API_KEY = os.getenv("ZAI_API_KEY", "")
ZAI_MODEL = os.getenv("ZAI_MODEL", "glm-4.6")
ZAI_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "qwen/qwen-2.5-72b-instruct")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY", "")
TOGETHER_MODEL = os.getenv("TOGETHER_MODEL", "Qwen/Qwen2.5-72B-Instruct-Turbo")
TOGETHER_BASE_URL = "https://api.together.xyz/v1"

CLOUDFLARE_API_KEY = os.getenv("CLOUDFLARE_API_KEY", "")
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
CLOUDFLARE_MODEL = os.getenv("CLOUDFLARE_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")
CLOUDFLARE_BASE_URL = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1" if CLOUDFLARE_ACCOUNT_ID else "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"

# Rest mode tracking
_last_user_message_time = time.time()


def update_user_activity():
    """Call this when user sends a message - resets rest mode timer."""
    global _last_user_message_time
    _last_user_message_time = time.time()


def is_rest_mode():
    """Check if bot is in rest mode."""
    idle_seconds = time.time() - _last_user_message_time
    return idle_seconds > (REST_MODE_TIMEOUT * 60)


def get_idle_minutes():
    """Get minutes since last user message."""
    return int((time.time() - _last_user_message_time) / 60)


def _get_available_providers():
    """Get list of configured providers in priority order.
    Priority: Cloudflare (best, no region block) > DB providers (working first) > env providers > Cerebras last
    """
    providers = []
    seen_keys = set()

    # 0. Cloudflare from env (highest priority - no region block)
    if CLOUDFLARE_API_KEY and CLOUDFLARE_ACCOUNT_ID:
        cf_base_url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1"
        providers.append({
            "name": "Cloudflare Workers AI (env)",
            "api_key": CLOUDFLARE_API_KEY,
            "model": CLOUDFLARE_MODEL,
            "base_url": cf_base_url,
            "extra_headers": {},
        })
        seen_keys.add(CLOUDFLARE_API_KEY)

    # 1. Read from DB (set via Discord command) - tested working first
    db_providers = get_all_provider_keys()
    # Sort: working providers first, then untested, then failed
    db_providers_sorted = sorted(
        db_providers,
        key=lambda p: {"working": 0, "untested": 1, "failed": 2, "blocked": 2}.get(p["status"], 3)
    )

    for p in db_providers_sorted:
        provider_name = p["provider"]
        if provider_name not in PROVIDER_DEFS:
            continue
        defn = PROVIDER_DEFS[provider_name]
        # Skip blocked providers (auto-block by Cloudflare detection)
        if p["status"] == "blocked":
            continue
        if p["api_key"] in seen_keys:
            continue
        seen_keys.add(p["api_key"])

        # Handle account_id substitution for Cloudflare
        base_url = defn["base_url"]
        if defn.get("requires_account_id") and p.get("account_id"):
            base_url = base_url.replace("{account_id}", p["account_id"])

        providers.append({
            "name": f"{defn['display_name']} (DB)",
            "api_key": p["api_key"],
            "model": p["model"],
            "base_url": base_url,
            "extra_headers": defn.get("extra_headers", {}),
        })

    # 2. Fallback to env providers (if not already in DB)
    if ZAI_API_KEY and ZAI_API_KEY not in seen_keys:
        providers.append({
            "name": "Z.ai (GLM-4.6) (env)",
            "api_key": ZAI_API_KEY,
            "model": ZAI_MODEL,
            "base_url": ZAI_BASE_URL,
            "extra_headers": {},
        })
        seen_keys.add(ZAI_API_KEY)

    if GROQ_API_KEY and GROQ_API_KEY not in seen_keys:
        providers.append({
            "name": "Groq (env)",
            "api_key": GROQ_API_KEY,
            "model": GROQ_MODEL,
            "base_url": GROQ_BASE_URL,
            "extra_headers": {},
        })
        seen_keys.add(GROQ_API_KEY)

    if TOGETHER_API_KEY and TOGETHER_API_KEY not in seen_keys:
        providers.append({
            "name": "Together (env)",
            "api_key": TOGETHER_API_KEY,
            "model": TOGETHER_MODEL,
            "base_url": TOGETHER_BASE_URL,
            "extra_headers": {},
        })
        seen_keys.add(TOGETHER_API_KEY)

    if OPENROUTER_API_KEY and OPENROUTER_API_KEY not in seen_keys:
        providers.append({
            "name": "OpenRouter (env)",
            "api_key": OPENROUTER_API_KEY,
            "model": OPENROUTER_MODEL,
            "base_url": OPENROUTER_BASE_URL,
            "extra_headers": {
                "HTTP-Referer": "https://github.com/atmds-bot",
                "X-Title": "ATMDS Bot",
            },
        })
        seen_keys.add(OPENROUTER_API_KEY)

    if CEREBRAS_API_KEY and CEREBRAS_API_KEY not in seen_keys:
        providers.append({
            "name": "Cerebras (env)",
            "api_key": CEREBRAS_API_KEY,
            "model": CEREBRAS_MODEL,
            "base_url": "https://api.cerebras.ai/v1",
            "extra_headers": {},
        })

    return providers


def _call_provider(provider, messages, max_tokens=300, temperature=0.7):
    """Call a single LLM provider. Returns response text or raises exception."""
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=provider["api_key"],
            base_url=provider["base_url"],
            default_headers=provider.get("extra_headers", {}),
        )
        response = client.chat.completions.create(
            model=provider["model"],
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        err_str = str(e)

        # Auto-mark provider as blocked if Cloudflare HTML response
        if "<!DOCTYPE" in err_str or "Cloudflare" in err_str or "Attention Required" in err_str:
            try:
                from database import update_provider_status
                # Find provider key in DB by api_key
                db_providers = get_all_provider_keys()
                for p in db_providers:
                    if p["api_key"] == provider["api_key"]:
                        update_provider_status(
                            p["provider"],
                            "blocked",
                            "Cloudflare blocked (IP region restriction)"
                        )
                        print(f"🚫 Provider {p['provider']} auto-marked as BLOCKED (Cloudflare)")
                        break
            except Exception:
                pass

        # Re-raise with provider info
        raise RuntimeError(f"{provider['name']} error: {type(e).__name__}: {err_str[:200]}")


def _detect_error_type(error_str):
    """Classify error to give friendly message."""
    err_lower = error_str.lower()

    if "401" in error_str or "unauthorized" in err_lower or "permissiondenied" in err_lower:
        return "auth"
    if "cloudflare" in err_lower or "<!doctype" in err_lower or "<html" in err_lower:
        return "cloudflare"
    if "rate limit" in err_lower or "429" in error_str:
        return "rate_limit"
    if "model" in err_lower and ("not found" in err_lower or "does not exist" in err_lower):
        return "model_not_found"
    if "timeout" in err_lower or "timed out" in err_lower:
        return "timeout"
    if "connect" in err_lower or "network" in err_lower:
        return "network"
    return "unknown"


def generate_response(user_message):
    """
    Generate LLM response with conversation context.
    Tries each provider in order until one works.
    ONLY called when user sends a message (saves tokens).
    """
    update_user_activity()

    providers = _get_available_providers()
    if not providers:
        return (
            f"Sayang, belum ada LLM provider yang aktif nih 🌸\n"
            f"Salah satu dari API key ini harus diisi di file `.env`:\n"
            f"• `ZAI_API_KEY` (Z.ai - recommended, gratis, multi-bahasa)\n"
            f"• `CEREBRAS_API_KEY` (Cerebras - cepat, tapi IP server ini diblock)\n"
            f"• `GROQ_API_KEY` (Groq - cepat, free tier)\n"
            f"• `OPENROUTER_API_KEY` (OpenRouter - banyak model)\n"
            f"Daftar akun di https://z.ai/ untuk dapat API key gratis ya 🤍"
        )

    # Get conversation context
    recent = get_recent_messages(limit=10)
    memories = get_memories(limit=5)

    # Build messages
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Add memories as system context
    if memories:
        memory_text = "Hal yang pernah kamu ceritakan:\n" + "\n".join(f"- {m}" for m in memories)
        messages.append({"role": "system", "content": memory_text})

    # Add recent conversation history
    for role, content in recent:
        messages.append({"role": role, "content": content})

    # Add current message
    messages.append({"role": "user", "content": user_message})

    # Try each provider in order
    last_error = None
    for provider in providers:
        try:
            print(f"🧠 Trying {provider['name']}...")
            response = _call_provider(provider, messages, max_tokens=300, temperature=0.7)
            if last_error:
                print(f"✅ {provider['name']} succeeded after previous failures")
            else:
                print(f"✅ {provider['name']} responded")
            return response
        except Exception as e:
            last_error = str(e)
            print(f"❌ {provider['name']} failed: {last_error[:150]}")
            continue

    # All providers failed - give friendly message based on error type
    error_type = _detect_error_type(last_error)
    if error_type == "cloudflare":
        return (
            f"Maaf sayang, server LLM lagi diblock Cloudflare nih 🌸\n"
            f"Kemungkinan IP server ini diblokir sementara. Coba lagi nanti ya, "
            f"atau mending pakai provider alternatif. Kasih tau aku kalau mau switch ke Z.ai/GLM 🤍"
        )
    elif error_type == "auth":
        return (
            f"Sayang, ada masalah autentikasi API key nih 🌸 Coba cek di file `.env` "
            f"apakah API key-nya valid dan belum expired. Mungkin perlu regenerate key baru ya 🤍"
        )
    elif error_type == "rate_limit":
        return f"Bentar ya sayang, lagi kena rate limit 🌸 Coba lagi beberapa detik lagi ya 🤍"
    elif error_type == "timeout":
        return f"Maaf sayang, server LLM lama banget responnya 🌸 Coba lagi sebentar ya 🤍"
    elif error_type == "network":
        return f"Ada gangguan jaringan nih sayang 🌸 Coba lagi sebentar ya 🤍"
    else:
        short_err = last_error[:100].replace("\n", " ")
        return f"Maaf sayang, ada gangguan teknis nih 🌸 ({short_err}...) Coba lagi ya 🤍"


def generate_meal_reminder(meal_time):
    """Generate meal reminder. Default: template (hemat token)."""
    # Only use LLM if explicitly enabled AND providers are available
    if USE_LLM_FOR_REMINDERS:
        try:
            msg = generate_response(f"Sekarang jam {meal_time}. Ingatkan aku untuk makan, lembut & singkat.")
            if msg and len(msg) < 500 and "Maaf sayang" not in msg:
                return msg
        except Exception:
            pass
    return random.choice(MEAL_REMINDER_TEMPLATES)


def generate_task_reminder(task_description):
    """Generate task reminder. Default: template (hemat token)."""
    if USE_LLM_FOR_REMINDERS:
        try:
            msg = generate_response(f"Ingetin aku untuk: {task_description}. Lembut & singkat.")
            if msg and len(msg) < 500 and "Maaf sayang" not in msg:
                return msg
        except Exception:
            pass
    template = random.choice(TASK_REMINDER_TEMPLATES)
    return template.format(task=task_description)
