"""
ATMDS Bot - Discord Bot utama
(Assistant Tapi Mesra dan Sopan)

Fitur:
- Chat dengan LLM Cerebras (qwen-3-32b)
- Rest mode: hemat token saat idle
- Notifikasi makan terjadwal (default: 12:00, 18:00, 21:00)
- Task reminder: "ingetin aku [waktu] [tugas]"
- Memory: inget nama + fakta tentang user
"""
import os
import sys
import re
import discord
from discord.ext import commands

from config import (
    DISCORD_TOKEN, DISCORD_USER_ID, USER_NAME, ASSISTANT_NAME,
    CEREBRAS_API_KEY, CEREBRAS_MODEL, REST_MODE_TIMEOUT,
)
from database import (
    init_db, save_message, add_task, add_memory,
    get_user_name, update_user_name, list_all_tasks,
    clear_conversations,
    set_campus_url, set_campus_cookies, set_campus_enabled,
    get_campus_config, list_all_assignments, mark_assignment_done,
    get_pending_assignments,
    add_provider_key, remove_provider_key, get_provider_key,
    get_all_provider_keys, PROVIDER_DEFS,
)
from llm import generate_response, update_user_activity, is_rest_mode, get_idle_minutes
import scheduler
import campus
import provider_tester
import gkeep

# Init database
init_db()

# Discord setup
intents = discord.Intents.default()
intents.message_content = True
intents.dm_messages = True
intents.messages = True
intents.members = True

# Disable built-in help command (we use custom !help)
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


# =========== HELPERS ===========

# Discord message limit is 2000 chars (4000 for some endpoints, but we stay safe at 1900)
DISCORD_MSG_LIMIT = 1900


def chunk_message(text, limit=DISCORD_MSG_LIMIT):
    """
    Split long text into chunks <= limit chars.
    Tries to split on newline, then space, then hard-cut.
    Returns list of strings.
    """
    if len(text) <= limit:
        return [text]

    chunks = []
    remaining = text
    while len(remaining) > limit:
        # Try to find a newline near the limit (look back 200 chars)
        split_at = -1
        newline_search_start = max(0, limit - 200)
        for i in range(limit, newline_search_start, -1):
            if i <= len(remaining) and remaining[i-1] == "\n":
                split_at = i
                break

        if split_at == -1:
            # Try to find a space near the limit
            for i in range(limit, newline_search_start, -1):
                if i <= len(remaining) and remaining[i-1] == " ":
                    split_at = i
                    break

        if split_at == -1:
            # Hard cut at limit
            split_at = limit

        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()

    if remaining:
        chunks.append(remaining)

    return chunks


async def safe_reply(message, text):
    """Reply to a message, auto-splitting if too long."""
    chunks = chunk_message(text)
    for i, chunk in enumerate(chunks):
        if i == 0:
            await message.reply(chunk)
        else:
            await message.channel.send(chunk)


async def safe_send(ctx, text):
    """Send a message in ctx, auto-splitting if too long."""
    chunks = chunk_message(text)
    for chunk in chunks:
        await ctx.send(chunk)


async def safe_dm(user, text):
    """Send DM to user, auto-splitting if too long."""
    chunks = chunk_message(text)
    for chunk in chunks:
        await user.send(chunk)


@bot.event
async def on_ready():
    print("=" * 60)
    print(f"🌸 {ASSISTANT_NAME} udah online!")
    print(f"📢 Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"👤 User: {get_user_name()}")
    print(f"🤖 Model: {CEREBRAS_MODEL}")
    print(f"💤 Rest mode setelah {REST_MODE_TIMEOUT} menit idle")
    print(f"🍽️ Meal reminders: {', '.join(os.getenv('MEAL_TIMES', '12:00,18:00,21:00').split(','))}")
    print("=" * 60)

    # Setup scheduler
    scheduler.set_bot(bot)
    scheduler.setup_scheduler()

    # Send greeting to user
    try:
        user = bot.get_user(DISCORD_USER_ID)
        if user is None:
            user = await bot.fetch_user(DISCORD_USER_ID)
        if user:
            await user.send(
                f"🌸 Halo {get_user_name()}! Aku {ASSISTANT_NAME} udah aktif nih~ "
                f"Ketik apa aja buat ngobrol ya. Ketik `!status` buat lihat info, "
                f"atau `!help` buat lihat semua perintah 💕"
            )
    except Exception as e:
        print(f"⚠️ Gagal kirim greeting: {e}")


@bot.event
async def on_message(message):
    # Ignore bot messages
    if message.author.bot:
        return

    # Only respond to authorized user
    if message.author.id != DISCORD_USER_ID:
        return

    # Deduplication: skip if this message ID already processed
    # (protect against multiple bot instances running in parallel)
    if not hasattr(bot, '_processed_msg_ids'):
        bot._processed_msg_ids = {}
    msg_id = message.id
    if msg_id in bot._processed_msg_ids:
        return
    bot._processed_msg_ids[msg_id] = True
    # Cleanup old entries (keep last 100)
    if len(bot._processed_msg_ids) > 100:
        bot._processed_msg_ids = dict(list(bot._processed_msg_ids.items())[-100:])

    # Update activity (wake up from rest mode)
    update_user_activity()

    content = message.content.strip()

    # Empty message (e.g., attachment only)
    if not content:
        return

    # Handle mention in guild
    if bot.user.mentioned_in(message):
        content = re.sub(r'<@!?\d+>', '', content).strip()

    # If it's a command, dispatch to commands
    if content.startswith("!"):
        await bot.process_commands(message)
        return

    # Handle DM or mention as regular chat
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mention = bot.user in message.mentions

    if not (is_dm or is_mention):
        return

    # Handle "ingetin" natural language (without command prefix)
    if content.lower().startswith("ingetin") or content.lower().startswith("inget"):
        await handle_reminder(message, content)
        return

    # Regular chat
    print(f"💬 {message.author.display_name}: {content[:80]}")

    # Save user message
    save_message("user", content)

    # Show typing indicator
    async with message.channel.typing():
        try:
            response = generate_response(content)

            # Detect if response is an error message (don't save to conversation history)
            is_error = (
                response.startswith("Maaf sayang,") or
                response.startswith("Sayang, API key") or
                response.startswith("Bentar ya sayang,") or
                "gak ditemukan" in response.lower() or
                "ada gangguan koneksi" in response.lower()
            )

            if not is_error:
                save_message("assistant", response)

            await safe_reply(message, response)
            print(f"🤖 {ASSISTANT_NAME}: {response[:80]}{'...' if len(response) > 80 else ''}")
        except Exception as e:
            # Don't expose raw error to user (could be huge Cloudflare HTML)
            error_str = str(e)
            print(f"❌ Bot Error ({type(e).__name__}): {error_str[:300]}")

            # Send short friendly message to user
            short_msg = "Maaf sayang, ada gangguan teknis nih 🌸 Tim aku lagi cek ya. Coba lagi sebentar ya 🤍"
            await safe_reply(message, short_msg)


async def handle_reminder(message, content):
    """Parse reminder request: 'ingetin aku [time] [task]'."""
    # Strip "ingetin aku" or "inget"
    text = re.sub(r'^inget(in)?\s+(aku\s+)?', '', content, flags=re.IGNORECASE).strip()

    if not text:
        await safe_reply(message,
            f"Hai {get_user_name()}, mau diingetin apa nih? "
            f"Format: `ingetin aku [waktu] [tugas]` 🌸\n"
            f"Contoh: `ingetin aku 2 jam lagi belajar matematika`"
        )
        return

    # Try to extract time pattern from text
    time_patterns = [
        (r'(\d+\s*jam\s*lagi)', None),
        (r'(\d+\s*menit\s*lagi)', None),
        (r'(\d+\s*hari\s*lagi)', None),
        (r'(besok\s*jam\s*\d{1,2}[:\.]\d{2})', None),
        (r'(jam\s*\d{1,2}[:\.]\d{2})', None),
        (r'(\d{1,2}[:\.]\d{2})', None),
        (r'(besok)', None),
    ]

    found_time_str = None
    task_desc = text

    for pattern, _ in time_patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            found_time_str = m.group(1)
            task_desc = re.sub(re.escape(found_time_str), '', text, flags=re.IGNORECASE).strip()
            task_desc = re.sub(r'^(untuk|buat)\s+', '', task_desc, flags=re.IGNORECASE).strip()
            break

    if not found_time_str:
        await safe_reply(message,
            f"Hmm {get_user_name()}, kapan mau diingetin nih? 🌸\n"
            f"Coba format kayak gini:\n"
            f"• `ingetin aku 2 jam lagi [tugas]`\n"
            f"• `ingetin aku besok jam 15:30 [tugas]`\n"
            f"• `ingetin aku jam 20:00 [tugas]`"
        )
        return

    remind_at = scheduler.parse_time_string(found_time_str)

    if not remind_at:
        await safe_reply(message,
            f"Aku gak ngerti waktu '{found_time_str}' nih {get_user_name()} 🌸\n"
            f"Coba pakai format '2 jam lagi', 'besok jam 15:30', atau 'jam 20:00' ya"
        )
        return

    if not task_desc:
        await safe_reply(message,
            f"Mau diingetin apa nih {get_user_name()}? 🌸\n"
            f"Contoh: `ingetin aku {found_time_str} belajar matematika`"
        )
        return

    # Save task
    task_id = add_task(task_desc, remind_at.isoformat())
    formatted_time = remind_at.strftime("%d/%m/%Y %H:%M")
    await safe_reply(message,
        f"✅ Siap {get_user_name()}! Aku ingetin kamu untuk **{task_desc}** "
        f"pada {formatted_time} ya 🌸\n"
        f"(ID: #{task_id})"
    )
    print(f"📝 Task #{task_id} added: {task_desc} at {formatted_time}")


# =========== COMMANDS ===========

@bot.command(name="status")
async def cmd_status(ctx):
    """Cek status bot & rest mode."""
    name = get_user_name()
    idle = get_idle_minutes()
    rest_status = "💤 Rest mode" if is_rest_mode() else "💚 Aktif"
    tasks = list_all_tasks()
    pending = [t for t in tasks if t[3] == 0 and t[4] == 0]

    embed = discord.Embed(
        title=f"🌸 Status {ASSISTANT_NAME}",
        color=discord.Color.pink()
    )
    embed.add_field(name="👤 User", value=name, inline=True)
    embed.add_field(name="🤖 Model", value=CEREBRAS_MODEL, inline=True)
    embed.add_field(name="💚 Mode", value=rest_status, inline=True)
    embed.add_field(name="⏰ Idle", value=f"{idle} menit", inline=True)
    embed.add_field(name="📝 Pending Tasks", value=str(len(pending)), inline=True)
    embed.add_field(name="🍽️ Meal Reminders", value=", ".join(__import__('config').MEAL_TIMES), inline=True)
    await ctx.send(embed=embed)


@bot.command(name="tugas")
async def cmd_tasks(ctx):
    """Lihat semua tugas yang belum selesai."""
    tasks = list_all_tasks()
    if not tasks:
        await ctx.send(f"Belum ada tugas nih {get_user_name()} 🌸")
        return

    lines = [f"📝 Daftar tugas {get_user_name()}:\n"]
    for tid, desc, remind_at, completed, fired in tasks[:10]:
        status = "✅" if completed else ("📤" if fired else "⏳")
        try:
            dt = __import__('datetime').datetime.fromisoformat(remind_at)
            time_str = dt.strftime("%d/%m %H:%M")
        except Exception:
            time_str = remind_at
        lines.append(f"{status} #{tid} - {desc} (jadwal: {time_str})")

    await safe_send(ctx, "\n".join(lines))


@bot.command(name="selesai")
async def cmd_done(ctx, task_id: int):
    """Tandai tugas selesai. Usage: !selesai <id>"""
    from database import mark_task_done
    mark_task_done(task_id)
    await ctx.send(f"Yeay {get_user_name()}! Tugas #{task_id} udah selesai 🌸 Semangat ya!")


@bot.command(name="ingat")
async def cmd_remember(ctx, *, text):
    """Simpan memory jangka panjang. Usage: !ingat <fakta>"""
    add_memory(text)
    await ctx.send(f"Oke {get_user_name()}, aku inget ini: {text} 💚")


@bot.command(name="panggil")
async def cmd_callme(ctx, *, name):
    """Ubah nama user. Usage: !panggil <nama baru>"""
    update_user_name(name)
    await ctx.send(f"Siap! Mulai sekarang aku panggil kamu {name} 🌸")


@bot.command(name="reset")
async def cmd_reset(ctx):
    """Reset conversation history."""
    clear_conversations()
    await ctx.send(f"Oke {get_user_name()}, aku udah reset memory percakapan 🌸")


@bot.command(name="hapus")
async def cmd_hapus(ctx, jumlah: int = 50):
    """
    Hapus pesan-pesan terbaru di channel/DM ini.
    Usage: !hapus [jumlah] (default 50, max 100)
    Akan hapus pesan dari user dan bot.
    """
    if jumlah > 100:
        jumlah = 100
    if jumlah < 1:
        jumlah = 1

    # Konfirmasi
    msg = await ctx.send(f"⏳ Hapus {jumlah} pesan terbaru ya sayang... 🌸")

    try:
        # Discord bulk_delete (max 100, messages < 14 hari)
        deleted = 0
        async for message in ctx.channel.history(limit=jumlah + 5):
            # Skip the confirmation message itself
            if message.id == msg.id:
                continue
            try:
                await message.delete()
                deleted += 1
                if deleted >= jumlah:
                    break
            except discord.HTTPException:
                # Some messages can't be deleted (e.g., older than 14 days in DM)
                continue
            except discord.Forbidden:
                await ctx.send("❌ Aku gak punya permission buat hapus pesan di channel ini sayang 🌸")
                return

        # Hapus confirmation message juga
        try:
            await msg.delete()
        except Exception:
            pass

        # Kirim summary (akan kehapus juga jadi biarin)
        summary = await ctx.send(f"✅ Udah hapus {deleted} pesan ya sayang 🤍")
        # Auto-delete summary setelah 3 detik
        import asyncio
        await asyncio.sleep(3)
        try:
            await summary.delete()
        except Exception:
            pass
    except Exception as e:
        await ctx.send(f"❌ Ada error pas hapus pesan: {str(e)[:100]} 🌸")


@bot.command(name="clear")
async def cmd_clear(ctx, mode: str = "all"):
    """
    Clear history atau messages.
    Usage:
    !clear chat - Reset conversation history bot (memory LLM)
    !clear messages [jumlah] - Hapus pesan Discord (default 50)
    """
    mode = mode.lower()

    if mode == "chat":
        clear_conversations()
        await ctx.send(f"✅ Memory percakapan udah aku reset sayang 🤍 Sekarang aku mulai fresh lagi.")
    elif mode == "messages":
        # Call hapus with default count
        await cmd_hapus.callback(ctx, 50)
    elif mode == "all":
        # Reset chat history + delete messages
        clear_conversations()
        await ctx.send(f"✅ Memory percakapan di-reset 🤍 Sekarang aku hapus pesan Discord ya...")
        await cmd_hapus.callback(ctx, 50)
    else:
        await ctx.send(
            f"Hmm, mode gak dikenal sayang 🌸\n"
            f"Pakai: `!clear chat` (reset memory), `!clear messages` (hapus Discord), atau `!clear all` (dua-duanya)"
        )


@bot.command(name="help")
async def cmd_help(ctx):
    """Tampilkan semua perintah."""
    embed = discord.Embed(
        title=f"📚 Bantuan {ASSISTANT_NAME}",
        description="Berikut perintah yang tersedia:",
        color=discord.Color.pink()
    )
    embed.add_field(
        name="💬 Chat",
        value="Ketik apa aja di DM atau mention aku buat ngobrol",
        inline=False
    )
    embed.add_field(
        name="⏰ Reminder",
        value="`ingetin aku [waktu] [tugas]` (natural)\n"
              "Contoh: `ingetin aku 2 jam lagi belajar`",
        inline=False
    )
    embed.add_field(
        name="🎓 Kampus",
        value="`!kampus set <url>` - Set URL kampus kamu\n"
              "`!kampus cookie <cookie>` - Set cookies (untuk login)\n"
              "`!kampus cek` - Cek tugas sekarang\n"
              "`!kampus list` - Lihat semua tugas pending\n"
              "`!kampus selesai <id>` - Tandai tugas selesai\n"
              "`!kampus status` - Lihat konfigurasi kampus\n"
              "`!kampus tutorial` - Cara ambil cookies",
        inline=False
    )
    embed.add_field(
        name="🔑 API Key LLM",
        value="`!apikey add <provider> <key> [model]` - Tambah API key\n"
              "`!apikey list` - Lihat semua provider\n"
              "`!apikey test <provider>` - Test provider tertentu\n"
              "`!apikey test all` - Test semua provider\n"
              "`!apikey remove <provider>` - Hapus provider\n"
              "`!apikey tutorial` - Cara daftar API key\n\n"
              "Provider: cloudflare, openrouter, zai, groq, together, cerebras, deepseek, openai",
        inline=False
    )
    embed.add_field(
        name="📝 Catatan (Google Keep)",
        value="`!catat <isi>` - Bikin catatan di Google Keep\n"
              "`!gkeep set <email> <app_password>` - Setup Google Keep\n"
              "`!gkeep status` - Cek status koneksi\n"
              "`!gkeep test` - Test login Google Keep\n"
              "`!gkeep list` - Lihat catatan terbaru\n"
              "`!gkeep cari <keyword>` - Cari catatan\n"
              "`!gkeep tutorial` - Cara ambil App Password Google",
        inline=False
    )
    embed.add_field(
        name="📋 Commands",
        value="`!status` - Lihat status bot\n"
              "`!tugas` - Lihat semua tugas (personal)\n"
              "`!selesai <id>` - Tandai tugas selesai (personal)\n"
              "`!ingat <fakta>` - Simpan memory\n"
              "`!panggil <nama>` - Ubah nama kamu\n"
              "`!reset` - Reset percakapan (memory LLM)\n"
              "`!hapus [jumlah]` - Hapus pesan Discord (default 50)\n"
              "`!clear chat|messages|all` - Reset memory / hapus pesan\n"
              "`!help` - Tampilkan bantuan ini",
        inline=False
    )
    await ctx.send(embed=embed)


# =========== CAMPUS COMMANDS ===========

@bot.group(name="kampus", invoke_without_command=True)
async def cmd_kampus(ctx):
    """Kelola integrasi kampus."""
    if ctx.invoked_subcommand is None:
        await cmd_kampus_status.callback(ctx)


@cmd_kampus.command(name="set")
async def cmd_kampus_set(ctx, *, url):
    """Set URL halaman daftar tugas kampus kamu. Usage: !kampus set <url>"""
    set_campus_url(url)
    await ctx.send(
        f"✅ Oke sayang! URL kampus udah aku simpan 🤍\n"
        f"🔗 {url}\n\n"
        f"Kalau halamannya butuh login, jangan lupa set cookies juga ya. "
        f"Ketik `!kampus tutorial` buat lihat caranya 🌸"
    )


@cmd_kampus.command(name="cookie")
async def cmd_kampus_cookie(ctx, *, cookie_string):
    """Set cookies untuk akses halaman kampus yang butuh login. Usage: !kampus cookie <cookie_string>"""
    # Try to delete user's message for security (don't leave cookies in chat)
    try:
        await ctx.message.delete()
    except Exception:
        pass

    set_campus_cookies(cookie_string)
    set_campus_enabled(True)
    await ctx.send(
        f"✅ Cookies udah aku simpan dengan aman, sayang 🤍\n"
        f"🔐 Pesan kamu otomatis aku hapus biar gak bocor di chat ya.\n\n"
        f"Sekarang ketik `!kampus cek` buat test apakah cookienya jalan 🌸"
    )


@cmd_kampus.command(name="cek")
async def cmd_kampus_cek(ctx):
    """Cek tugas kampus sekarang."""
    config = get_campus_config()
    if not config["url"]:
        await ctx.send(
            f"Sayang, URL kampus belum di-set nih 🌸\n"
            f"Ketik: `!kampus set <url>` dulu ya"
        )
        return

    await ctx.send(f"⏳ Bentar ya sayang, aku lagi cek kampus kamu... 🌸")

    result = campus.check_campus_assignments()

    if not result.get("success"):
        error = result.get("error", "unknown")
        await ctx.send(
            f"❌ Maaf sayang, gagal cek kampus: {error} 🌸\n"
            f"Kalau soal auth, coba update cookies: `!kampus cookie <cookie>`"
        )
        return

    summary = campus.format_assignment_summary(for_notification=False)
    if summary:
        new_count = result.get("new", 0)
        total = result.get("total_found", 0)
        overdue = result.get("overdue", 0)
        pages = result.get("fetched_pages", 1)

        overdue_text = f"\n⚠️ {overdue} tugas uda lewat deadline!" if overdue > 0 else ""
        await safe_send(ctx,
            f"📚 Halo sayang! Ini hasil cek kampus kamu 🤍\n"
            f"📊 Total {total} tugas pending"
            f"{f', {new_count} baru' if new_count > 0 else ''}"
            f"{overdue_text}\n\n"
            f"{summary}\n\n"
            f"Semangat ya! Aku yakin kamu bisa kerjain semua 💕"
        )
    else:
        await safe_send(ctx,
            f"🎉 Wah sayang, gak ada tugas pending di kampus kamu! 🤍\n"
            f"Santai dulu yuk~ atau kalau mau aku cek lagi nanti, tinggal bilang 🌸"
        )


@cmd_kampus.command(name="list")
async def cmd_kampus_list(ctx):
    """Lihat semua tugas kampus yang belum selesai."""
    assignments = get_pending_assignments()
    if not assignments:
        await ctx.send(f"Belum ada tugas pending nih sayang 🌸 Mungkin belum di-cek juga. Coba `!kampus cek` dulu ya.")
        return

    lines = [f"📝 Tugas kampus {get_user_name()} yang belum selesai:\n"]
    for aid, title, due_date, course, url in assignments:
        line = f"#{aid} - **{title}**"
        if course:
            line += f" | 📚 {course}"
        if due_date:
            line += f" | ⏰ {due_date}"
        lines.append(line)

    lines.append(f"\nKetik `!kampus selesai <id>` buat tandai yang udah dikerjain 🤍")
    await safe_send(ctx, "\n".join(lines))


@cmd_kampus.command(name="selesai")
async def cmd_kampus_done(ctx, assignment_id: int):
    """Tandai tugas kampus selesai. Usage: !kampus selesai <id>"""
    mark_assignment_done(assignment_id)
    await ctx.send(
        f"Yeay sayang! Tugas kampus #{assignment_id} udah selesai 🤍\n"
        f"Aku bangga sama kamu, semangat terus ya 💕"
    )


@cmd_kampus.command(name="status")
async def cmd_kampus_status(ctx):
    """Lihat status konfigurasi kampus."""
    config = get_campus_config()
    assignments = list_all_assignments()
    pending = [a for a in assignments if a[4] == "pending"]

    embed = discord.Embed(
        title="🎓 Status Kampus",
        color=discord.Color.pink()
    )
    embed.add_field(
        name="🔗 URL",
        value=config["url"][:60] + "..." if len(config["url"]) > 60 else (config["url"] or "❌ Belum diset"),
        inline=False
    )
    embed.add_field(
        name="🔐 Cookies",
        value="✅ Sudah diset" if config["cookies"] else "❌ Belum diset",
        inline=True
    )
    embed.add_field(
        name="💚 Enabled",
        value="✅ Ya" if config["enabled"] else "❌ Tidak",
        inline=True
    )
    embed.add_field(
        name="🕐 Last Checked",
        value=config["last_checked"][:19].replace("T", " ") if config["last_checked"] else "Belum pernah",
        inline=True
    )
    embed.add_field(
        name="📊 Total Pending",
        value=f"{len(pending)} tugas",
        inline=False
    )
    await ctx.send(embed=embed)


@cmd_kampus.command(name="tutorial")
async def cmd_kampus_tutorial(ctx):
    """Tutorial cara ambil cookies dari browser."""
    embed = discord.Embed(
        title="📚 Tutorial: Cara Ambil Cookies Kampus",
        description=(
            "Cookies dipakai biar bot bisa akses halaman kampus yang butuh login, "
            "tanpa kamu harus kasih password ke bot 🌸"
        ),
        color=discord.Color.pink()
    )
    embed.add_field(
        name="🔧 Cara Ambil Cookies",
        value=(
            "1. Buka **halaman daftar tugas kampus** di browser kamu (pastikan sudah login)\n"
            "2. Tekan **F12** untuk buka Developer Tools\n"
            "3. Klik tab **Application** (Chrome) atau **Storage** (Firefox)\n"
            "4. Di sidebar kiri, cari **Cookies** → klik nama domain kampus kamu\n"
            "5. Kamu akan lihat tabel berisi: Name | Value | Domain\n"
            "6. Copy semua baris (atau minimal yang penting: `session`, `PHPSESSID`, `MoodleSession`, dll)\n"
            "7. Format jadi: `name1=value1; name2=value2; name3=value3`\n"
            "8. Ketik di Discord: `!kampus cookie <paste disini>`"
        ),
        inline=False
    )
    embed.add_field(
        name="💡 Alternatif Cepat",
        value=(
            "1. Buka halaman tugas kampus di browser\n"
            "2. Tekan **F12** → klik tab **Network**\n"
            "3. Refresh halaman (F5)\n"
            "4. Klik request pertama (nama file HTML)\n"
            "5. Cari **Request Headers** → cari baris `Cookie:`\n"
            "6. Copy seluruh nilai setelah `Cookie: `\n"
            "7. Ketik: `!kampus cookie <paste>`"
        ),
        inline=False
    )
    embed.add_field(
        name="🔒 Keamanan",
        value=(
            "• Bot otomatis hapus pesan kamu yang berisi cookies 🤍\n"
            "• Cookies disimpan di SQLite lokal di server kamu\n"
            "• Jangan share cookies ke siapapun!\n"
            "• Logout dari kampus = cookies expired, harus ambil lagi"
        ),
        inline=False
    )
    embed.set_footer(text="Kalau bingung, kasih tau aku URL kampus kamu, aku bantu 🌸")
    await ctx.send(embed=embed)


# =========== APIKEY COMMANDS ===========

@bot.group(name="apikey", invoke_without_command=True)
async def cmd_apikey(ctx):
    """Kelola API key LLM provider."""
    if ctx.invoked_subcommand is None:
        await cmd_apikey_list.callback(ctx)


@cmd_apikey.command(name="add")
async def cmd_apikey_add(ctx, provider: str, api_key: str, model: str = None):
    """
    Tambah/update API key provider.
    Usage: !apikey add <provider> <key> [model]
    Auto-delete pesan yang berisi API key untuk keamanan.
    """
    provider = provider.lower().strip()

    # Validate provider name
    if provider not in PROVIDER_DEFS:
        supported = ", ".join(PROVIDER_DEFS.keys())
        await ctx.send(
            f"❌ Provider '{provider}' gak dikenal nih sayang 🌸\n"
            f"Provider yang didukung: `{supported}`\n\n"
            f"Ketik `!apikey tutorial` buat lihat cara daftar di tiap provider ya 🤍"
        )
        return

    # Auto-delete user message for security (it contains the API key)
    try:
        await ctx.message.delete()
    except Exception:
        pass

    # Add to DB
    success, msg = add_provider_key(provider, api_key, model)
    defn = PROVIDER_DEFS[provider]

    if success:
        await ctx.send(
            f"✅ Siap sayang! API key {defn['display_name']} udah aku simpan 🤍\n"
            f"📝 Model: `{model or defn['default_model']}`\n"
            f"🔐 Pesan kamu otomatis aku hapus biar key gak bocor ya\n\n"
            f"Sekarang ketik `!apikey test {provider}` buat test apakah jalan dari server ini 🌸"
        )
    else:
        await ctx.send(f"❌ {msg}")


@cmd_apikey.command(name="remove")
async def cmd_apikey_remove(ctx, provider: str):
    """Hapus provider dari DB. Usage: !apikey remove <provider>"""
    provider = provider.lower().strip()
    deleted = remove_provider_key(provider)
    if deleted:
        defn = PROVIDER_DEFS.get(provider, {})
        display = defn.get("display_name", provider)
        await ctx.send(f"✅ Provider {display} udah aku hapus sayang 🤍")
    else:
        await ctx.send(f"❌ Provider '{provider}' gak ada di DB nih 🌸")


@cmd_apikey.command(name="list")
async def cmd_apikey_list(ctx):
    """Lihat semua provider yang ada di DB + dari env."""
    db_providers = get_all_provider_keys()

    # Also show what's available from env (not in DB)
    from config import (
        CEREBRAS_API_KEY, GROQ_API_KEY, ZAI_API_KEY,
        OPENROUTER_API_KEY, TOGETHER_API_KEY,
        CEREBRAS_MODEL, GROQ_MODEL, ZAI_MODEL,
        OPENROUTER_MODEL, TOGETHER_MODEL,
        CLOUDFLARE_API_KEY, CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_MODEL,
    )
    env_providers = {
        "cloudflare": (CLOUDFLARE_API_KEY, CLOUDFLARE_MODEL, CLOUDFLARE_ACCOUNT_ID),
        "openrouter": (OPENROUTER_API_KEY, OPENROUTER_MODEL, None),
        "zai": (ZAI_API_KEY, ZAI_MODEL, None),
        "groq": (GROQ_API_KEY, GROQ_MODEL, None),
        "together": (TOGETHER_API_KEY, TOGETHER_MODEL, None),
        "cerebras": (CEREBRAS_API_KEY, CEREBRAS_MODEL, None),
    }

    db_provider_names = {p["provider"] for p in db_providers}

    lines = ["**🔑 Daftar Provider LLM:**\n"]

    # DB providers (set via Discord command)
    if db_providers:
        lines.append("**📥 Dari command `!apikey add`:**")
        for p in db_providers:
            emoji = provider_tester.STATUS_EMOJI.get(p["status"], "⏳")
            masked_key = p["api_key"][:8] + "..." if len(p["api_key"]) > 8 else "***"
            defn = PROVIDER_DEFS.get(p["provider"], {})
            display = defn.get("display_name", p["provider"])
            lines.append(f"  {emoji} `{p['provider']}` - {display}")
            lines.append(f"      Key: `{masked_key}` | Model: `{p['model']}` | Status: `{p['status']}`")
        lines.append("")

    # Env providers (not in DB)
    env_lines = []
    for name, (key, model, account_id) in env_providers.items():
        if key and name not in db_provider_names:
            defn = PROVIDER_DEFS.get(name, {})
            display = defn.get("display_name", name)
            masked_key = key[:8] + "..." if len(key) > 8 else "***"
            extra = f" | Account: `{account_id[:8]}...`" if account_id else ""
            env_lines.append(f"  ⚙️ `{name}` - {display} | Key: `{masked_key}`{extra} | Model: `{model}`")

    if env_lines:
        lines.append("**⚙️ Dari file `.env`:**")
        lines.extend(env_lines)
        lines.append("")

    # Available providers (not yet added)
    available = [name for name in PROVIDER_DEFS if name not in db_provider_names and not env_providers.get(name, ("",))[0]]
    if available:
        lines.append("**📚 Provider lain yang bisa dicoba:**")
        for name in available:
            defn = PROVIDER_DEFS[name]
            lines.append(f"  • `{name}` - {defn['display_name']} (daftar: {defn['signup_url']})")
        lines.append("")

    lines.append("**Commands:**")
    lines.append("• `!apikey add <provider> <key> [model]` - tambah provider")
    lines.append("• `!apikey test <provider>` - test provider tertentu")
    lines.append("• `!apikey test all` - test semua provider")
    lines.append("• `!apikey remove <provider>` - hapus provider")
    lines.append("• `!apikey tutorial` - cara daftar API key")

    await safe_send(ctx, "\n".join(lines))


@cmd_apikey.command(name="test")
async def cmd_apikey_test(ctx, provider: str = "all"):
    """Test provider. Usage: !apikey test <provider> atau !apikey test all"""
    provider = provider.lower().strip()

    if provider == "all":
        await ctx.send("⏳ Bentar sayang, aku lagi test semua provider... 🌸")
        results = provider_tester.test_all_providers()

        if not results:
            await ctx.send(
                f"Belum ada provider di DB nih sayang 🌸\n"
                f"Tambah dulu dengan: `!apikey add <provider> <key>`\n"
                f"Atau ketik `!apikey tutorial` buat cara daftar"
            )
            return

        lines = ["**🧪 Hasil test semua provider:**\n"]
        for r in results:
            emoji = provider_tester.STATUS_EMOJI.get(r["status"], "❓")
            defn = PROVIDER_DEFS.get(r["provider"], {})
            display = defn.get("display_name", r["provider"])
            lines.append(f"{emoji} **{display}** (`{r['provider']}`)")
            lines.append(f"   Model: `{r['model']}`")
            lines.append(f"   Status: `{r['status']}`")
            lines.append(f"   Detail: {r['message']}\n")

        working = sum(1 for r in results if r["status"] == "working")
        blocked = sum(1 for r in results if r["status"] == "blocked")
        lines.append(f"📊 Summary: {working} working, {blocked} blocked, {len(results) - working - blocked} lainnya")

        await safe_send(ctx, "\n".join(lines))
        return

    # Test single provider
    if provider not in PROVIDER_DEFS:
        supported = ", ".join(PROVIDER_DEFS.keys())
        await ctx.send(f"❌ Provider '{provider}' gak dikenal. Yang tersedia: {supported} 🌸")
        return

    # Get key from DB or env
    p_info = get_provider_key(provider)
    if not p_info:
        # Check env
        from config import (
            CEREBRAS_API_KEY, GROQ_API_KEY, ZAI_API_KEY,
            OPENROUTER_API_KEY, TOGETHER_API_KEY,
            CEREBRAS_MODEL, GROQ_MODEL, ZAI_MODEL,
            OPENROUTER_MODEL, TOGETHER_MODEL,
            CLOUDFLARE_API_KEY, CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_MODEL,
        )
        env_map = {
            "cloudflare": (CLOUDFLARE_API_KEY, CLOUDFLARE_MODEL, CLOUDFLARE_ACCOUNT_ID),
            "openrouter": (OPENROUTER_API_KEY, OPENROUTER_MODEL, None),
            "zai": (ZAI_API_KEY, ZAI_MODEL, None),
            "groq": (GROQ_API_KEY, GROQ_MODEL, None),
            "together": (TOGETHER_API_KEY, TOGETHER_MODEL, None),
            "cerebras": (CEREBRAS_API_KEY, CEREBRAS_MODEL, None),
        }
        if provider in env_map and env_map[provider][0]:
            api_key = env_map[provider][0]
            model = env_map[provider][1]
            account_id = env_map[provider][2]
        else:
            await ctx.send(
                f"❌ Provider '{provider}' belum punya API key nih sayang 🌸\n"
                f"Tambah dulu: `!apikey add {provider} <key>`"
            )
            return
    else:
        api_key = p_info["api_key"]
        model = p_info["model"]
        account_id = p_info.get("account_id")

    defn = PROVIDER_DEFS[provider]
    await ctx.send(f"⏳ Lagi test {defn['display_name']} ({model})... 🌸")

    # Cloudflare butuh special handling (account_id in URL)
    if provider == "cloudflare":
        if not account_id:
            await ctx.send(f"❌ Cloudflare butuh account_id sayang. Set via env CLOUDFLARE_ACCOUNT_ID atau `!apikey add cloudflare <key> <model> --account <id>` 🌸")
            return
        # Use special tester for Cloudflare
        result = _test_cloudflare(api_key, account_id, model)
    else:
        result = provider_tester.test_provider(provider, api_key, model)

    emoji = provider_tester.STATUS_EMOJI.get(result["status"], "❓")

    await ctx.send(
        f"{emoji} **{defn['display_name']}** - `{result['status']}`\n"
        f"📝 {result['message']}\n\n"
        + ("✅ Provider ini siap dipakai untuk chat! 🤍" if result["status"] == "working"
           else "❌ Provider ini gak bisa dipakai dari server ini. Coba provider lain ya sayang 🌸"
           if result["status"] in ("blocked", "failed")
           else "⏰ Coba lagi nanti ya sayang 🌸")
    )


def _test_cloudflare(api_key, account_id, model):
    """Special tester for Cloudflare Workers AI (needs account_id in URL)."""
    import httpx
    base_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"

    try:
        r = httpx.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Test. Reply: OK"}],
                "max_tokens": 10,
            },
            timeout=20.0,
        )

        if r.status_code == 200:
            try:
                data = r.json()
                # Cloudflare response format: {result: {response: "..."}, ...}
                # OR OpenAI-compatible: {choices: [{message: {content: "..."}}]}
                if "choices" in data:
                    text = data["choices"][0]["message"]["content"].strip()
                elif "result" in data and "response" in data["result"]:
                    text = data["result"]["response"].strip()
                else:
                    text = "(response format unknown)"

                # Update DB status if exists
                try:
                    update_provider_status("cloudflare", "working", None)
                except Exception:
                    pass
                return {
                    "status": "working",
                    "message": f"Berhasil! Response: {text[:60]}",
                    "response": text,
                }
            except Exception as e:
                return {"status": "failed", "message": f"Response parse error: {str(e)[:80]}"}

        # Non-200
        body_text = r.text[:300]
        if r.status_code in (401, 403):
            update_provider_status("cloudflare", "auth_failed", f"HTTP {r.status_code}")
            return {
                "status": "auth_failed",
                "message": f"Auth gagal (HTTP {r.status_code}): {body_text[:100]}",
            }
        if r.status_code == 429:
            return {"status": "rate_limited", "message": "Rate limited"}
        if "cloudflare" in body_text.lower() or "<!doctype" in body_text.lower():
            return {"status": "blocked", "message": "Cloudflare block"}
        return {"status": "failed", "message": f"HTTP {r.status_code}: {body_text[:100]}"}
    except Exception as e:
        return {"status": "failed", "message": f"Error: {str(e)[:100]}"}


