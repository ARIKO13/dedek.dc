"""
ATMDS Bot Campus Module
- Fetch campus page (with cookies/auth)
- Extract text content with BeautifulSoup
- Use LLM (Cerebras) to identify pending assignments
- Save assignments to DB
- Notify user about unfinished tasks
"""
import re
import json
import httpx
from bs4 import BeautifulSoup
from datetime import datetime

from config import CEREBRAS_API_KEY, CEREBRAS_MODEL, USER_NAME
from database import (
    get_campus_config, update_campus_last_checked,
    upsert_assignment, get_pending_assignments,
    get_unnotified_pending_assignments, mark_assignment_notified,
)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Initialize Cerebras client (lazy)
_cerebras_client = None


def _get_cerebras():
    """Lazy init Cerebras client."""
    global _cerebras_client
    if _cerebras_client is None and CEREBRAS_API_KEY:
        try:
            from cerebras.cloud.sdk import Cerebras
            _cerebras_client = Cerebras(api_key=CEREBRAS_API_KEY)
        except Exception as e:
            print(f"❌ Failed to init Cerebras for campus: {e}")
    return _cerebras_client


def parse_cookie_string(cookie_str):
    """
    Parse cookie string into dict.
    Accepts formats:
      - "name1=value1; name2=value2"
      - "name1=value1,name2=value2"
    """
    if not cookie_str:
        return {}
    cookies = {}
    # Split by ; or , then by =
    parts = re.split(r'[;,]\s*', cookie_str)
    for part in parts:
        if '=' in part:
            name, _, value = part.partition('=')
            name = name.strip()
            value = value.strip()
            if name:
                cookies[name] = value
    return cookies


def fetch_campus_page(url, cookies_str=None, user_agent=None):
    """
    Fetch campus page content.
    Returns (status_code, text_content, html) or (None, None, None) on error.
    """
    if not url:
        return None, None, None

    headers = {
        "User-Agent": user_agent or DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "id,en-US;q=0.9,en;q=0.8",
    }

    cookies = parse_cookie_string(cookies_str)

    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=30.0,
            verify=False,  # Some campus sites have self-signed certs
        ) as client:
            response = client.get(url, headers=headers, cookies=cookies)
            return response.status_code, response.text, response.text
    except httpx.TimeoutException:
        return None, None, None
    except Exception as e:
        print(f"❌ Fetch error: {e}")
        return None, None, None


def extract_text_from_html(html):
    """
    Extract clean text from HTML for LLM processing.
    Removes scripts, styles, etc. Keeps meaningful content.
    """
    if not html:
        return ""

    soup = BeautifulSoup(html, "html.parser")

    # Remove non-content tags
    for tag in soup(["script", "style", "noscript", "iframe", "svg", "nav", "footer", "header"]):
        tag.decompose()

    # Get text
    text = soup.get_text(separator="\n", strip=True)

    # Clean up: remove excessive blank lines
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    text = "\n".join(lines)

    # Limit to ~8000 chars (avoid token explosion)
    if len(text) > 8000:
        text = text[:8000] + "\n... [truncated]"

    return text


def extract_links_from_html(html, base_url=None):
    """Extract assignment-like links from HTML."""
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"]
        if base_url and href.startswith("/"):
            href = base_url.rstrip("/") + href
        elif base_url and not href.startswith("http"):
            continue
        if text and len(text) > 3:
            links.append({"text": text, "url": href})
    return links[:30]  # limit


