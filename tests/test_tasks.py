import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from config.settings import TIMEZONE
from src.database.migrations import run_migrations
from src.domain.tasks import (
    create_task,
    get_task,
    list_tasks,
    mark_task_status,
    update_task,
)


class TestTaskManagerDomain(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_tasks.db")
        # Run schema migrations to initialize tables including `tasks`
        run_migrations(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_create_task_success(self):
        task_id = create_task(
            title="Selesaikan Laporan Pajak",
            description="Laporan PPh 21 bulanan",
            due_at="2026-11-05T17:00:00+07:00",
            priority="high",
            db_path=self.db_path,
        )
        self.assertIsInstance(task_id, int)
        self.assertGreater(task_id, 0)

        task = get_task(task_id, db_path=self.db_path)
        self.assertIsNotNone(task)
        self.assertEqual(task["id"], task_id)
        self.assertEqual(task["title"], "Selesaikan Laporan Pajak")
        self.assertEqual(task["description"], "Laporan PPh 21 bulanan")
        # Check stored timestamp is ISO-8601 UTC
        self.assertTrue(task["due_at"].endswith("+00:00") or task["due_at"].endswith("Z"))
        self.assertEqual(task["priority"], "high")
        self.assertEqual(task["status"], "open")
        self.assertIsNotNone(task["created_at"])
        self.assertIsNotNone(task["updated_at"])

    def test_create_task_default_values(self):
        task_id = create_task(
            title="Beli Kopi",
            db_path=self.db_path,
        )
        task = get_task(task_id, db_path=self.db_path)
        self.assertEqual(task["priority"], "normal")
        self.assertEqual(task["status"], "open")
        self.assertIsNone(task["description"])
        self.assertIsNone(task["due_at"])

    def test_create_task_validation_title(self):
        with self.assertRaises(ValueError):
            create_task(title="", db_path=self.db_path)

        with self.assertRaises(ValueError):
            create_task(title="   ", db_path=self.db_path)

        with self.assertRaises(ValueError):
            create_task(title=None, db_path=self.db_path)

    def test_create_task_validation_priority(self):
        with self.assertRaises(ValueError):
            create_task(title="Task invalid priority", priority="super_urgent", db_path=self.db_path)

    def test_create_task_validation_due_at(self):
        with self.assertRaises(ValueError):
            create_task(title="Task invalid due_at", due_at="invalid-date-format", db_path=self.db_path)

    def test_get_task_not_found(self):
        task = get_task(99999, db_path=self.db_path)
        self.assertIsNone(task)

    def test_list_tasks_filter_by_status(self):
        t1 = create_task(title="Task 1", db_path=self.db_path)
        t2 = create_task(title="Task 2", db_path=self.db_path)
        t3 = create_task(title="Task 3", db_path=self.db_path)

        mark_task_status(t2, status="done", db_path=self.db_path)
        mark_task_status(t3, status="cancelled", db_path=self.db_path)

        open_tasks = list_tasks(status="open", db_path=self.db_path)
        self.assertEqual(len(open_tasks), 1)
        self.assertEqual(open_tasks[0]["id"], t1)

        done_tasks = list_tasks(status="done", db_path=self.db_path)
        self.assertEqual(len(done_tasks), 1)
        self.assertEqual(done_tasks[0]["id"], t2)

        cancelled_tasks = list_tasks(status="cancelled", db_path=self.db_path)
        self.assertEqual(len(cancelled_tasks), 1)
        self.assertEqual(cancelled_tasks[0]["id"], t3)

        all_tasks = list_tasks(status="all", db_path=self.db_path)
        self.assertEqual(len(all_tasks), 3)

        with self.assertRaises(ValueError):
            list_tasks(status="unknown_status", db_path=self.db_path)

    def test_list_tasks_filter_by_due_date_asia_jakarta(self):
        # In Asia/Jakarta (UTC+7):
        # 2026-11-02 01:00:00 local is 2026-11-01 18:00:00 UTC
        # 2026-11-02 23:00:00 local is 2026-11-02 16:00:00 UTC
        # 2026-11-03 01:00:00 local is 2026-11-02 18:00:00 UTC (Next day)
        t_target_morning = create_task(
            title="Target Morning",
            due_at="2026-11-02T01:00:00+07:00",
            db_path=self.db_path,
        )
        t_target_evening = create_task(
            title="Target Evening",
            due_at="2026-11-02T23:00:00+07:00",
            db_path=self.db_path,
        )
        t_next_day = create_task(
            title="Next Day",
            due_at="2026-11-03T01:00:00+07:00",
            db_path=self.db_path,
        )
        t_prev_day = create_task(
            title="Previous Day",
            due_at="2026-11-01T23:59:00+07:00",
            db_path=self.db_path,
        )

        tasks_on_nov2 = list_tasks(status="open", due_date="2026-11-02", db_path=self.db_path)
        retrieved_ids = [t["id"] for t in tasks_on_nov2]

        self.assertIn(t_target_morning, retrieved_ids)
        self.assertIn(t_target_evening, retrieved_ids)
        self.assertNotIn(t_next_day, retrieved_ids)
        self.assertNotIn(t_prev_day, retrieved_ids)

    def test_list_tasks_invalid_due_date(self):
        with self.assertRaises(ValueError):
            list_tasks(due_date="not-a-valid-date", db_path=self.db_path)

    def test_update_task(self):
        task_id = create_task(
            title="Initial Title",
            description="Initial Description",
            priority="normal",
            db_path=self.db_path,
        )
        initial_task = get_task(task_id, db_path=self.db_path)

        success = update_task(
            task_id,
            title="Updated Title",
            description="Updated Description",
            priority="urgent",
            due_at="2026-11-10T12:00:00+07:00",
            db_path=self.db_path,
        )
        self.assertTrue(success)

        updated_task = get_task(task_id, db_path=self.db_path)
        self.assertEqual(updated_task["title"], "Updated Title")
        self.assertEqual(updated_task["description"], "Updated Description")
        self.assertEqual(updated_task["priority"], "urgent")
        self.assertTrue(updated_task["due_at"].startswith("2026-11-10T05:00:00"))
        self.assertGreaterEqual(updated_task["updated_at"], initial_task["updated_at"])

    def test_update_task_validation(self):
        task_id = create_task(title="Task to update", db_path=self.db_path)

        with self.assertRaises(ValueError):
            update_task(task_id, title="", db_path=self.db_path)

        with self.assertRaises(ValueError):
            update_task(task_id, priority="invalid_priority", db_path=self.db_path)

        with self.assertRaises(ValueError):
            update_task(task_id, status="invalid_status", db_path=self.db_path)

        with self.assertRaises(ValueError):
            update_task(task_id, non_existent_column="hacked", db_path=self.db_path)

    def test_update_task_not_found(self):
        success = update_task(99999, title="Non-existent", db_path=self.db_path)
        self.assertFalse(success)

    def test_mark_task_status(self):
        task_id = create_task(title="Task to mark", db_path=self.db_path)

        # Mark done
        self.assertTrue(mark_task_status(task_id, "done", db_path=self.db_path))
        task = get_task(task_id, db_path=self.db_path)
        self.assertEqual(task["status"], "done")

        # Mark archived
        self.assertTrue(mark_task_status(task_id, "archived", db_path=self.db_path))
        task = get_task(task_id, db_path=self.db_path)
        self.assertEqual(task["status"], "archived")

        # Invalid status raises ValueError
        with self.assertRaises(ValueError):
            mark_task_status(task_id, "invalid_status", db_path=self.db_path)

        # Non-existent task returns False
        self.assertFalse(mark_task_status(99999, "done", db_path=self.db_path))

    def test_naive_datetime_stored_as_utc(self):
        # If naive datetime string is provided (e.g. '2026-11-02 10:00:00'),
        # it should be interpreted as Asia/Jakarta (UTC+7) and stored as UTC (03:00:00+00:00).
        task_id = create_task(
            title="Meeting Task",
            due_at="2026-11-02 10:00:00",
            db_path=self.db_path,
        )
        task = get_task(task_id, db_path=self.db_path)
        self.assertEqual(task["due_at"], "2026-11-02T03:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
