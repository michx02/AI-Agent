
# logger.py
import os
import sqlite3
from datetime import datetime, timezone,timedelta
from typing import Optional
import discord

# Use one DB file for logs
LOG_DB_PATH = os.environ.get("DISCORD_LOG_DB", "discord_logs.db")

def _conn():
    con = sqlite3.connect(LOG_DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def fetch_user_recent_in_channel(channel_id: int, user_id: int, minutes=240, limit=40):
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    with _conn() as db:
        rows = db.execute(
            """
            SELECT author_id, author_name, content, is_bot, created_at
            FROM messages
            WHERE channel_id = ?
              AND author_id = ?
              AND created_at >= ?
            ORDER BY datetime(created_at) ASC
            LIMIT ?
            """,
            (str(channel_id), str(user_id), cutoff, limit),
        ).fetchall()
    lines = []
    for r in rows:
        if not r["content"]:
            continue
        name = r["author_name"] or r["author_id"]
        lines.append(f"user({name}): {r['content']}")
    return lines

def fetch_user_recent_in_guild(guild_id: int, user_id: int, minutes=240, limit=80):
    """Fallback if nothing in this channel—look across the whole server."""
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    with _conn() as db:
        rows = db.execute(
            """
            SELECT channel_id, author_id, author_name, content, created_at
            FROM messages
            WHERE guild_id = ?
              AND author_id = ?
              AND created_at >= ?
            ORDER BY datetime(created_at) ASC
            LIMIT ?
            """,
            (str(guild_id), str(user_id), cutoff, limit),
        ).fetchall()
    # Prepend channel for clarity since this spans channels
    lines = []
    for r in rows:
        if not r["content"]:
            continue
        name = r["author_name"] or r["author_id"]
        lines.append(f"user({name}) in #{r['channel_id']}: {r['content']}")
    return lines

def ensure_schema():
    with _conn() as db:
        db.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id        TEXT NOT NULL,     -- Discord message id
            channel_id        TEXT NOT NULL,     -- Channel or Thread id
            guild_id          TEXT,              -- Nullable in DMs
            author_id         TEXT NOT NULL,
            author_name       TEXT,
            content           TEXT,
            is_bot            INTEGER NOT NULL DEFAULT 0,
            reference_id      TEXT,              -- parent message id if this is a reply
            created_at        TEXT NOT NULL      -- ISO8601 UTC
        )
        """)
        db.execute("CREATE INDEX IF NOT EXISTS idx_msgs_channel_time ON messages(channel_id, created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_msgs_message_id ON messages(message_id)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_msgs_reference_id ON messages(reference_id)")

def _utcnow_iso():
    return datetime.now(timezone.utc).isoformat()

def log_message(msg: discord.Message):
    """Call this for every message in on_message before any returns."""
    ensure_schema()

    # starter: if this is in a Thread, channel.id is the thread id (good)
    channel_id = str(msg.channel.id)
    guild_id = str(msg.guild.id) if msg.guild else None
    author_id = str(msg.author.id)
    author_name = getattr(msg.author, "display_name", None) or msg.author.name
    content = msg.content or ""
    is_bot = 1 if msg.author.bot else 0

    # reply parent id if present
    ref_id: Optional[str] = None
    if msg.reference and msg.reference.message_id:
        ref_id = str(msg.reference.message_id)

    with _conn() as db:
        db.execute(
            """INSERT INTO messages
               (message_id, channel_id, guild_id, author_id, author_name, content, is_bot, reference_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(msg.id), channel_id, guild_id, author_id, author_name, content, is_bot, ref_id, _utcnow_iso())
        )

def fetch_recent_history_for_scope(message: discord.Message, limit=40, minutes=90):
    """
    Return recent messages in the same reply chain / thread / channel,
    including ones where the bot was NOT tagged. Oldest -> newest order.
    """
    ensure_schema()
    from datetime import timedelta
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()

    # Prefer reply-chain scope if the message is a reply
    # We approximate by time + same channel; optionally you can walk the chain via reference_id.
    channel_id = str(message.channel.id)

    sql = """
    SELECT author_id, author_name, content, is_bot, created_at
    FROM messages
    WHERE channel_id = ?
      AND created_at >= ?
    ORDER BY datetime(created_at) ASC
    LIMIT ?
    """
    params = (channel_id, cutoff_iso, limit)

    # If you want STRICT reply-chain only, uncomment this alternative block:
    # if message.reference and message.reference.message_id:
    #     root = str(message.reference.message_id)
    #     sql = """
    #     SELECT author_id, author_name, content, is_bot, created_at
    #     FROM messages
    #     WHERE (channel_id = ?)
    #       AND (created_at >= ?)
    #       AND (reference_id = ? OR message_id = ? OR reference_id IN (
    #              SELECT message_id FROM messages WHERE reference_id = ?
    #           ))
    #     ORDER BY datetime(created_at) ASC
    #     LIMIT ?
    #     """
    #     params = (channel_id, cutoff_iso, root, root, root, limit)

    with _conn() as db:
        rows = db.execute(sql, params).fetchall()

    lines = []
    for r in rows:
        if not r["content"]:
            continue
        role = "assistant" if r["is_bot"] else "user"
        name = r["author_name"] or r["author_id"]
        lines.append(f"{role}({name}): {r['content']}")
    return lines