def identify_pending_assignments_with_llm(text_content, page_url=""):
    """
    Use LLM to identify pending/unfinished assignments from page text.
    Multi-provider (Cloudflare primary, OpenRouter fallback).
    Returns list of dicts: [{title, due_date, course, url}]
    """
    # Try LLM providers in order
    try:
        from llm import _get_available_providers, _call_provider
        providers = _get_available_providers()

        if not providers:
            print("❌ No LLM provider available, using regex")
            return identify_pending_assignments_regex(text_content)

        prompt = f"""Kamu adalah parser otomatis untuk halaman Moodle e-learning kampus.

Tugas: Identifikasi SEMUA tugas/assignment/quiz yang BELUM SELESAI dari halaman berikut.

Halaman (URL: {page_url}):

---BEGIN PAGE CONTENT---
{text_content}
---END PAGE CONTENT---

Aturan ekstraksi:
1. Identifikasi tugas/assignment/quiz yang masih PENDING (belum submitted, belum done)
2. Untuk setiap tugas, ekstrak:
   - title: judul tugas (wajib, jangan kosong)
   - due_date: deadline (format ISO yyyy-mm-dd HH:MM jika ada, null kalau gak ada)
   - course: nama mata kuliah (jika ada)
   - status: "pending" (default), "overdue" kalau deadline udah lewat
3. ABAIKAN tugas yang sudah selesai/submitted/completed
4. ABAIKAN pengumuman, materi kuliah, link non-tugas
5. Kalau halaman redirect/login page (bukan dashboard), jawab: []
6. Kalau gak ada tugas pending, jawab: []

WAJIB jawab dengan JSON array valid saja, tanpa teks/markdown lain:
[{{"title": "Tugas Bab 3", "due_date": "2024-12-15 23:59", "course": "Algoritma", "status": "pending"}}]"""

        messages = [
            {"role": "system", "content": "Kamu adalah parser JSON otomatis untuk halaman kampus. Selalu jawab dengan JSON array valid, tanpa teks lain."},
            {"role": "user", "content": prompt}
        ]

        last_error = None
        for provider in providers:
            try:
                result = _call_provider(provider, messages, max_tokens=1000, temperature=0.1)
                # Extract JSON from response
                json_match = re.search(r'\[.*\]', result, re.DOTALL)
                if json_match:
                    json_str = json_match.group(0)
                    assignments = json.loads(json_str)
                    valid = []
                    for a in assignments:
                        if isinstance(a, dict) and a.get("title"):
                            valid.append({
                                "title": str(a["title"])[:200],
                                "due_date": a.get("due_date"),
                                "course": a.get("course"),
                                "url": a.get("url"),
                                "status": a.get("status", "pending"),
                            })
                    print(f"✅ {provider['name']} parsed {len(valid)} assignments")
                    return valid
                # No JSON found, try next provider
                continue
            except Exception as e:
                last_error = str(e)[:100]
                print(f"⚠️  {provider['name']} failed for campus parse: {last_error}")
                continue

        # All providers failed, use regex fallback
        print(f"❌ All LLM providers failed, using regex. Last error: {last_error}")
        return identify_pending_assignments_regex(text_content)

    except Exception as e:
        print(f"❌ Campus LLM error: {e}")
        return identify_pending_assignments_regex(text_content)


def identify_pending_assignments_regex(text_content):
    """
    Fallback: simple regex-based parsing.
    Looks for patterns like "Deadline: ...", "Due: ...", "Tugas: ..."
    """
    if not text_content:
        return []

    assignments = []
    patterns = [
        r'(?:deadline|due|batas waktu|pengumpulan)[:\s]+([^\n]{5,80})',
        r'(?:tugas|assignment|quiz|task)[:\s]+([^\n]{5,80})',
    ]

    for pattern in patterns:
        matches = re.finditer(pattern, text_content, re.IGNORECASE)
        for m in matches:
            title = m.group(1).strip()
            if len(title) > 5 and not any(title == a["title"] for a in assignments):
                assignments.append({
                    "title": title,
                    "due_date": None,
                    "course": None,
                    "url": None,
                })

    return assignments[:10]  # limit


