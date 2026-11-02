import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from config.settings import TIMEZONE
from src.database.migrations import run_migrations
from src.domain.reminders import (
    cancel_reminder,
    create_reminder,
    get_due_reminders,
    get_reminder,
    mark_reminder_failed,
    mark_reminder_processing,
    mark_reminder_sent,
    snooze_reminder,
)


class TestReminderEngine(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_reminders.db")
        run_migrations(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_reminder_success_with_naive_and_aware_datetimes(self):
        # Naive datetime string: should assume Asia/Jakarta (UTC+7) and convert to UTC
        r_id1 = create_reminder(
            entity_type="task",
            entity_id=101,
            remind_at="2026-11-05 10:00:00",
            db_path=self.db_path,
        )
        self.assertIsInstance(r_id1, int)
        self.assertGreater(r_id1, 0)

        rem1 = get_reminder(r_id1, db_path=self.db_path)
        self.assertIsNotNone(rem1)
        self.assertEqual(rem1["id"], r_id1)
        self.assertEqual(rem1["entity_type"], "task")
        self.assertEqual(rem1["entity_id"], 101)
        # 10:00 WIB is 03:00 UTC
        self.assertTrue(rem1["remind_at"].endswith("+00:00") or rem1["remind_at"].endswith("Z"))
        self.assertIn("2026-11-05T03:00:00", rem1["remind_at"])
        self.assertEqual(rem1["status"], "pending")
        self.assertEqual(rem1["retry_count"], 0)
        self.assertIsNone(rem1["sent_at"])
        self.assertTrue(len(rem1["idempotency_key"]) > 0)

        # Explicit timezone string (+07:00)
        r_id2 = create_reminder(
            entity_type="task",
            entity_id=102,
            remind_at="2026-11-05T17:00:00+07:00",
            db_path=self.db_path,
        )
        rem2 = get_reminder(r_id2, db_path=self.db_path)
        # 17:00 WIB is 10:00 UTC
        self.assertIn("2026-11-05T10:00:00", rem2["remind_at"])

    def test_create_reminder_idempotency_custom_key(self):
        key = "custom-idem-key-999"
        r_id1 = create_reminder(
            entity_type="task",
            entity_id=1,
            remind_at="2026-11-05T10:00:00+07:00",
            idempotency_key=key,
            db_path=self.db_path,
        )
        # Duplicate invocation with the same key
        r_id2 = create_reminder(
            entity_type="task",
            entity_id=1,
            remind_at="2026-11-05T10:00:00+07:00",
            idempotency_key=key,
            db_path=self.db_path,
        )
        self.assertEqual(r_id1, r_id2)

        # Verify only 1 record exists in DB
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM reminders WHERE idempotency_key = ?;", (key,))
        count = cursor.fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)

    def test_create_reminder_idempotency_default_key(self):
        # When idempotency_key is None, default key should prevent duplicates
        r_id1 = create_reminder(
            entity_type="task",
            entity_id=25,
            remind_at="2026-11-06T09:00:00+07:00",
            db_path=self.db_path,
        )
        r_id2 = create_reminder(
            entity_type="task",
            entity_id=25,
            remind_at="2026-11-06T09:00:00+07:00",
            db_path=self.db_path,
        )
        self.assertEqual(r_id1, r_id2)

    def test_create_reminder_invalid_arguments(self):
        # Empty entity_type
        with self.assertRaises(ValueError):
            create_reminder("", 1, "2026-11-05T10:00:00+07:00", db_path=self.db_path)

        # Invalid entity_id
        with self.assertRaises(ValueError):
            create_reminder("task", "not-an-int", "2026-11-05T10:00:00+07:00", db_path=self.db_path)  # type: ignore

        with self.assertRaises(ValueError):
            create_reminder("task", True, "2026-11-05T10:00:00+07:00", db_path=self.db_path)  # type: ignore

        # Invalid remind_at datetime format
        with self.assertRaises(ValueError):
            create_reminder("task", 1, "invalid-datetime-string", db_path=self.db_path)

        with self.assertRaises(ValueError):
            create_reminder("task", 1, None, db_path=self.db_path)  # type: ignore

    def test_get_due_reminders_ordering_and_filters(self):
        # Create past, present, and future reminders
        # Base reference time: 2026-11-02T12:00:00+07:00 (05:00 UTC)
        r_past1 = create_reminder("task", 1, "2026-11-02T10:00:00+07:00", db_path=self.db_path)
        r_past2 = create_reminder("task", 2, "2026-11-02T11:00:00+07:00", db_path=self.db_path)
        r_exact = create_reminder("task", 3, "2026-11-02T12:00:00+07:00", db_path=self.db_path)
        r_future = create_reminder("task", 4, "2026-11-02T13:00:00+07:00", db_path=self.db_path)

        # Query due reminders at 12:00:00+07:00
        due = get_due_reminders(now_utc="2026-11-02T12:00:00+07:00", db_path=self.db_path)
        due_ids = [r["id"] for r in due]

        self.assertIn(r_past1, due_ids)
        self.assertIn(r_past2, due_ids)
        self.assertIn(r_exact, due_ids)
        self.assertNotIn(r_future, due_ids)

        # Check ascending chronological order
        self.assertEqual(due_ids, [r_past1, r_past2, r_exact])

        # Test limit
        due_limited = get_due_reminders(now_utc="2026-11-02T12:00:00+07:00", limit=2, db_path=self.db_path)
        self.assertEqual(len(due_limited), 2)
        self.assertEqual([r["id"] for r in due_limited], [r_past1, r_past2])

    def test_mark_reminder_sent_lifecycle(self):
        r_id = create_reminder("task", 10, "2026-11-02T08:00:00+07:00", db_path=self.db_path)
        
        # Mark as sent
        success = mark_reminder_sent(r_id, db_path=self.db_path)
        self.assertTrue(success)

        rem = get_reminder(r_id, db_path=self.db_path)
        self.assertEqual(rem["status"], "sent")
        self.assertIsNotNone(rem["sent_at"])

        # Calling mark_sent again should return False (already sent)
        self.assertFalse(mark_reminder_sent(r_id, db_path=self.db_path))

        # Sent reminders should not appear in due list
        due = get_due_reminders(now_utc="2026-11-02T12:00:00+07:00", db_path=self.db_path)
        self.assertNotIn(r_id, [r["id"] for r in due])

    def test_mark_reminder_failed_and_retry_threshold(self):
        r_id = create_reminder("task", 20, "2026-11-02T08:00:00+07:00", db_path=self.db_path)
        max_retries = 3

        # 1st failure: retry_count=1, remains pending
        success1 = mark_reminder_failed(r_id, max_retries=max_retries, db_path=self.db_path)
        self.assertTrue(success1)
        rem1 = get_reminder(r_id, db_path=self.db_path)
        self.assertEqual(rem1["retry_count"], 1)
        self.assertEqual(rem1["status"], "pending")

        # 2nd failure: retry_count=2, remains pending
        success2 = mark_reminder_failed(r_id, max_retries=max_retries, db_path=self.db_path)
        self.assertTrue(success2)
        rem2 = get_reminder(r_id, db_path=self.db_path)
        self.assertEqual(rem2["retry_count"], 2)
        self.assertEqual(rem2["status"], "pending")

        # 3rd failure: retry_count=3 >= max_retries, transitions to failed
        success3 = mark_reminder_failed(r_id, max_retries=max_retries, db_path=self.db_path)
        self.assertTrue(success3)
        rem3 = get_reminder(r_id, db_path=self.db_path)
        self.assertEqual(rem3["retry_count"], 3)
        self.assertEqual(rem3["status"], "failed")

        # 4th failure: already failed, should return False
        success4 = mark_reminder_failed(r_id, max_retries=max_retries, db_path=self.db_path)
        self.assertFalse(success4)

        # Failed reminders should not be due
        due = get_due_reminders(now_utc="2026-11-02T12:00:00+07:00", db_path=self.db_path)
        self.assertNotIn(r_id, [r["id"] for r in due])

    def test_mark_reminder_processing_and_failure_recovery(self):
        r_id = create_reminder("task", 30, "2026-11-02T08:00:00+07:00", db_path=self.db_path)

        # Transition pending -> processing
        self.assertTrue(mark_reminder_processing(r_id, db_path=self.db_path))
        rem = get_reminder(r_id, db_path=self.db_path)
        self.assertEqual(rem["status"], "processing")

        # While processing, should not appear in due list
        due = get_due_reminders(now_utc="2026-11-02T12:00:00+07:00", db_path=self.db_path)
        self.assertNotIn(r_id, [r["id"] for r in due])

        # If it fails, resets to pending so it can be retried
        self.assertTrue(mark_reminder_failed(r_id, max_retries=3, db_path=self.db_path))
        rem_after = get_reminder(r_id, db_path=self.db_path)
        self.assertEqual(rem_after["status"], "pending")
        self.assertEqual(rem_after["retry_count"], 1)

    def test_cancel_reminder(self):
        r_id = create_reminder("task", 40, "2026-11-02T08:00:00+07:00", db_path=self.db_path)

        # Cancel pending reminder
        self.assertTrue(cancel_reminder(r_id, db_path=self.db_path))
        rem = get_reminder(r_id, db_path=self.db_path)
        self.assertEqual(rem["status"], "cancelled")

        # Cancel again returns False
        self.assertFalse(cancel_reminder(r_id, db_path=self.db_path))

        # Cancelled reminder does not show in due reminders
        due = get_due_reminders(now_utc="2026-11-02T12:00:00+07:00", db_path=self.db_path)
        self.assertNotIn(r_id, [r["id"] for r in due])

        # Cannot mark cancelled reminder as sent
        self.assertFalse(mark_reminder_sent(r_id, db_path=self.db_path))

    def test_snooze_reminder_lifecycle(self):
        r_id = create_reminder("task", 50, "2026-11-02T08:00:00+07:00", db_path=self.db_path)

        # Mark sent
        mark_reminder_sent(r_id, db_path=self.db_path)
        rem_sent = get_reminder(r_id, db_path=self.db_path)
        self.assertEqual(rem_sent["status"], "sent")
        self.assertIsNotNone(rem_sent["sent_at"])

        # Snooze sent reminder to future time
        new_time = "2026-11-02T15:00:00+07:00"
        self.assertTrue(snooze_reminder(r_id, new_remind_at=new_time, db_path=self.db_path))

        rem_snoozed = get_reminder(r_id, db_path=self.db_path)
        self.assertEqual(rem_snoozed["status"], "pending")
        self.assertEqual(rem_snoozed["retry_count"], 0)
        self.assertIsNone(rem_snoozed["sent_at"])
        self.assertIn("2026-11-02T08:00:00", rem_snoozed["remind_at"])

        # Snoozing with invalid date raises ValueError
        with self.assertRaises(ValueError):
            snooze_reminder(r_id, "invalid-time", db_path=self.db_path)

        # Snoozing cancelled reminder returns False
        r_cancel = create_reminder("task", 51, "2026-11-02T08:00:00+07:00", db_path=self.db_path)
        cancel_reminder(r_cancel, db_path=self.db_path)
        self.assertFalse(snooze_reminder(r_cancel, "2026-11-02T16:00:00+07:00", db_path=self.db_path))

    def test_non_existent_reminder_operations(self):
        non_existent_id = 999999
        self.assertIsNone(get_reminder(non_existent_id, db_path=self.db_path))
        self.assertFalse(mark_reminder_sent(non_existent_id, db_path=self.db_path))
        self.assertFalse(mark_reminder_failed(non_existent_id, db_path=self.db_path))
        self.assertFalse(cancel_reminder(non_existent_id, db_path=self.db_path))
        self.assertFalse(snooze_reminder(non_existent_id, "2026-11-02T10:00:00", db_path=self.db_path))


if __name__ == "__main__":
    unittest.main()
