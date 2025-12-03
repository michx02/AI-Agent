# logger.py
import discord
from datetime import datetime, timezone, timedelta
from typing import Optional

from db_postgres import get_connection


def fetch_user_recent_in_channel(channel_id: int, user_id: int, minutes=240, limit=40):
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT author_id, author_name, content, is_bot, created_at
                FROM messages
                WHERE channel_id = %s
                  AND author_id = %s
                  AND created_at >= %s
                ORDER BY created_at ASC
                LIMIT %s
                """,
                (str(channel_id), str(user_id), cutoff, limit),
            )
            rows = cur.fetchall()

    lines = []
    for r in rows:
        if not r["content"]:
            continue
        name = r["author_name"] or r["author_id"]
        lines.append(f"user({name}): {r['content']}")
    return lines


def fetch_user_recent_in_guild(guild_id: int, user_id: int, minutes=240, limit=80):
    """Fallback if nothing in this channel; look across the whole server."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT channel_id, author_id, author_name, content, created_at
                FROM messages
                WHERE guild_id = %s
                  AND author_id = %s
                  AND created_at >= %s
                ORDER BY created_at ASC
                LIMIT %s
                """,
                (str(guild_id), str(user_id), cutoff, limit),
            )
            rows = cur.fetchall()

    lines = []
    for r in rows:
        if not r["content"]:
            continue
        name = r["author_name"] or r["author_id"]
        lines.append(f"user({name}) in #{r['channel_id']}: {r['content']}")
    return lines


def ensure_schema():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id           BIGSERIAL PRIMARY KEY,
                    message_id   TEXT NOT NULL,
                    channel_id   TEXT NOT NULL,
                    guild_id     TEXT,
                    author_id    TEXT NOT NULL,
                    author_name  TEXT,
                    content      TEXT,
                    is_bot       BOOLEAN NOT NULL DEFAULT FALSE,
                    reference_id TEXT,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_msgs_channel_time ON messages(channel_id, created_at)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_msgs_message_id ON messages(message_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_msgs_reference_id ON messages(reference_id)")


def _utcnow():
    return datetime.now(timezone.utc)


def log_message(msg: discord.Message):
    """Call this for every message in on_message before any returns."""
    ensure_schema()

    channel_id = str(msg.channel.id)
    guild_id = str(msg.guild.id) if msg.guild else None
    author_id = str(msg.author.id)
    author_name = getattr(msg.author, "display_name", None) or msg.author.name
    content = msg.content or ""
    is_bot = bool(msg.author.bot)

    ref_id: Optional[str] = None
    if msg.reference and msg.reference.message_id:
        ref_id = str(msg.reference.message_id)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO messages
                   (message_id, channel_id, guild_id, author_id, author_name, content, is_bot, reference_id, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (str(msg.id), channel_id, guild_id, author_id, author_name, content, is_bot, ref_id, _utcnow()),
            )


def fetch_recent_history_for_scope(message: discord.Message, limit=40, minutes=90):
    """
    Return recent messages in the same reply chain / thread / channel,
    including ones where the bot was NOT tagged. Oldest -> newest order.
    """
    ensure_schema()
    cutoff_dt = datetime.now(timezone.utc) - timedelta(minutes=minutes)

    channel_id = str(message.channel.id)

    sql = """
    SELECT author_id, author_name, content, is_bot, created_at
    FROM messages
    WHERE channel_id = %s
      AND created_at >= %s
    ORDER BY created_at ASC
    LIMIT %s
    """
    params = (channel_id, cutoff_dt, limit)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    lines = []
    for r in rows:
        if not r["content"]:
            continue
        role = "assistant" if r["is_bot"] else "user"
        name = r["author_name"] or r["author_id"]
        lines.append(f"{role}({name}): {r['content']}")
    return lines
