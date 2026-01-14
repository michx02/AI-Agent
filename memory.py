from dotenv import load_dotenv  # pip install python-dotenv
import os
import time
import threading
import json
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
        self._user_fact_buffers: Dict[str, List[str]] = {}
        self._guild_fact_buffers: Dict[str, List[str]] = {}
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

    # ---------- auto-fact extraction ----------
    def update_facts_from_text(self, user_id: int, guild_id: int | None, text: str):
        facts = extract_facts(text)
        if not facts:
            return
        for item in facts:
            fact_type = item.get("type")
            fact_text = (item.get("fact") or "").strip()
            if not fact_text:
                continue
            if fact_type == "team" and guild_id:
                self._add_team_fact_unique(guild_id, fact_text)
            elif fact_type == "user":
                self._add_fact_unique(user_id, fact_text)

    def record_message_for_facts(self, user_id: int, guild_id: int | None, text: str, batch_size: int = 30):
        cleaned = (text or "").strip()
        if not cleaned:
            return

        user_key = str(user_id)
        user_buf = self._user_fact_buffers.setdefault(user_key, [])
        user_buf.append(cleaned)
        if len(user_buf) >= batch_size:
            batch_text = "\n".join(user_buf[-batch_size:])
            facts = extract_facts(batch_text)
            for item in facts:
                if item.get("type") == "user":
                    fact_text = (item.get("fact") or "").strip()
                    if fact_text:
                        self._add_fact_unique(user_id, fact_text)
            self._user_fact_buffers[user_key] = []

        if guild_id is None:
            return
        guild_key = str(guild_id)
        guild_buf = self._guild_fact_buffers.setdefault(guild_key, [])
        guild_buf.append(cleaned)
        if len(guild_buf) >= batch_size:
            batch_text = "\n".join(guild_buf[-batch_size:])
            facts = extract_facts(batch_text)
            for item in facts:
                if item.get("type") == "team":
                    fact_text = (item.get("fact") or "").strip()
                    if fact_text:
                        self._add_team_fact_unique(guild_id, fact_text)
            self._guild_fact_buffers[guild_key] = []

    def _add_fact_unique(self, user_id: int, fact: str, cap: int = 100):
        with self._lock, get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM profiles WHERE user_id=%s AND fact=%s LIMIT 1",
                    (str(user_id), fact),
                )
                if cur.fetchone():
                    return
        self.add_fact(user_id, fact, cap=cap)

    def _add_team_fact_unique(self, guild_id: int, fact: str, cap: int = 300):
        with self._lock, get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM team_facts WHERE guild_id=%s AND fact=%s LIMIT 1",
                    (str(guild_id), fact),
                )
                if cur.fetchone():
                    return
        self.add_team_fact(guild_id, fact, cap=cap)


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


def extract_facts(text: str) -> List[dict]:
    prompt = (
        "Extract durable facts and preferences from the text. "
        "Return a JSON array of objects with fields: "
        '{"type":"user|team","fact":"..."}.\n'
        "Rules: Only include facts likely to remain true or useful; "
        "avoid temporary details, dates, or one-off messages. "
        "Prefer short sentences. If no facts, return [].\n\n"
        f"Text:\n{text}\n"
    )
    resp = client.models.generate_content(
        model=MODEL,
        contents=prompt,
    )
    raw = (resp.text or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Try to salvage a JSON array if the model wrapped it in text.
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end == -1 or end < start:
            return []
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []
