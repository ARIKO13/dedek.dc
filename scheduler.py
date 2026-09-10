"""
ATMDS Bot Scheduler
- Notifikasi makan terjadwal (default: 12:00, 18:00, 21:00)
- Cek task setiap menit, kirim notif kalau sudah due
- Pakai APScheduler (AsyncIO)
"""
import re
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import MEAL_TIMES, DISCORD_USER_ID, CAMPUS_CHECK_ENABLED, CAMPUS_CHECK_TIME, CAMPUS_URL, CAMPUS_COOKIES, USER_NAME
from database import get_pending_tasks, mark_task_fired, set_campus_url, set_campus_cookies
from llm import generate_meal_reminder, generate_task_reminder
import campus

scheduler = AsyncIOScheduler()
_discord_bot = None


def set_bot(bot):
    """Inject Discord bot instance."""
    global _discord_bot
    _discord_bot = bot


async def _send_dm(message):
    """Send DM to the configured user."""
    if not _discord_bot:
        print("⚠️ Bot belum diset, skip notif")
        return False

    user = _discord_bot.get_user(DISCORD_USER_ID)
    if user is None:
        # Try fetch_user as fallback
        try:
            user = await _discord_bot.fetch_user(DISCORD_USER_ID)
        except Exception as e:
            print(f"❌ Tidak bisa fetch user {DISCORD_USER_ID}: {e}")
            return False

    if user:
        try:
            # Auto-split long messages (Discord limit ~2000 chars)
            text = message
            chunks = []
            while len(text) > 1900:
                # Try to split at newline near the limit
                split_at = -1
                for i in range(1900, 1700, -1):
                    if i <= len(text) and text[i-1] == "\n":
                        split_at = i
                        break
                if split_at == -1:
                    for i in range(1900, 1700, -1):
                        if i <= len(text) and text[i-1] == " ":
                            split_at = i
                            break
                if split_at == -1:
                    split_at = 1900
                chunks.append(text[:split_at].rstrip())
                text = text[split_at:].lstrip()
            if text:
                chunks.append(text)

            for chunk in chunks:
                await user.send(chunk)
            print(f"📤 Notif terkirim ({len(chunks)} chunk): {message[:80]}...")
            return True
        except Exception as e:
            print(f"❌ Gagal kirim DM: {e}")
            return False
    else:
        print(f"❌ User {DISCORD_USER_ID} tidak ditemukan")
        return False


async def send_meal_reminder(meal_time):
    """Scheduled meal reminder job."""
    print(f"🍽️ Triggered meal reminder at {meal_time}")
    message = generate_meal_reminder(meal_time)
    await _send_dm(message)


async def check_tasks():
    """Check pending tasks every minute, fire reminders if due."""
    if not _discord_bot:
        return

    now = datetime.now()
    tasks = get_pending_tasks()

    for task_id, description, remind_at in tasks:
        try:
            remind_time = datetime.fromisoformat(remind_at)
            if remind_time <= now:
                message = generate_task_reminder(description)
                sent = await _send_dm(message)
                if sent:
                    mark_task_fired(task_id)
                    print(f"⏰ Task #{task_id} fired: {description}")
        except Exception as e:
            print(f"❌ Error checking task #{task_id}: {e}")


async def check_campus():
    """Daily campus check - fetch page, identify pending assignments, notify user."""
    if not _discord_bot:
        return

    print(f"📚 Daily campus check at {datetime.now().isoformat()}")

    result = campus.check_campus_assignments()

    if not result.get("success"):
        error = result.get("error", "unknown")
        print(f"❌ Campus check failed: {error}")
        # Notify user about error (but don't spam - only on auth/network errors)
        if "Auth failed" in error or "Network" in error:
            await _send_dm(
                f"Sayang, ada masalah pas cek tugas kampus nih: {error} 🌸\n"
                f"Coba perbarui cookies kamu dengan `!kampus cookie <cookie_string>` ya"
            )
        return

    summary = campus.format_assignment_summary(for_notification=True)

    if summary:
        new_count = result.get("new", 0)
        total = result.get("total_found", 0)
        header = (
            f"📚 Halo Sayang! Aku udah cek kampus kamu tadi pagi 🤍\n"
            f"📊 Ketemu {total} tugas yang belum dikerjain"
            f"{f', {new_count} di antaranya tugas baru' if new_count > 0 else ''}:\n\n"
        )
        message = header + summary + "\n\nSemangat ya sayang! Jangan lupa kerjain 💕"
        await _send_dm(message)

        # Mark all as notified
        from database import get_unnotified_pending_assignments, mark_assignment_notified
        for aid, *_ in get_unnotified_pending_assignments():
            mark_assignment_notified(aid)
    else:
        print(f"✅ No pending assignments found")
        # Optional: send encouraging message
        # await _send_dm(f"Sayang, gak ada tugas pending di kampus kamu hari ini. Santai aja ya 🤍")


