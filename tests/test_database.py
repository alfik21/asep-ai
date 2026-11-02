import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config.settings import DB_PATH, TIMEZONE
from src.database.connection import get_db_connection
from src.database.migrations import run_migrations


class TestDatabaseConnection(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_file = Path(self.temp_dir.name) / "test_asep.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_connection_pragmas(self):
        conn = get_db_connection(str(self.db_file))
        cursor = conn.cursor()

        # Check foreign keys pragma
        cursor.execute("PRAGMA foreign_keys;")
        foreign_keys = cursor.fetchone()[0]
        self.assertEqual(foreign_keys, 1, "foreign_keys PRAGMA must be ON (1)")

        # Check journal mode is WAL
        cursor.execute("PRAGMA journal_mode;")
        journal_mode = cursor.fetchone()[0]
        self.assertEqual(journal_mode.lower(), "wal", "journal_mode PRAGMA must be WAL")

        conn.close()

    def test_default_db_path_creation(self):
        # Test connecting using default path (db_path=None) creates parent dir and connects
        with patch("src.database.connection.DB_PATH", self.db_file):
            conn = get_db_connection()
            self.assertTrue(self.db_file.parent.exists())
            cursor = conn.cursor()
            cursor.execute("PRAGMA foreign_keys;")
            self.assertEqual(cursor.fetchone()[0], 1)
            conn.close()

    def test_foreign_key_enforcement(self):
        conn = get_db_connection(str(self.db_file))
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY);")
        cursor.execute(
            "CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id));"
        )
        conn.commit()

        # Inserting a foreign key pointing to non-existent parent must raise IntegrityError
        with self.assertRaises(sqlite3.IntegrityError):
            cursor.execute("INSERT INTO child (id, parent_id) VALUES (1, 999);")
            conn.commit()

        conn.close()

    def test_row_factory_dict_access(self):
        conn = get_db_connection(str(self.db_file))
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE dummy (id INTEGER, name TEXT);")
        cursor.execute("INSERT INTO dummy VALUES (1, 'Asep');")
        conn.commit()

        cursor.execute("SELECT id, name FROM dummy WHERE id = 1;")
        row = cursor.fetchone()
        self.assertEqual(row["name"], "Asep")
        self.assertEqual(row["id"], 1)
        conn.close()


class TestMigrations(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_file = Path(self.temp_dir.name) / "test_migrations.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_run_migrations_creates_all_tables_and_records(self):
        # Initial run: should apply 1 migration
        applied_count = run_migrations(str(self.db_file))
        self.assertEqual(applied_count, 1)

        conn = get_db_connection(str(self.db_file))
        cursor = conn.cursor()

        # Verify all 10 expected tables exist
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
        )
        tables = {row[0] for row in cursor.fetchall()}
        expected_tables = {
            "schema_migrations",
            "categories",
            "tasks",
            "transactions",
            "notes",
            "reminders",
            "daily_reviews",
            "installed_features",
            "feature_requests",
            "audit_logs",
        }
        self.assertTrue(
            expected_tables.issubset(tables),
            f"Missing tables: {expected_tables - tables}",
        )

        # Verify schema_migrations record
        cursor.execute("SELECT version, applied_at, checksum FROM schema_migrations;")
        migrations = cursor.fetchall()
        self.assertEqual(len(migrations), 1)
        row = migrations[0]
        self.assertTrue("001_initial_schema" in row["version"])
        self.assertIsNotNone(row["applied_at"])
        self.assertTrue(len(row["checksum"]) > 0)

        conn.close()

    def test_run_migrations_idempotency(self):
        # First run applies 1
        count1 = run_migrations(str(self.db_file))
        self.assertEqual(count1, 1)

        # Second run should apply 0
        count2 = run_migrations(str(self.db_file))
        self.assertEqual(count2, 0)

    def test_column_definitions_for_all_tables(self):
        run_migrations(str(self.db_file))
        conn = get_db_connection(str(self.db_file))
        cursor = conn.cursor()

        expected_columns = {
            "schema_migrations": {"version", "applied_at", "checksum"},
            "categories": {"id", "domain", "name", "is_active"},
            "tasks": {"id", "title", "description", "due_at", "priority", "status", "created_at", "updated_at"},
            "transactions": {"id", "type", "amount_idr", "category_id", "description", "occurred_at", "payment_method", "status", "created_at"},
            "notes": {"id", "title", "content", "category_id", "tags_json", "created_at", "updated_at", "archived_at"},
            "reminders": {"id", "entity_type", "entity_id", "remind_at", "status", "idempotency_key", "sent_at", "retry_count"},
            "daily_reviews": {"id", "review_date", "summary_json", "generated_at", "delivery_status"},
            "installed_features": {"id", "name", "version", "status", "manifest_json", "installed_at"},
            "feature_requests": {"id", "request_text", "proposal_json", "status", "created_at", "approved_at"},
            "audit_logs": {"id", "actor", "action", "entity_type", "entity_id", "timestamp", "result"},
        }

        for table, expected_cols in expected_columns.items():
            cursor.execute(f"PRAGMA table_info({table});")
            actual_cols = {col["name"] for col in cursor.fetchall()}
            self.assertTrue(
                expected_cols.issubset(actual_cols),
                f"Table {table} is missing columns: {expected_cols - actual_cols}",
            )

        conn.close()

    def test_schema_constraints_and_foreign_keys(self):
        run_migrations(str(self.db_file))
        conn = get_db_connection(str(self.db_file))
        cursor = conn.cursor()

        # 1. Foreign key on transactions -> categories
        with self.assertRaises(sqlite3.IntegrityError):
            cursor.execute(
                """
                INSERT INTO transactions (type, amount_idr, category_id, occurred_at, created_at)
                VALUES ('expense', 50000, 9999, '2026-11-02T12:00:00Z', '2026-11-02T12:00:00Z');
                """
            )
            conn.commit()

        # 2. Foreign key on notes -> categories
        with self.assertRaises(sqlite3.IntegrityError):
            cursor.execute(
                """
                INSERT INTO notes (title, content, category_id, created_at, updated_at)
                VALUES ('Test', 'Content', 9999, '2026-11-02T12:00:00Z', '2026-11-02T12:00:00Z');
                """
            )
            conn.commit()

        # 3. Unique constraint on installed_features.name
        cursor.execute(
            """
            INSERT INTO installed_features (name, version, manifest_json, installed_at)
            VALUES ('calc', '1.0.0', '{}', '2026-11-02T12:00:00Z');
            """
        )
        conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            cursor.execute(
                """
                INSERT INTO installed_features (name, version, manifest_json, installed_at)
                VALUES ('calc', '1.0.1', '{}', '2026-11-02T12:05:00Z');
                """
            )
            conn.commit()

        # 4. Unique constraint on reminders.idempotency_key
        cursor.execute(
            """
            INSERT INTO reminders (entity_type, entity_id, remind_at, idempotency_key)
            VALUES ('task', 1, '2026-11-02T13:00:00Z', 'unique-key-1');
            """
        )
        conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            cursor.execute(
                """
                INSERT INTO reminders (entity_type, entity_id, remind_at, idempotency_key)
                VALUES ('task', 2, '2026-11-02T14:00:00Z', 'unique-key-1');
                """
            )
            conn.commit()

        conn.close()


if __name__ == "__main__":
    unittest.main()