def check_campus_assignments():
    """
    Main entry: fetch campus page(s), extract assignments, save to DB.
    Untuk Moodle, fetch multiple halaman:
    - /my/ (dashboard)
    - /calendar/view.php (calendar dengan deadlines)
    Returns dict with summary.
    """
    config = get_campus_config()

    if not config["enabled"]:
        return {"success": False, "error": "Campus checking disabled"}

    if not config["url"]:
        return {"success": False, "error": "Campus URL not set"}

    print(f"📚 Checking campus: {config['url']}")

    # Determine pages to fetch based on URL pattern
    base_url = config["url"].rstrip("/")
    pages_to_fetch = [config["url"]]  # Always fetch the configured URL

    # If Moodle URL, also fetch calendar & timeline
    if "elearning" in config["url"] or "moodle" in config["url"].lower():
        # Extract base (https://elearning.pnj.ac.id)
        from urllib.parse import urlparse
        parsed = urlparse(config["url"])
        moodle_base = f"{parsed.scheme}://{parsed.netloc}"

        # Add timeline page (shows upcoming assignments)
        timeline_url = f"{moodle_base}/calendar/view.php?view=upcoming"
        if timeline_url not in pages_to_fetch:
            pages_to_fetch.append(timeline_url)

        # Add my/ page if not already the main URL
        if not config["url"].endswith("/my/"):
            my_url = f"{moodle_base}/my/"
            if my_url not in pages_to_fetch:
                pages_to_fetch.append(my_url)

    all_text_content = []
    fetched_pages = 0

    for page_url in pages_to_fetch[:3]:  # Limit to 3 pages max
        print(f"  📄 Fetching: {page_url}")
        status_code, html, _ = fetch_campus_page(
            page_url,
            cookies_str=config["cookies"],
            user_agent=config["user_agent"],
        )

        if status_code is None:
            print(f"  ⚠️  Failed to fetch {page_url} (network)")
            continue

        if status_code in (401, 403):
            return {
                "success": False,
                "error": f"Auth failed ({status_code}). Cookies expired atau belum diset.",
            }

        if status_code == 302 or "login" in str(html).lower()[:1000]:
            return {
                "success": False,
                "error": "Halaman redirect ke login. Cookies expired atau gak valid.",
            }

        if status_code != 200:
            print(f"  ⚠️  HTTP {status_code} for {page_url}")
            continue

        text = extract_text_from_html(html)
        if text and len(text) > 50:
            # Add separator for LLM context
            all_text_content.append(f"=== PAGE: {page_url} ===\n{text}")
            fetched_pages += 1

    if not all_text_content:
        return {"success": False, "error": "Tidak ada halaman yang berhasil di-fetch"}

    # Combine all page texts
    combined_text = "\n\n".join(all_text_content)

    # Limit total content to ~10000 chars (avoid token explosion)
    if len(combined_text) > 10000:
        combined_text = combined_text[:10000] + "\n... [truncated]"

    # Identify assignments via LLM
    assignments = identify_pending_assignments_with_llm(combined_text, config["url"])

    # Save to DB
    new_count = 0
    updated_count = 0
    overdue_count = 0

    for a in assignments:
        _, is_new = upsert_assignment(
            title=a["title"],
            due_date=a.get("due_date"),
            course=a.get("course"),
            url=a.get("url"),
        )
        if is_new:
            new_count += 1
        else:
            updated_count += 1

        if a.get("status") == "overdue":
            overdue_count += 1

    # Update last checked
    update_campus_last_checked()

    return {
        "success": True,
        "total_found": len(assignments),
        "new": new_count,
        "updated": updated_count,
        "overdue": overdue_count,
        "fetched_pages": fetched_pages,
        "url": config["url"],
    }


def format_assignment_summary(for_notification=False):
    """
    Format pending assignments into a readable message.
    If for_notification=True, only include unnotified ones (for daily push).
    """
    if for_notification:
        assignments = get_unnotified_pending_assignments()
    else:
        assignments = get_pending_assignments()

    if not assignments:
        return None

    lines = []
    for aid, title, due_date, course, url in assignments:
        line = f"📝 **{title}**"
        if course:
            line += f" | 📚 {course}"
        if due_date:
            try:
                # Try parse ISO
                if "T" in due_date:
                    dt = datetime.fromisoformat(due_date)
                    due_date = dt.strftime("%d/%m %H:%M")
                line += f" | ⏰ {due_date}"
            except Exception:
                line += f" | ⏰ {due_date}"
        line += f" (ID: #{aid})"
        lines.append(line)

    return "\n".join(lines)
