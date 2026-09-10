"""
ATMDS Provider Tester
Test koneksi LLM provider untuk deteksi:
- Working (key valid, region OK)
- Auth failed (key invalid)
- Blocked (Cloudflare/region restriction)
- Rate limited
"""
import httpx
from database import PROVIDER_DEFS, update_provider_status


def test_provider(provider_name, api_key, model=None):
    """
    Test a single LLM provider.
    Returns dict: {status, message, response_text?}
    Status: 'working' | 'auth_failed' | 'blocked' | 'rate_limited' | 'failed' | 'unknown_provider'
    """
    if provider_name not in PROVIDER_DEFS:
        return {
            "status": "unknown_provider",
            "message": f"Provider '{provider_name}' tidak dikenal",
        }

    defn = PROVIDER_DEFS[provider_name]
    if not model:
        model = defn["default_model"]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        **defn.get("extra_headers", {}),
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": "Test connection. Reply with: OK"}
        ],
        "max_tokens": 10,
        "temperature": 0.1,
    }

    try:
        # Use httpx for direct test (better error visibility than openai SDK)
        with httpx.Client(timeout=20.0) as client:
            response = client.post(
                f"{defn['base_url']}/chat/completions",
                headers=headers,
                json=payload,
            )

        if response.status_code == 200:
            try:
                data = response.json()
                text = data["choices"][0]["message"]["content"].strip()
                update_provider_status(provider_name, "working", None)
                return {
                    "status": "working",
                    "message": f"Berhasil! Response: {text[:60]}",
                    "response": text,
                }
            except Exception:
                return {
                    "status": "working",
                    "message": "Berhasil connect (response format unexpected)",
                }

        # Non-200 responses
        body_text = response.text[:500]
        content_type = response.headers.get("content-type", "")

        # Cloudflare block
        if "cloudflare" in body_text.lower() or "<!doctype" in body_text.lower() or "<html" in body_text.lower():
            update_provider_status(provider_name, "blocked", "Cloudflare blocked")
            return {
                "status": "blocked",
                "message": f"Di-block Cloudflare (HTTP {response.status_code}). IP region tidak di-support.",
            }

        # Auth issues
        if response.status_code in (401, 403):
            err_msg = ""
            try:
                err_data = response.json()
                err_msg = err_data.get("error", {}).get("message", "")
            except Exception:
                err_msg = body_text[:100]

            # Check if it's region block disguised as 403
            if "location" in err_msg.lower() or "region" in err_msg.lower():
                update_provider_status(provider_name, "blocked", "Region restriction")
                return {
                    "status": "blocked",
                    "message": f"Region/lokasi tidak di-support: {err_msg[:80]}",
                }

            update_provider_status(provider_name, "auth_failed", f"HTTP {response.status_code}: {err_msg[:80]}")
            return {
                "status": "auth_failed",
                "message": f"Auth gagal (HTTP {response.status_code}): {err_msg[:80]}",
            }

        # Rate limited
        if response.status_code == 429:
            update_provider_status(provider_name, "rate_limited", "Rate limited")
            return {
                "status": "rate_limited",
                "message": "Kena rate limit, coba lagi sebentar",
            }

        # Other errors
        update_provider_status(provider_name, "failed", f"HTTP {response.status_code}")
        return {
            "status": "failed",
            "message": f"HTTP {response.status_code}: {body_text[:100]}",
        }

    except httpx.TimeoutException:
        update_provider_status(provider_name, "failed", "Timeout")
        return {"status": "failed", "message": "Timeout (server lambat)"}
    except httpx.ConnectError as e:
        update_provider_status(provider_name, "failed", f"Connect error: {str(e)[:80]}")
        return {"status": "failed", "message": f"Gagal connect: {str(e)[:80]}"}
    except Exception as e:
        err_msg = str(e)[:200]
        update_provider_status(provider_name, "failed", err_msg)
        return {"status": "failed", "message": f"Error: {err_msg}"}


def test_all_providers():
    """Test all providers in DB. Returns list of {provider, status, message}."""
    from database import get_all_provider_keys
    results = []
    for p in get_all_provider_keys():
        result = test_provider(p["provider"], p["api_key"], p["model"])
        results.append({
            "provider": p["provider"],
            "model": p["model"],
            "status": result["status"],
            "message": result["message"],
        })
    return results


STATUS_EMOJI = {
    "working": "✅",
    "blocked": "🚫",
    "auth_failed": "🔑❌",
    "rate_limited": "⏰",
    "failed": "❌",
    "untested": "⏳",
    "unknown_provider": "❓",
}
