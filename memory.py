

# ---- SQLite-backed Memory for Discord+Gemini bots ----
# pip install google-genai discord.py
import os, time, sqlite3, threading
from google import genai
from typing import Dict, Any
from dotenv import load_dotenv  # pip install python-dotenv
import os


load_dotenv()  # reads .env in project root

YOUR_API_KEY = os.getenv("YOUR_API_KEY")
if not YOUR_API_KEY:
    raise RuntimeError("Missing MY_API_KEY")

DB_PATH = os.environ.get("BOT_MEMORY_DB", "memory.sqlite3")

client = genai.Client(api_key = YOUR_API_KEY)
MODEL = "gemini-2.5-flash"


class Memory:
    def __init__(self, path: str = DB_PATH, max_chars: int = 6000):
        self.path = path
        self.max_chars = max_chars
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._init_schema()

    # ---------- schema ----------
    def _init_schema(self):
        with self._conn:
            self._conn.execute("""
            CREATE TABLE IF NOT EXISTS threads (
                thread_key TEXT PRIMARY KEY,
                summary    TEXT DEFAULT ''
            )""")
            self._conn.execute("""
            CREATE TABLE IF NOT EXISTS turns (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_key TEXT NOT NULL,
                role       TEXT NOT NULL,
                text       TEXT NOT NULL,
                ts         REAL NOT NULL,
                FOREIGN KEY(thread_key) REFERENCES threads(thread_key)
            )""")
            self._conn.execute("""
            CREATE INDEX IF NOT EXISTS turns_key_idx
            ON turns(thread_key, id)
            """)
            self._conn.execute("""
            CREATE TABLE IF NOT EXISTS profiles (
                user_id TEXT NOT NULL,
                fact    TEXT NOT NULL,
                ts      REAL NOT NULL
            )""")
            self._conn.execute("""
            CREATE INDEX IF NOT EXISTS profiles_user_idx
            ON profiles(user_id, ts DESC)
            """)
            # ---- NEW: team facts (guild-scoped) ----
            self._conn.execute("""
            CREATE TABLE IF NOT EXISTS team_facts (
                guild_id TEXT NOT NULL,
                fact     TEXT NOT NULL,
                ts       REAL NOT NULL
            )
            """)
            self._conn.execute("""
            CREATE INDEX IF NOT EXISTS team_facts_guild_idx
            ON team_facts(guild_id, ts DESC)
            """)


    # ---------- keying strategy ----------
    def _key(self, message) -> str:
        # per-channel + per-user memory; adjust if you prefer global per-user or per-channel
        guild = message.guild.id if message.guild else "DM"
        return f"{guild}#{message.channel.id}#{message.author.id}"

    # ---------- thread ops ----------
    def get_thread(self, key: str) -> Dict[str, Any]:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT summary FROM threads WHERE thread_key=?",
                (key,)
            ).fetchone()
            summary = row[0] if row else ""
            turns = self._conn.execute(
                "SELECT role, text, ts FROM turns WHERE thread_key=? ORDER BY id ASC",
                (key,)
            ).fetchall()
        return {
            "summary": summary,
            "turns": [{"role": r, "text": t, "ts": ts} for (r, t, ts) in turns]
        }

    def save_thread(self, key: str, thread: Dict[str, Any]):
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO threads(thread_key, summary) VALUES(?, ?) "
                "ON CONFLICT(thread_key) DO UPDATE SET summary=excluded.summary",
                (key, thread.get("summary", ""))
            )

    def add_turn(self, key: str, role: str, text: str):
        now = time.time()
        with self._lock, self._conn:
            # ensure thread exists
            self._conn.execute(
                "INSERT INTO threads(thread_key, summary) VALUES(?, '') "
                "ON CONFLICT(thread_key) DO NOTHING",
                (key,)
            )
            # insert turn
            self._conn.execute(
                "INSERT INTO turns(thread_key, role, text, ts) VALUES(?, ?, ?, ?)",
                (key, role, text, now)
            )
        # manage size after inserting
        self._trim_or_summarize(key)

    def _length_stats(self, key: str):
        with self._lock, self._conn:
            srow = self._conn.execute(
                "SELECT COALESCE(summary, '') FROM threads WHERE thread_key=?",
                (key,)
            ).fetchone()
            summary = srow[0] if srow else ""
            trows = self._conn.execute(
                "SELECT id, text FROM turns WHERE thread_key=?",
                (key,)
            ).fetchall()
        total_turn_chars = sum(len(t) for (_id, t) in trows)
        return summary, trows, len(summary) + total_turn_chars

    def _trim_or_summarize(self, key: str):
        summary, trows, total = self._length_stats(key)
        if total <= self.max_chars:
            return
        # if many turns, summarize oldest ~70%, keep newest ~30%
        if len(trows) > 6:
            cut = max(4, int(len(trows) * 0.7))
            older_ids = [tid for (tid, _t) in trows[:cut]]
            newer_ids = [tid for (tid, _t) in trows[cut:]]

            # build text to summarize from older chunk
            with self._lock, self._conn:
                older = self._conn.execute(
                    "SELECT role, text FROM turns WHERE id IN (%s) ORDER BY id ASC"
                    % ",".join("?" * len(older_ids)),
                    older_ids
                ).fetchall()
            convo_text = "\n".join(f"{r.capitalize()}: {t}" for (r, t) in older)

            # ---- call your summarize() helper (defined below in your codebase) ----
            summary_add = summarize(convo_text, limit=800)

            # update DB: set new summary, delete older rows we summarized
            with self._lock, self._conn:
                new_summary = (summary + "\n" + summary_add).strip() if summary else summary_add
                self._conn.execute(
                    "UPDATE threads SET summary=? WHERE thread_key=?",
                    (new_summary, key)
                )
                self._conn.execute(
                    "DELETE FROM turns WHERE id IN (%s)" % ",".join("?" * len(older_ids)),
                    older_ids
                )

            # if still too long, hard-trim to last 6 turns
            summary2, trows2, total2 = self._length_stats(key)
            if total2 > self.max_chars and len(trows2) > 6:
                to_keep = [tid for (tid, _t) in trows2[-6:]]
                to_del  = [tid for (tid, _t) in trows2 if tid not in to_keep]
                with self._lock, self._conn:
                    if to_del:
                        self._conn.execute(
                            "DELETE FROM turns WHERE id IN (%s)" % ",".join("?" * len(to_del)),
                            to_del
                        )
        else:
            # few turns: keep last 6
            to_keep = [tid for (tid, _t) in trows[-6:]]
            to_del  = [tid for (tid, _t) in trows if tid not in to_keep]
            with self._lock, self._conn:
                if to_del:
                    self._conn.execute(
                        "DELETE FROM turns WHERE id IN (%s)" % ",".join("?" * len(to_del)),
                        [tid for (tid, _t) in to_del]
                    )

    # ---------- long-term facts ----------
    def add_fact(self, user_id: int, fact: str, cap: int = 100):
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO profiles(user_id, fact, ts) VALUES(?, ?, ?)",
                (str(user_id), fact.strip(), time.time())
            )
            # cap facts per user
            rows = self._conn.execute(
                "SELECT rowid FROM profiles WHERE user_id=? ORDER BY ts DESC",
                (str(user_id),)
            ).fetchall()
            if len(rows) > cap:
                to_delete = [r[0] for r in rows[cap:]]
                self._conn.execute(
                    "DELETE FROM profiles WHERE rowid IN (%s)" % ",".join("?" * len(to_delete)),
                    to_delete
                )

    def get_facts(self, user_id: int):
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT fact FROM profiles WHERE user_id=? ORDER BY ts DESC",
                (str(user_id),)
            ).fetchall()
        return [r[0] for r in rows]
    
    def add_team_fact(self, guild_id: int, fact: str, cap: int = 300):
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO team_facts(guild_id, fact, ts) VALUES(?, ?, ?)",
                (str(guild_id), fact.strip(), time.time())
            )
            # cap per guild to avoid unbounded growth
            rows = self._conn.execute(
                "SELECT rowid FROM team_facts WHERE guild_id=? ORDER BY ts DESC",
                (str(guild_id),)
            ).fetchall()
            if len(rows) > cap:
                to_delete = [r[0] for r in rows[cap:]]
                qmarks = ",".join("?" * len(to_delete))
                self._conn.execute(f"DELETE FROM team_facts WHERE rowid IN ({qmarks})", to_delete)

    def get_team_facts(self, guild_id: int, limit: int = 50):
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT fact FROM team_facts WHERE guild_id=? ORDER BY ts DESC LIMIT ?",
                (str(guild_id), limit)
            ).fetchall()
        return [r[0] for r in rows]





#HELPER Functions:


def summarize(text: str, limit=800):
    # Use Gemini (or any LLM) to compress older turns into notes
    resp = client.models.generate_content(
        model=MODEL,
        contents=(
            "Summarize the following conversation into factual, compact notes "
            f"(<= {limit} characters). Keep user goals/preferences and unresolved tasks.\n\n"
            f"{text}"
        )
    )
    return (resp.text or "")[:limit]

'''

def build_prompt(user_id: int, thread):
    facts = memory.get_facts(user_id)
    recent = "\n".join(f"{t['role'].capitalize()}: {t['text']}" for t in thread["turns"])
    preface = (
        "You are a helpful assistant for a Discord server.\n"
        "Use long-term facts if relevant; be concise.\n\n"
    )
    profile = ("Known user facts:\n- " + "\n- ".join(facts) + "\n\n") if facts else ""
    summary = (f"Conversation summary so far:\n{thread['summary']}\n\n") if thread.get("summary") else ""
    return preface + profile + summary + "Recent messages:\n" + recent + "\nAssistant:"
'''