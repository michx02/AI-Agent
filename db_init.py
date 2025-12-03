from db_postgres import get_connection


def create_tables():
    """
    Initialize PostgreSQL tables matching the prior SQLite schemas used for
    memory and message logging.
    """
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
        """,
        "CREATE INDEX IF NOT EXISTS idx_msgs_channel_time ON messages(channel_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_msgs_message_id ON messages(message_id)",
        "CREATE INDEX IF NOT EXISTS idx_msgs_reference_id ON messages(reference_id)",
    ]

    with get_connection() as conn:
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)
    print("PostgreSQL tables are ready.")
