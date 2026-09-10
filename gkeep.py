"""
ATMDS Bot Google Keep Integration
- Simpan catatan langsung ke Google Keep user
- Bisa via !catat <isi> command di Discord
- Bisa trigger via natural language ("catet nih: ...")
- Auto-tag dengan label yang sudah diset
"""
import os
import threading
from datetime import datetime
from config import GOOGLE_KEEP_EMAIL, GOOGLE_KEEP_APP_PASSWORD, GOOGLE_KEEP_DEFAULT_TAG

# Lazy-loaded singletons
_keep_client = None
_keep_lock = threading.Lock()
_keep_initialized = False


def _get_keep():
    """Lazy init gkeepapi client (thread-safe)."""
    global _keep_client, _keep_initialized
    if _keep_initialized:
        return _keep_client

    if not GOOGLE_KEEP_EMAIL or not GOOGLE_KEEP_APP_PASSWORD:
        return None

    try:
        import gkeepapi
        with _keep_lock:
            if _keep_initialized:
                return _keep_client
            _keep_client = gkeepapi.Keep()
            _keep_client.login(GOOGLE_KEEP_EMAIL, GOOGLE_KEEP_APP_PASSWORD)
            _keep_initialized = True
            print(f"✅ Google Keep login successful: {GOOGLE_KEEP_EMAIL}")
        return _keep_client
    except Exception as e:
        err = str(e)[:200]
        print(f"❌ Google Keep login failed: {err}")
        _keep_initialized = True  # Don't retry every time
        return None


def is_configured():
    """Check if Google Keep is configured."""
    return bool(GOOGLE_KEEP_EMAIL and GOOGLE_KEEP_APP_PASSWORD)


def is_authenticated():
    """Check if Google Keep is logged in successfully."""
    return _keep_client is not None


def create_note(title, content, tag=None, color=None):
    """
    Create a new note in Google Keep.
    Returns: (success: bool, message: str)
    """
    keep = _get_keep()
    if not keep:
        return False, "Google Keep belum dikonfigurasi. Set GOOGLE_KEEP_EMAIL & GOOGLE_KEEP_APP_PASSWORD di .env atau pakai `!gkeep set <email> <app_password>` di Discord."

    try:
        # Create note
        gnote = keep.createNote(title, content)
        # Set color if specified (WHITE, RED, ORANGE, YELLOW, GREEN, TEAL, BLUE, PURPLE, PINK)
        if color:
            color_map = {
                "white": "WHITE",
                "red": "RED",
                "orange": "ORANGE",
                "yellow": "YELLOW",
                "green": "GREEN",
                "teal": "TEAL",
                "blue": "BLUE",
                "purple": "PURPLE",
                "pink": "PINK",
            }
            color_upper = color_map.get(color.lower(), color.upper())
            try:
                gnote.color = getattr(gnote, 'Color', None) and gnote.Color.Value(color_upper)
            except Exception:
                pass  # Color is optional

        # Apply tag/label if exists
        tag_to_use = tag or GOOGLE_KEEP_DEFAULT_TAG
        if tag_to_use:
            try:
                labels = keep.findLabels()
                for label in labels:
                    if label.name.lower() == tag_to_use.lower():
                        gnote.labels.add(label.id)
                        break
                else:
                    # Label doesn't exist, create it
                    new_label = keep.createLabel(tag_to_use)
                    gnote.labels.add(new_label.id)
            except Exception as label_err:
                print(f"⚠️ Failed to set label: {label_err}")

        # Sync (push to Google Keep)
        keep.sync()

        # Get URL (gkeepapi doesn't provide direct URL, but use the note ID)
        note_id = gnote.id
        url = f"https://keep.google.com/u/0/#NOTE/{note_id}"
        return True, f"✅ Catatan tersimpan di Google Keep!\n📝 Judul: {title}\n🔗 {url}"
    except Exception as e:
        err = str(e)[:200]
        return False, f"❌ Gagal simpan ke Google Keep: {err}"


def create_quick_note(content, tag=None):
    """
    Create a quick note (auto-generate title from first 30 chars of content).
    Returns: (success, message)
    """
    title = content[:30].replace("\n", " ")
    if len(content) > 30:
        title += "..."
    return create_note(title, content, tag=tag)


def list_notes(limit=10, label=None):
    """
    List recent notes.
    Returns: (success, message)
    """
    keep = _get_keep()
    if not keep:
        return False, "Google Keep belum dikonfigurasi."

    try:
        # Sort by timestamps.updated descending
        notes = sorted(keep.all(), key=lambda n: n.timestamps.updated, reverse=True)

        if label:
            # Filter by label name
            notes = [n for n in notes if any(l.name.lower() == label.lower() for l in n.labels.all())]

        notes = notes[:limit]

        if not notes:
            return True, "Belum ada catatan di Google Keep kamu."

        lines = [f"📝 Catatan Google Keep terbaru ({len(notes)}):\n"]
        for i, n in enumerate(notes, 1):
            title = n.title or "(tanpa judul)"
            content_preview = (n.text or "")[:50].replace("\n", " ")
            if len(n.text or "") > 50:
                content_preview += "..."
            labels_str = ", ".join(l.name for l in n.labels.all())
            labels_display = f" [#{labels_str}]" if labels_str else ""
            lines.append(f"{i}. **{title}**{labels_display}")
            if content_preview:
                lines.append(f"   {content_preview}")
        return True, "\n".join(lines)
    except Exception as e:
        err = str(e)[:200]
        return False, f"❌ Gagal list catatan: {err}"


def find_notes(query, limit=10):
    """
    Search notes by text query.
    Returns: (success, message)
    """
    keep = _get_keep()
    if not keep:
        return False, "Google Keep belum dikonfigurasi."

    try:
        notes = keep.find(query)
        if not notes:
            return True, f"Tidak ada catatan yang cocok dengan '{query}'."

        notes_list = list(notes)[:limit]
        lines = [f"🔍 Hasil pencarian untuk '{query}' ({len(notes_list)} catatan):\n"]
        for i, n in enumerate(notes_list, 1):
            title = n.title or "(tanpa judul)"
            content_preview = (n.text or "")[:50].replace("\n", " ")
            lines.append(f"{i}. **{title}**")
            if content_preview:
                lines.append(f"   {content_preview}")
        return True, "\n".join(lines)
    except Exception as e:
        err = str(e)[:200]
        return False, f"❌ Gagal search catatan: {err}"


def get_status():
    """Get Google Keep connection status."""
    if not is_configured():
        return "❌ Belum dikonfigurasi (isi GOOGLE_KEEP_EMAIL & GOOGLE_KEEP_APP_PASSWORD di .env atau pakai `!gkeep set`)"
    if is_authenticated():
        return f"✅ Terhubung sebagai {GOOGLE_KEEP_EMAIL}"
    return "❌ Login gagal - cek email & app password"