@cmd_apikey.command(name="tutorial")
async def cmd_apikey_tutorial(ctx):
    """Tutorial cara daftar API key di tiap provider."""
    embed = discord.Embed(
        title="📚 Tutorial: Cara Daftar API Key LLM",
        description=(
            "Daftar akun gratis di salah satu provider di bawah, lalu tambahkan API key-nya "
            "pakai command `!apikey add <provider> <key>` 🌸\n\n"
            "**Provider Recommended (yang biasanya jalan dari server ini):**"
        ),
        color=discord.Color.pink()
    )

    embed.add_field(
        name="✅ OpenRouter (Recommended)",
        value=(
            "🔗 https://openrouter.ai/keys\n"
            "💰 Free credits untuk new user, pakai model `qwen/qwen-2.5-72b-instruct` (default)\n"
            "⭐ Multibahasa Indonesia kuat, tone natural, recommended buat ATMDS\n"
            "```!apikey add openrouter sk-or-v1-xxx```"
        ),
        inline=False
    )

    embed.add_field(
        name="✅ Z.ai (GLM-4.6)",
        value=(
            "🔗 https://z.ai/\n"
            "💰 Gratis, region Asia (kemungkinan besar bisa diakses)\n"
            "⭐ Model GLM-4.6, multibahasa Indonesia sangat kuat\n"
            "```!apikey add zai xxx.yyy.zzz```"
        ),
        inline=False
    )

    embed.add_field(
        name="✅ Together AI",
        value=(
            "🔗 https://api.together.xyz/\n"
            "💰 Dapat $5 free credit untuk new user\n"
            "⭐ Bisa pakai model Qwen, DeepSeek, dll\n"
            "```!apikey add together xxx```"
        ),
        inline=False
    )

    embed.add_field(
        name="❓ DeepSeek",
        value=(
            "🔗 https://platform.deepseek.com/\n"
            "💰 Free credit untuk new user, region Asia\n"
            "⭐ Model DeepSeek V3 (multibahasa kuat)\n"
            "```!apikey add deepseek sk-xxx```"
        ),
        inline=False
    )

    embed.add_field(
        name="❌ Cerebras / Groq / Gemini",
        value=(
            "⚠️ Ketiganya kemungkinan diblokir dari server Hong Kong ini (Cloudflare/region restriction).\n"
            "Coba aja kalau mau, kalau gak jalan ya tinggal pilih provider lain 🌸"
        ),
        inline=False
    )

    embed.add_field(
        name="🚀 Setelah tambah key",
        value=(
            "1. Test: `!apikey test <provider>`\n"
            "2. Kalau ✅ working, langsung chat aja di DM, bot otomatis pakai provider itu\n"
            "3. Kalau ❌ blocked, coba provider lain"
        ),
        inline=False
    )

    embed.set_footer(text="Kalau ada provider lain yang mau ditambah, kasih tau aku ya 🤍")
    await ctx.send(embed=embed)


