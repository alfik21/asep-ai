import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.database.connection import get_db_connection
from src.database.migrations import run_migrations


class TestSystemHealthAndAudit(unittest.TestCase):
    """Test suite for system health checks, audit logging, and secret redaction."""

    def setUp(self):
        """Create a temporary database for isolated testing."""
        self.test_dir = tempfile.mkdtemp()
        self.db_path = str(Path(self.test_dir) / "test_health.db")
        run_migrations(self.db_path)

    def tearDown(self):
        """Clean up the temporary directory."""
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_redact_sensitive_text_api_keys(self):
        from src.system.health import redact_sensitive_text

        raw_text = "Config loaded with key sk-abcdef1234567890abcdef123456 and backup AIzaSyD8Gj9XkL2m1NpQrStUvWxYz0123456789"
        redacted = redact_sensitive_text(raw_text)

        self.assertNotIn("sk-abcdef1234567890abcdef123456", redacted)
        self.assertNotIn("AIzaSyD8Gj9XkL2m1NpQrStUvWxYz0123456789", redacted)
        self.assertIn("[REDACTED", redacted)

    def test_redact_sensitive_text_bearer_tokens(self):
        from src.system.health import redact_sensitive_text

        raw_text = "Authorization: Bearer secret-jwt-token-string-value-12345"
        redacted = redact_sensitive_text(raw_text)

        self.assertNotIn("secret-jwt-token-string-value-12345", redacted)
        self.assertIn("Bearer [REDACTED", redacted)

    def test_redact_sensitive_text_passwords_and_secrets(self):
        from src.system.health import redact_sensitive_text

        raw_text = "Connecting with password=my_super_secret_pwd and secret: 'topsecretkey123'"
        redacted = redact_sensitive_text(raw_text)

        self.assertNotIn("my_super_secret_pwd", redacted)
        self.assertNotIn("topsecretkey123", redacted)
        self.assertIn("[REDACTED", redacted)

    def test_redact_sensitive_text_phone_numbers(self):
        from src.system.health import redact_sensitive_text

        raw_text = "Contact 081234567890 or +6281987654321 or intl +14155552671 about task 100 on 2026-11-02."
        redacted = redact_sensitive_text(raw_text)

        self.assertNotIn("081234567890", redacted)
        self.assertNotIn("+6281987654321", redacted)
        self.assertNotIn("+14155552671", redacted)
        # Normal content preserved
        self.assertIn("task 100", redacted)
        self.assertIn("2026-11-02", redacted)

    def test_redact_sensitive_text_none_and_empty(self):
        from src.system.health import redact_sensitive_text

        self.assertEqual(redact_sensitive_text(None), "")
        self.assertEqual(redact_sensitive_text(""), "")
        self.assertEqual(redact_sensitive_text("Clean normal string"), "Clean normal string")

    def test_log_audit_action_and_get_recent_logs(self):
        from src.system.health import get_recent_audit_logs, log_audit_action

        log_id1 = log_audit_action(
            actor="owner",
            action="create_task",
            entity_type="task",
            entity_id="1",
            result="success",
            db_path=self.db_path,
        )
        self.assertIsInstance(log_id1, int)
        self.assertGreater(log_id1, 0)

        log_id2 = log_audit_action(
            actor="system",
            action="daily_briefing",
            result="sent",
            db_path=self.db_path,
        )
        self.assertGreater(log_id2, log_id1)

        logs = get_recent_audit_logs(limit=10, db_path=self.db_path)
        self.assertEqual(len(logs), 2)
        # Verify ordering (newest first)
        self.assertEqual(logs[0]["id"], log_id2)
        self.assertEqual(logs[0]["actor"], "system")
        self.assertEqual(logs[0]["action"], "daily_briefing")
        self.assertEqual(logs[1]["id"], log_id1)
        self.assertEqual(logs[1]["entity_type"], "task")
        self.assertEqual(logs[1]["entity_id"], "1")

    def test_log_audit_action_redacts_sensitive_data(self):
        from src.system.health import get_recent_audit_logs, log_audit_action

        sensitive_action = "update_key with sk-12345678901234567890123456 and phone 081234567899"
        log_id = log_audit_action(
            actor="owner",
            action=sensitive_action,
            entity_type="system",
            result="failed with password=my_db_password",
            db_path=self.db_path,
        )

        logs = get_recent_audit_logs(limit=1, db_path=self.db_path)
        self.assertEqual(len(logs), 1)
        log_entry = logs[0]
        self.assertNotIn("sk-12345678901234567890123456", log_entry["action"])
        self.assertNotIn("081234567899", log_entry["action"])
        self.assertNotIn("my_db_password", log_entry["result"])
        self.assertIn("[REDACTED", log_entry["action"])
        self.assertIn("[REDACTED", log_entry["result"])

    def test_get_system_health_metrics(self):
        from src.system.health import get_system_health

        health = get_system_health(db_path=self.db_path)

        self.assertIn("status", health)
        self.assertIn("timestamp", health)
        self.assertIn("os", health)
        self.assertIn("memory", health)
        self.assertIn("disk", health)
        self.assertIn("database", health)

        # OS metrics
        self.assertIn("platform", health["os"])
        self.assertIn("cpu_cores", health["os"])
        self.assertGreaterEqual(health["os"]["cpu_cores"], 1)

        # Memory metrics
        mem = health["memory"]
        self.assertIn("total_mb", mem)
        self.assertIn("used_mb", mem)
        self.assertIn("free_mb", mem)
        self.assertIn("percent", mem)
        self.assertGreaterEqual(mem["total_mb"], 0)

        # Disk metrics
        disk = health["disk"]
        self.assertIn("total_gb", disk)
        self.assertIn("used_gb", disk)
        self.assertIn("free_gb", disk)
        self.assertIn("percent", disk)
        self.assertGreater(disk["total_gb"], 0)

        # Database metrics
        db = health["database"]
        self.assertEqual(db["status"], "ok")
        self.assertEqual(db["integrity"], "ok")
        self.assertGreaterEqual(db["table_count"], 5)
        self.assertEqual(db["pending_reminders"], 0)
        self.assertEqual(db["active_features"], 0)

    def test_get_system_health_counts_reminders_and_features(self):
        from src.system.health import get_system_health

        conn = get_db_connection(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO reminders (entity_type, entity_id, remind_at, status)
                VALUES ('task', 1, '2026-11-02T10:00:00Z', 'pending'),
                       ('task', 2, '2026-11-02T12:00:00Z', 'completed');
                """
            )
            conn.execute(
                """
                INSERT INTO installed_features (name, version, status, manifest_json, installed_at)
                VALUES ('custom_weather', '1.0.0', 'active', '{}', '2026-11-02T10:00:00Z'),
                       ('disabled_tool', '0.5.0', 'inactive', '{}', '2026-11-02T10:00:00Z');
                """
            )
            conn.commit()
        finally:
            conn.close()

        health = get_system_health(db_path=self.db_path)
        self.assertEqual(health["database"]["pending_reminders"], 1)
        self.assertEqual(health["database"]["active_features"], 1)

    def test_format_system_health(self):
        from src.system.health import format_system_health

        mock_health = {
            "status": "healthy",
            "timestamp": "2026-11-02T12:00:00Z",
            "os": {
                "platform": "linux",
                "cpu_cores": 2,
                "load_avg": [0.15, 0.25, 0.10],
            },
            "memory": {
                "total_mb": 1024.0,
                "used_mb": 450.0,
                "free_mb": 574.0,
                "percent": 43.9,
            },
            "disk": {
                "total_gb": 25.0,
                "used_gb": 5.5,
                "free_gb": 19.5,
                "percent": 22.0,
            },
            "database": {
                "status": "ok",
                "integrity": "ok",
                "path": "/data/asep.db",
                "size_bytes": 102400,
                "size_kb": 100.0,
                "size_mb": 0.1,
                "table_count": 9,
                "pending_reminders": 3,
                "active_features": 1,
            },
        }

        output = format_system_health(mock_health)
        self.assertIn("Status Kesehatan Sistem", output)
        self.assertIn("Sehat (Normal)", output)
        self.assertIn("RAM:", output)
        self.assertIn("450.0 MB / 1024.0 MB (43.9%)", output)
        self.assertIn("Penyimpanan (Disk):", output)
        self.assertIn("5.5 GB / 25.0 GB (22.0%)", output)
        self.assertIn("Database SQLite:", output)
        self.assertIn("Integritas: OK", output)
        self.assertIn("Jumlah Tabel: 9", output)
        self.assertIn("Pengingat Tertunda: 3", output)
        self.assertIn("Fitur Aktif: 1", output)


if __name__ == "__main__":
    unittest.main()
