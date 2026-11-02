import sqlite3
from pathlib import Path
from typing import Optional

from config.settings import BUSY_TIMEOUT_MS, DB_PATH


def get_db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Creates and configures an SQLite database connection.

    Configured with WAL mode, foreign keys enforcement, busy timeout,
    and sqlite3.Row row factory for dict-like access.
    """
    target_path = str(DB_PATH) if db_path is None else str(db_path)

    if target_path != ":memory:":
        parent_dir = Path(target_path).parent
        parent_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(
        target_path,
        timeout=BUSY_TIMEOUT_MS / 1000.0,
    )

    conn.row_factory = sqlite3.Row

    # Enforce SQLite performance and integrity constraints
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS};")
    conn.execute("PRAGMA synchronous = NORMAL;")

    return conn