# =========== CATAT / GKEEP COMMANDS ===========

@bot.command(name="catat")
async def cmd_catat(ctx, *, text):
    """
    Simpan catatan ke Google Keep.
    Usage: !catat <isi catatan>
    """
    if not gkeep.is_configured():
        await safe_send(ctx,
            f"Sayang, Google Keep belum dikonfigurasi nih 🌸\n"
            f"Setup dulu dengan: `!gkeep set <email_google> <app_password>`\n"
            f"Atau ketik `!gkeep tutorial` buat lihat cara setupnya 🤍"
        )
        return

    # Auto-delete user message for security if it contains password-like info
    # (gak hapus - catatan biasanya bukan credential)

    await ctx.send("⏳ Bentar sayang, lagi simpen ke Google Keep... 🌸")

    success, message = gkeep.create_quick_note(text)

    if success:
        await safe_send(ctx, f"✅ Sudah aku catat di Google Keep ya sayang 🤍\n\n{message}")
        print(f"📝 Note saved to Google Keep: {text[:50]}...")
    else:
        await safe_send(ctx, f"❌ Maaf sayang, gagal simpan catatan: {message} 🌸")


@bot.group(name="gkeep", invoke_without_command=True)
async def cmd_gkeep(ctx):
    """Kelola integrasi Google Keep."""
    if ctx.invoked_subcommand is None:
        # Show status
        status = gkeep.get_status()
        await safe_send(ctx,
            f"📚 **Google Keep Status**\n\n"
            f"{status}\n\n"
            f"**Commands:**\n"
            f"• `!catat <isi>` - Bikin catatan baru (cepat)\n"
            f"• `!gkeep set <email> <app_password>` - Setup Google Keep\n"
            f"• `!gkeep status` - Cek status koneksi\n"
            f"• `!gkeep list` - Lihat catatan terbaru\n"
            f"• `!gkeep cari <keyword>` - Cari catatan\n"
            f"• `!gkeep tutorial` - Cara ambil App Password Google\n"
            f"• `!gkeep test` - Test login Google Keep"
        )


