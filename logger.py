
# ----- ultra-minimal chat logger (SQLite, stdlib only) -----
import sqlite3
from datetime import timezone

LOG_DB_PATH = "discord_logs.sqlite3"

# create table once at startup
_conn = sqlite3.connect(LOG_DB_PATH, check_same_thread=False)
_cur = _conn.cursor()
_cur.execute("""
CREATE TABLE IF NOT EXISTS messages_simple (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id  INTEGER UNIQUE,
  author_id   INTEGER,
  username    TEXT,
  channel_id  INTEGER,
  channel     TEXT,
  created_at  TEXT,     -- UTC ISO8601
  content     TEXT
);
""")
_conn.commit()

def log_message(m):
    ts = m.created_at.replace(tzinfo=timezone.utc).isoformat()
    channel_name = getattr(m.channel, "name", None) or str(m.channel.id)
    username = getattr(m.author, "display_name", None) or getattr(m.author, "global_name", None) or m.author.name
    _cur.execute(
        "INSERT OR IGNORE INTO messages_simple (message_id, author_id, username, channel_id, channel, created_at, content) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (m.id, m.author.id, username, m.channel.id, channel_name, ts, m.content),
    )
    _conn.commit()
# -----------------------------------------------------------
