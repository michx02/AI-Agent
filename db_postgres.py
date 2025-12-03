import os
import psycopg2
from psycopg2.extras import RealDictCursor


def get_database_url() -> str:
    """
    Read the PostgreSQL connection string from the environment.
    Adjust the name if your variable is not DATABASE_URL.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return url


def get_connection():
    """
    Open a new connection to the PostgreSQL database.
    RealDictCursor lets you get rows as dictionaries if you want that.
    """
    url = get_database_url()
    return psycopg2.connect(url, cursor_factory=RealDictCursor)