@cmd_gkeep.command(name="set")
async def cmd_gkeep_set(ctx, email: str, app_password: str):
    """Set Google Keep credentials. Usage: !gkeep set <email> <app_password>"""
    # Auto-delete user message (contains credentials)
    try:
        await ctx.message.delete()
    except Exception:
        pass

    # Update .env file
    env_path = "/home/z/my-project/atmds_bot/.env"
    try:
        with open(env_path, "r") as f:
            env_content = f.read()

        # Replace or add GOOGLE_KEEP_EMAIL
        import re
        env_content = re.sub(
            r"^GOOGLE_KEEP_EMAIL=.*$",
            f"GOOGLE_KEEP_EMAIL={email}",
            env_content,
            flags=re.MULTILINE
        )
        if "GOOGLE_KEEP_EMAIL=" not in env_content:
            env_content += f"\nGOOGLE_KEEP_EMAIL={email}\n"

        # Replace or add GOOGLE_KEEP_APP_PASSWORD
        env_content = re.sub(
            r"^GOOGLE_KEEP_APP_PASSWORD=.*$",
            f"GOOGLE_KEEP_APP_PASSWORD={app_password}",
            env_content,
            flags=re.MULTILINE
        )
        if "GOOGLE_KEEP_APP_PASSWORD=" not in env_content:
            env_content += f"GOOGLE_KEEP_APP_PASSWORD={app_password}\n"

        with open(env_path, "w") as f:
            f.write(env_content)

        # Update config module runtime
        from config import GOOGLE_KEEP_DEFAULT_TAG
        import config
        config.GOOGLE_KEEP_EMAIL = email
        config.GOOGLE_KEEP_APP_PASSWORD = app_password

        # Reset gkeep client to force re-login
        import gkeep as gkeep_module
        gkeep_module._keep_client = None
        gkeep_module._keep_initialized = False

        await safe_send(ctx,
            f"✅ Google Keep credentials udah aku simpan sayang 🤍\n"
            f"📧 Email: `{email}`\n"
            f"🔐 App password: `{'*' * len(app_password)}`\n"
            f"🧹 Pesan kamu otomatis aku hapus biar gak bocor di chat ya\n\n"
            f"Sekarang ketik `!gkeep test` buat verifikasi loginnya jalan 🌸"
        )
    except Exception as e:
        await safe_send(ctx, f"❌ Gagal simpan credentials: {str(e)[:100]}")