def parse_time_string(time_str):
    """
    Parse natural language time strings.
    Supports:
      - "2 jam lagi"
      - "30 menit lagi"
      - "besok jam 15:30"
      - "jam 15:30"
      - "15:30"
      - ISO format "2024-01-15T15:30:00"
    Returns datetime or None.
    """
    if not time_str:
        return None

    time_str = time_str.lower().strip()
    now = datetime.now()

    # "X jam lagi"
    m = re.search(r'(\d+)\s*jam\s*lagi', time_str)
    if m:
        hours = int(m.group(1))
        return now + timedelta(hours=hours)

    # "X menit lagi"
    m = re.search(r'(\d+)\s*menit\s*lagi', time_str)
    if m:
        minutes = int(m.group(1))
        return now + timedelta(minutes=minutes)

    # "X hari lagi"
    m = re.search(r'(\d+)\s*hari\s*lagi', time_str)
    if m:
        days = int(m.group(1))
        return now + timedelta(days=days)

    # "besok jam HH:MM"
    m = re.search(r'besok\s*jam\s*(\d{1,2})[:\.](\d{2})', time_str)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        tomorrow = now + timedelta(days=1)
        return tomorrow.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # "besok" saja (default jam 9 pagi)
    if time_str == "besok" or time_str.startswith("besok"):
        tomorrow = now + timedelta(days=1)
        return tomorrow.replace(hour=9, minute=0, second=0, microsecond=0)

    # "jam HH:MM" atau "HH:MM"
    m = re.search(r'(?:jam\s*)?(\d{1,2})[:\.](\d{2})', time_str)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)  # next day if time already passed
        return target

    # ISO format fallback
    try:
        return datetime.fromisoformat(time_str)
    except Exception:
        return None


def setup_scheduler():
    """Initialize all scheduled jobs."""
    # Meal reminders (cron jobs)
    for meal_time in MEAL_TIMES:
        try:
            parts = meal_time.strip().split(":")
            if len(parts) != 2:
                continue
            hour, minute = int(parts[0]), int(parts[1])
            scheduler.add_job(
                send_meal_reminder,
                CronTrigger(hour=hour, minute=minute),
                args=[meal_time.strip()],
                id=f"meal_{meal_time}",
                replace_existing=True,
            )
            print(f"✅ Scheduled meal reminder at {meal_time.strip()}")
        except Exception as e:
            print(f"❌ Failed to schedule meal reminder at {meal_time}: {e}")

    # Task checker every minute
    scheduler.add_job(
        check_tasks,
        'interval',
        minutes=1,
        id='task_checker',
        replace_existing=True,
    )
    print("✅ Scheduled task checker (every 1 minute)")

    # Sync env campus config to DB (if set via .env)
    if CAMPUS_URL:
        set_campus_url(CAMPUS_URL)
        print(f"✅ Campus URL synced from env: {CAMPUS_URL}")
    if CAMPUS_COOKIES:
        set_campus_cookies(CAMPUS_COOKIES)
        print(f"✅ Campus cookies synced from env")

    # Daily campus check (if enabled or URL is set)
    try:
        parts = CAMPUS_CHECK_TIME.strip().split(":")
        hour, minute = int(parts[0]), int(parts[1])
        scheduler.add_job(
            check_campus,
            CronTrigger(hour=hour, minute=minute),
            id='campus_check',
            replace_existing=True,
        )
        print(f"✅ Scheduled campus check at {CAMPUS_CHECK_TIME} (daily)")
    except Exception as e:
        print(f"❌ Failed to schedule campus check: {e}")

    scheduler.start()
    print("📅 Scheduler started")


def shutdown_scheduler():
    """Gracefully shutdown scheduler."""
    try:
        scheduler.shutdown(wait=False)
        print("📅 Scheduler stopped")
    except Exception:
        pass
