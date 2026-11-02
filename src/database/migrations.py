import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.database.connection import get_db_connection

logger = logging.getLogger(__name__)


def run_migrations(db_path: Optional[str] = None) -> int:
    """Discovers and executes unapplied SQL migrations in sequential order.

    Each migration runs within a database transaction. The version,
    UTC application timestamp, and SHA256 checksum are stored in `schema_migrations`.

    Args:
        db_path: Optional path to the SQLite database file or ':memory:'.

    Returns:
        int: Number of newly applied migrations.
    """
    migrations_dir = Path(__file__).resolve().parent / "migrations"
    if not migrations_dir.exists():
        logger.warning("Migrations directory not found at %s", migrations_dir)
        return 0

    migration_files = sorted(migrations_dir.glob("*.sql"))
    if not migration_files:
        logger.info("No migration files found in %s", migrations_dir)
        return 0

    conn = get_db_connection(db_path)
    applied_count = 0

    try:
        # Ensure schema_migrations table exists
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL,
                checksum TEXT NOT NULL
            );
            """
        )
        conn.commit()

        # Fetch existing applied migrations
        cursor = conn.cursor()
        cursor.execute("SELECT version, checksum FROM schema_migrations;")
        applied = {row["version"]: row["checksum"] for row in cursor.fetchall()}

        for migration_file in migration_files:
            version = migration_file.name
            content = migration_file.read_text(encoding="utf-8")
            checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()

            if version in applied:
                if applied[version] != checksum:
                    logger.warning(
                        "Migration %s checksum mismatch! Recorded: %s, File: %s",
                        version,
                        applied[version],
                        checksum,
                    )
                continue

            logger.info("Applying migration: %s", version)
            now_utc = datetime.now(timezone.utc).isoformat()

            try:
                conn.executescript(content)
                with conn:
                    conn.execute(
                        """
                        INSERT INTO schema_migrations (version, applied_at, checksum)
                        VALUES (?, ?, ?);
                        """,
                        (version, now_utc, checksum),
                    )
                applied_count += 1
            except Exception as e:
                logger.error("Failed to apply migration %s: %s", version, e)
                raise

        return applied_count
    finally:
        conn.close()


if __name__ == "__main__":
    count = run_migrations()
    print(f"Migrasi sukses, {count} file migrasi baru diterapkan.")