@cmd_gkeep.command(name="status")
async def cmd_gkeep_status(ctx):
    """Cek status koneksi Google Keep."""
    status = gkeep.get_status()
    await safe_send(ctx, f"📚 **Google Keep Status**\n\n{status}")


@cmd_gkeep.command(name="test")
async def cmd_gkeep_test(ctx):
    """Test login Google Keep."""
    if not gkeep.is_configured():
        await safe_send(ctx,
            f"❌ Google Keep belum dikonfigurasi sayang 🌸\n"
            f"Pakai: `!gkeep set <email> <app_password>`"
        )
        return

    await ctx.send("⏳ Lagi test login ke Google Keep... 🌸")

    # Force re-init
    import gkeep as gkeep_module
    gkeep_module._keep_client = None
    gkeep_module._keep_initialized = False

    if gkeep.is_authenticated() or gkeep._get_keep() is not None:
        if gkeep.is_authenticated():
            await safe_send(ctx,
                f"✅ Login Google Keep BERHASIL sayang! 🤍\n"
                f"📧 Email: {gkeep.GOOGLE_KEEP_EMAIL}\n\n"
                f"Sekarang bisa pakai `!catat <isi>` buat simpen catatan 🌸"
            )
        else:
            await safe_send(ctx,
                f"❌ Login gagal sayang 🌸\n"
                f"Cek email & app password-nya ya. Ketik `!gkeep tutorial` buat lihat caranya 🤍"
            )
    else:
        await safe_send(ctx,
            f"❌ Login gagal sayang 🌸\n"
            f"Kemungkinan penyebab:\n"
            f"• App password salah\n"
            f"• Email salah\n"
            f"• Akun Google butuh verifikasi 2FA dulu (wajib aktif)\n\n"
            f"Ketik `!gkeep tutorial` buat lihat cara setupnya 🤍"
        )


