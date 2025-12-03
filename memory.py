from dotenv import load_dotenv  # pip install python-dotenv
import os
import time
import threading
from typing import Dict, Any, List

from google import genai

from db_postgres import get_connection

load_dotenv()  # reads .env in project root

YOUR_API_KEY = os.getenv("YOUR_API_KEY")
if not YOUR_API_KEY:
    raise RuntimeError("Missing MY_API_KEY")

client = genai.Client(api_key=YOUR_API_KEY)
MODEL = "gemini-2.5-flash"


class Memory:
    def __init__(self, max_chars: int = 6000):
        self.max_chars = max_chars
        self._lock = threading.RLock()
        self._init_schema()

    # ---------- schema ----------
    def _init_schema(self):
        statements = [
            """
            CREATE TABLE IF NOT EXISTS threads (
                thread_key TEXT PRIMARY KEY,
                summary    TEXT DEFAULT ''
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS turns (
                id         BIGSERIAL PRIMARY KEY,
                thread_key TEXT NOT NULL,
                role       TEXT NOT NULL,
                text       TEXT NOT NULL,
                ts         DOUBLE PRECISION NOT NULL,
                FOREIGN KEY(thread_key) REFERENCES threads(thread_key)
            )
            """,
            "CREATE INDEX IF NOT EXISTS turns_key_idx ON turns(thread_key, id)",
            """
            CREATE TABLE IF NOT EXISTS profiles (
                id      BIGSERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                fact    TEXT NOT NULL,
                ts      DOUBLE PRECISION NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS profiles_user_idx ON profiles(user_id, ts DESC)",
            """
            CREATE TABLE IF NOT EXISTS team_facts (
                id       BIGSERIAL PRIMARY KEY,
                guild_id TEXT NOT NULL,
                fact     TEXT NOT NULL,
                ts       DOUBLE PRECISION NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS team_facts_guild_idx ON team_facts(guild_id, ts DESC)",
        ]
        with get_connection() as conn:
            with conn.cursor() as cur:
                for stmt in statements:
                    cur.execute(stmt)

    # ---------- keying strategy ----------
    def _key(self, message) -> str:
        guild = message.guild.id if message.guild else "DM"
        return f"{guild}#{message.channel.id}#{message.author.id}"

    # ---------- thread ops ----------
    def get_thread(self, key: str) -> Dict[str, Any]:
        with self._lock, get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT summary FROM threads WHERE thread_key=%s",
                    (key,),
                )
                row = cur.fetchone()
                summary = row["summary"] if row else ""

                cur.execute(
                    "SELECT role, text, ts FROM turns WHERE thread_key=%s ORDER BY id ASC",
                    (key,),
                )
                turns = cur.fetchall()

        return {
            "summary": summary,
            "turns": [{"role": r["role"], "text": r["text"], "ts": r["ts"]} for r in turns],
        }

    def save_thread(self, key: str, thread: Dict[str, Any]):
        with self._lock, get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO threads(thread_key, summary) VALUES(%s, %s)
                    ON CONFLICT(thread_key) DO UPDATE SET summary=EXCLUDED.summary
                    """,
                    (key, thread.get("summary", "")),
                )

    def add_turn(self, key: str, role: str, text: str):
        now = time.time()
        with self._lock, get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO threads(thread_key, summary) VALUES(%s, '')
                    ON CONFLICT(thread_key) DO NOTHING
                    """,
                    (key,),
                )
                cur.execute(
                    "INSERT INTO turns(thread_key, role, text, ts) VALUES(%s, %s, %s, %s)",
                    (key, role, text, now),
                )
        self._trim_or_summarize(key)

    def _length_stats(self, key: str):
        with self._lock, get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(summary, '') AS summary FROM threads WHERE thread_key=%s",
                    (key,),
                )
                srow = cur.fetchone()
                summary = srow["summary"] if srow else ""
                cur.execute(
                    "SELECT id, text FROM turns WHERE thread_key=%s ORDER BY id ASC",
                    (key,),
                )
                trows = cur.fetchall()
        total_turn_chars = sum(len(r["text"]) for r in trows)
        return summary, trows, len(summary) + total_turn_chars

    def _trim_or_summarize(self, key: str):
        summary, trows, total = self._length_stats(key)
        if total <= self.max_chars:
            return

        if len(trows) > 6:
            cut = max(4, int(len(trows) * 0.7))
            older_ids = [r["id"] for r in trows[:cut]]

            placeholders = ",".join(["%s"] * len(older_ids))
            convo_text = ""
            with self._lock, get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT role, text FROM turns WHERE id IN ({placeholders}) ORDER BY id ASC",
                        older_ids,
                    )
                    older = cur.fetchall()
            if older:
                convo_text = "\n".join(f"{r['role'].capitalize()}: {r['text']}" for r in older)

            summary_add = summarize(convo_text, limit=800) if convo_text else ""

            with self._lock, get_connection() as conn:
                with conn.cursor() as cur:
                    new_summary = (summary + "\n" + summary_add).strip() if summary else summary_add
                    cur.execute(
                        "UPDATE threads SET summary=%s WHERE thread_key=%s",
                        (new_summary, key),
                    )
                    cur.execute(
                        f"DELETE FROM turns WHERE id IN ({placeholders})",
                        older_ids,
                    )

            summary2, trows2, total2 = self._length_stats(key)
            if total2 > self.max_chars and len(trows2) > 6:
                to_keep = {r["id"] for r in trows2[-6:]}
                to_del = [r["id"] for r in trows2 if r["id"] not in to_keep]
                if to_del:
                    placeholders = ",".join(["%s"] * len(to_del))
                    with self._lock, get_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                f"DELETE FROM turns WHERE id IN ({placeholders})",
                                to_del,
                            )
        else:
            to_keep = {r["id"] for r in trows[-6:]}
            to_del = [r["id"] for r in trows if r["id"] not in to_keep]
            if to_del:
                placeholders = ",".join(["%s"] * len(to_del))
                with self._lock, get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            f"DELETE FROM turns WHERE id IN ({placeholders})",
                            to_del,
                        )

    # ---------- long-term facts ----------
    def add_fact(self, user_id: int, fact: str, cap: int = 100):
        with self._lock, get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO profiles(user_id, fact, ts) VALUES(%s, %s, %s)",
                    (str(user_id), fact.strip(), time.time()),
                )
                cur.execute(
                    "SELECT id FROM profiles WHERE user_id=%s ORDER BY ts DESC",
                    (str(user_id),),
                )
                rows = cur.fetchall()
                if len(rows) > cap:
                    to_delete = [r["id"] for r in rows[cap:]]
                    placeholders = ",".join(["%s"] * len(to_delete))
                    cur.execute(
                        f"DELETE FROM profiles WHERE id IN ({placeholders})",
                        to_delete,
                    )

    def get_facts(self, user_id: int) -> List[str]:
        with self._lock, get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT fact FROM profiles WHERE user_id=%s ORDER BY ts DESC",
                    (str(user_id),),
                )
                rows = cur.fetchall()
        return [r["fact"] for r in rows]

    def add_team_fact(self, guild_id: int, fact: str, cap: int = 300):
        with self._lock, get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO team_facts(guild_id, fact, ts) VALUES(%s, %s, %s)",
                    (str(guild_id), fact.strip(), time.time()),
                )
                cur.execute(
                    "SELECT id FROM team_facts WHERE guild_id=%s ORDER BY ts DESC",
                    (str(guild_id),),
                )
                rows = cur.fetchall()
                if len(rows) > cap:
                    to_delete = [r["id"] for r in rows[cap:]]
                    placeholders = ",".join(["%s"] * len(to_delete))
                    cur.execute(
                        f"DELETE FROM team_facts WHERE id IN ({placeholders})",
                        to_delete,
                    )

    def get_team_facts(self, guild_id: int, limit: int = 50) -> List[str]:
        with self._lock, get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT fact
                    FROM team_facts
                    WHERE guild_id=%s
                    ORDER BY ts DESC
                    LIMIT %s
                    """,
                    (str(guild_id), limit),
                )
                rows = cur.fetchall()
        return [r["fact"] for r in rows]


def summarize(text: str, limit=800):
    resp = client.models.generate_content(
        model=MODEL,
        contents=(
            "Summarize the following conversation into factual, compact notes "
            f"(<= {limit} characters). Keep user goals/preferences and unresolved tasks.\n\n"
            f"{text}"
        ),
    )
    return (resp.text or "")[:limit]