@cmd_gkeep.command(name="list")
async def cmd_gkeep_list(ctx, limit: int = 10):
    """Lihat catatan terbaru di Google Keep. Usage: !gkeep list [limit]"""
    if not gkeep.is_configured():
        await safe_send(ctx, "❌ Google Keep belum dikonfigurasi. Pakai `!gkeep set` dulu 🌸")
        return

    if limit > 20:
        limit = 20

    success, message = gkeep.list_notes(limit=limit)
    await safe_send(ctx, message)


@cmd_gkeep.command(name="cari")
async def cmd_gkeep_search(ctx, *, query):
    """Cari catatan di Google Keep. Usage: !gkeep cari <keyword>"""
    if not gkeep.is_configured():
        await safe_send(ctx, "❌ Google Keep belum dikonfigurasi. Pakai `!gkeep set` dulu 🌸")
        return

    success, message = gkeep.find_notes(query, limit=10)
    await safe_send(ctx, message)


@cmd_gkeep.command(name="tutorial")
async def cmd_gkeep_tutorial(ctx):
    """Tutorial cara setup Google Keep integration."""
    embed = discord.Embed(
        title="📚 Tutorial: Setup Google Keep",
        description=(
            "Bot bisa simpen catatan langsung ke Google Keep kamu! "
            "Tinggal ketik `!catat <isi>` di Discord, catatan muncul di Google Keep 🌸\n\n"
            "Caranya:"
        ),
        color=discord.Color.pink()
    )
    embed.add_field(
        name="1️⃣ Aktifkan 2FA Google Account",
        value=(
            "Google App Password wajib 2FA aktif.\n"
            "Buka: https://myaccount.google.com/signinoptions/two-step-verification\n"
            "Aktifkan 2-Step Verification (kalau belum)."
        ),
        inline=False
    )
    embed.add_field(
        name="2️⃣ Buat App Password",
        value=(
            "Buka: https://myaccount.google.com/apppasswords\n"
            "Pilih app: 'Other' (custom name)\n"
            "Isi nama: `Dedek Tersayang Bot`\n"
            "Klik **Create**\n"
            "Copy 16-digit password yang muncul (format: `xxxx xxxx xxxx xxxx`)"
        ),
        inline=False
    )
    embed.add_field(
        name="3️⃣ Set credentials di Bot",
        value=(
            "Di Discord, ketik (jangan lupa ganti email & password-nya):\n"
            "```\n"
            "!gkeep set emailkamu@gmail.com abcdefghijklmnop\n"
            "```\n"
            "🔐 Pesan kamu otomatis dihapus biar password gak bocor."
        ),
        inline=False
    )
    embed.add_field(
        name="4️⃣ Test & Pakai",
        value=(
            "Test login: `!gkeep test`\n"
            "Bikin catatan: `!catat beli susu besok`\n"
            "Lihat catatan: `!gkeep list`\n"
            "Cari catatan: `!gkeep cari susu`"
        ),
        inline=False
    )
    embed.add_field(
        name="🔒 Keamanan",
        value=(
            "• App Password = password khusus, gak bisa dipakai buat login Gmail\n"
            "• Hanya bisa akses Google Keep (scope terbatas)\n"
            "• Bisa di-revoke kapan aja di myaccount.google.com/apppasswords\n"
            "• Gak perlu kasih password Gmail asli ke siapapun!"
        ),
        inline=False
    )
    embed.set_footer(text="Kalau error pas login, pastikan 2FA udah aktif & app password benar 🌸")
    await ctx.send(embed=embed)


def main():
    """Entry point."""
    # Validate config
    if not DISCORD_TOKEN or DISCORD_TOKEN == "your_discord_bot_token_here":
        print("❌ DISCORD_TOKEN belum diset!")
        print("   1. Copy .env.example ke .env")
        print("   2. Isi DISCORD_TOKEN dengan token bot kamu")
        sys.exit(1)

    # Cek minimal 1 LLM provider aktif
    from config import (
        CLOUDFLARE_API_KEY, CLOUDFLARE_ACCOUNT_ID,
        OPENROUTER_API_KEY, ZAI_API_KEY, GROQ_API_KEY,
        TOGETHER_API_KEY,
    )
    has_provider = (
        (CLOUDFLARE_API_KEY and CLOUDFLARE_ACCOUNT_ID) or
        OPENROUTER_API_KEY or ZAI_API_KEY or GROQ_API_KEY or
        TOGETHER_API_KEY or CEREBRAS_API_KEY
    )
    if not has_provider:
        print("⚠️ Warning: Belum ada LLM provider yang dikonfigurasi!")
        print("   Isi minimal salah satu di .env:")
        print("   - CLOUDFLARE_API_KEY + CLOUDFLARE_ACCOUNT_ID (recommended)")
        print("   - OPENROUTER_API_KEY")
        print("   - ZAI_API_KEY")
        print("   - GROQ_API_KEY")
        print("   Atau pakai !apikey add di Discord setelah bot jalan")

    if not DISCORD_USER_ID or DISCORD_USER_ID == 0:
        print("❌ DISCORD_USER_ID belum diset!")
        print("   Isi dengan Discord user ID kamu")
        sys.exit(1)

    print("🚀 Memulai ATMDS Bot...")
    print(f"🤖 Asisten: {ASSISTANT_NAME}")
    print(f"👤 User: {USER_NAME}")
    print(f"🧠 Model: {CEREBRAS_MODEL}")
    print(f"💤 Rest mode timeout: {REST_MODE_TIMEOUT} menit")
    print()

    try:
        bot.run(DISCORD_TOKEN)
    except KeyboardInterrupt:
        print("\n👋 Shutting down...")
        scheduler.shutdown_scheduler()
    except Exception as e:
        print(f"\n❌ Bot crashed: {e}")
        scheduler.shutdown_scheduler()
        raise


if __name__ == "__main__":
    main()
