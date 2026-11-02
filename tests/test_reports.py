"""Unit tests for Daily Automation Aggregations (Morning Briefing & Evening Review)."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from config.settings import TIMEZONE
from src.database.connection import get_db_connection
from src.database.migrations import run_migrations
from src.domain.finance import add_transaction
from src.domain.reminders import create_reminder
from src.domain.reports import (
    format_idr,
    format_report_text,
    generate_evening_review,
    generate_morning_briefing,
    get_daily_review,
    save_daily_review,
)
from src.domain.tasks import create_task, mark_task_status


class TestReportsDomain(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_reports.db")
        run_migrations(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_format_idr_helper(self):
        self.assertEqual(format_idr(0), "Rp 0")
        self.assertEqual(format_idr(25000), "Rp 25.000")
        self.assertEqual(format_idr(1500000), "Rp 1.500.000")
        self.assertEqual(format_idr(-50000), "-Rp 50.000")

    def test_generate_morning_briefing_aggregation(self):
        target_date = "2026-11-02"
        # 1. Task due today (open)
        task1_id = create_task(
            title="Selesaikan Laporan Pajak",
            due_at="2026-11-02T15:00:00+07:00",
            priority="normal",
            db_path=self.db_path,
        )
        # 2. Overdue open task (due yesterday)
        task2_id = create_task(
            title="Follow Up Kontrak Vendor",
            due_at="2026-11-01T10:00:00+07:00",
            priority="normal",
            db_path=self.db_path,
        )
        # 3. High priority open task (no due date or future)
        task3_id = create_task(
            title="Deploy Hotfix Server",
            priority="urgent",
            due_at="2026-11-05T10:00:00+07:00",
            db_path=self.db_path,
        )
        # 4. Reminder scheduled for today
        rem_id = create_reminder(
            entity_type="task",
            entity_id=task1_id,
            remind_at="2026-11-02T09:00:00+07:00",
            db_path=self.db_path,
        )
        # 5. Yesterday's transactions (expenses)
        add_transaction(
            type="expense",
            amount_idr=50000,
            category="Makanan",
            description="Makan Siang",
            occurred_at="2026-11-01T12:00:00+07:00",
            db_path=self.db_path,
        )
        add_transaction(
            type="expense",
            amount_idr=25000,
            category="Transportasi",
            description="Bensin",
            occurred_at="2026-11-01T17:00:00+07:00",
            db_path=self.db_path,
        )
        # Today's transaction should not count for yesterday's recap
        add_transaction(
            type="expense",
            amount_idr=100000,
            category="Belanja",
            description="Alat Kantor",
            occurred_at="2026-11-02T08:00:00+07:00",
            db_path=self.db_path,
        )

        briefing = generate_morning_briefing(target_date=target_date, db_path=self.db_path)

        self.assertEqual(briefing["target_date"], target_date)
        self.assertEqual(briefing["yesterday_date"], "2026-11-01")

        # Verify today's tasks
        today_task_titles = [t["title"] for t in briefing["today_tasks"]]
        self.assertIn("Selesaikan Laporan Pajak", today_task_titles)

        # Verify overdue tasks
        overdue_titles = [t["title"] for t in briefing["overdue_tasks"]]
        self.assertIn("Follow Up Kontrak Vendor", overdue_titles)

        # Verify high priority / urgent tasks
        high_priority_titles = [t["title"] for t in briefing["high_priority_tasks"]]
        self.assertIn("Deploy Hotfix Server", high_priority_titles)

        # Verify pending reminders
        self.assertTrue(len(briefing["reminders"]) >= 1)
        rem_entity_ids = [r["entity_id"] for r in briefing["reminders"]]
        self.assertIn(task1_id, rem_entity_ids)

        # Verify yesterday's finance recap
        y_fin = briefing["yesterday_finance"]
        self.assertEqual(y_fin["total_expense"], 75000)
        self.assertIn("Makanan", y_fin["category_breakdown"])
        self.assertEqual(y_fin["category_breakdown"]["Makanan"], 50000)
        self.assertEqual(y_fin["category_breakdown"]["Transportasi"], 25000)

    def test_generate_morning_briefing_default_date(self):
        briefing = generate_morning_briefing(db_path=self.db_path)
        local_today = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")
        self.assertEqual(briefing["target_date"], local_today)
        self.assertIn("today_tasks", briefing)
        self.assertIn("yesterday_finance", briefing)

    def test_generate_evening_review_aggregation(self):
        target_date = "2026-11-02"
        # Today's tasks: 2 done, 1 open
        t1 = create_task(title="Task 1", due_at="2026-11-02T10:00:00+07:00", db_path=self.db_path)
        t2 = create_task(title="Task 2", due_at="2026-11-02T11:00:00+07:00", db_path=self.db_path)
        t3 = create_task(title="Task 3", due_at="2026-11-02T14:00:00+07:00", db_path=self.db_path)
        mark_task_status(t1, "done", db_path=self.db_path)
        mark_task_status(t2, "done", db_path=self.db_path)

        # Tomorrow's task
        t_tomorrow = create_task(
            title="Task Besok",
            due_at="2026-11-03T09:00:00+07:00",
            db_path=self.db_path,
        )

        # Today's transactions
        add_transaction(
            type="income",
            amount_idr=500000,
            category="Gaji",
            occurred_at="2026-11-02T09:00:00+07:00",
            db_path=self.db_path,
        )
        add_transaction(
            type="expense",
            amount_idr=150000,
            category="Makanan",
            occurred_at="2026-11-02T12:00:00+07:00",
            db_path=self.db_path,
        )

        review = generate_evening_review(target_date=target_date, db_path=self.db_path)

        self.assertEqual(review["target_date"], target_date)
        self.assertEqual(review["tomorrow_date"], "2026-11-03")
        self.assertEqual(review["completed_count"], 2)
        self.assertEqual(review["pending_count"], 1)
        self.assertEqual(round(review["completion_rate"], 1), 66.7)

        # Financial summary
        fin = review["finance"]
        self.assertEqual(fin["total_income"], 500000)
        self.assertEqual(fin["total_expense"], 150000)
        self.assertEqual(fin["net_cash_flow"], 350000)

        # Tomorrow's agenda preview
        tomorrow_titles = [t["title"] for t in review["tomorrow_tasks"]]
        self.assertIn("Task Besok", tomorrow_titles)

    def test_generate_evening_review_zero_tasks(self):
        target_date = "2026-11-02"
        review = generate_evening_review(target_date=target_date, db_path=self.db_path)
        self.assertEqual(review["completed_count"], 0)
        self.assertEqual(review["pending_count"], 0)
        self.assertIn(review["completion_rate"], (0.0, 100.0))

    def test_format_report_text_morning(self):
        target_date = "2026-11-02"
        t1 = create_task(title="Review Budget", due_at="2026-11-02T10:00:00+07:00", db_path=self.db_path)
        create_reminder(entity_type="task", entity_id=t1, remind_at="2026-11-02T09:00:00+07:00", db_path=self.db_path)
        add_transaction(
            type="expense",
            amount_idr=80000,
            category="Transportasi",
            occurred_at="2026-11-01T15:00:00+07:00",
            db_path=self.db_path,
        )

        briefing_data = generate_morning_briefing(target_date=target_date, db_path=self.db_path)
        formatted_text = format_report_text(briefing_data, "morning")

        self.assertIsInstance(formatted_text, str)
        self.assertIn("🌅 *Morning Briefing", formatted_text)
        self.assertIn("02 November 2026", formatted_text)
        self.assertIn("Review Budget", formatted_text)
        self.assertIn("Pengeluaran Kemarin", formatted_text)
        self.assertIn("Rp 80.000", formatted_text)

    def test_format_report_text_evening(self):
        target_date = "2026-11-02"
        t1 = create_task(title="Meeting Klien", due_at="2026-11-02T10:00:00+07:00", db_path=self.db_path)
        mark_task_status(t1, "done", db_path=self.db_path)
        create_task(title="Diskusi Tim", due_at="2026-11-03T10:00:00+07:00", db_path=self.db_path)
        add_transaction(
            type="income",
            amount_idr=1000000,
            occurred_at="2026-11-02T10:00:00+07:00",
            db_path=self.db_path,
        )
        add_transaction(
            type="expense",
            amount_idr=250000,
            occurred_at="2026-11-02T14:00:00+07:00",
            db_path=self.db_path,
        )

        review_data = generate_evening_review(target_date=target_date, db_path=self.db_path)
        formatted_text = format_report_text(review_data, "evening")

        self.assertIsInstance(formatted_text, str)
        self.assertIn("🌙 *Evening Review", formatted_text)
        self.assertIn("02 November 2026", formatted_text)
        self.assertIn("Produktivitas", formatted_text)
        self.assertIn("Pemasukan: Rp 1.000.000", formatted_text)
        self.assertIn("Pengeluaran: Rp 250.000", formatted_text)
        self.assertIn("Arus Kas Bersih: Rp 750.000", formatted_text)
        self.assertIn("Diskusi Tim", formatted_text)

    def test_format_report_text_invalid_type(self):
        with self.assertRaises(ValueError):
            format_report_text({}, "afternoon")

    def test_save_daily_review_persistence(self):
        review_date = "2026-11-02"
        summary_payload = {
            "completed_count": 5,
            "pending_count": 1,
            "completion_rate": 83.3,
            "total_expense": 120000,
        }

        row_id = save_daily_review(
            review_date=review_date,
            summary_data=summary_payload,
            delivery_status="sent",
            db_path=self.db_path,
        )
        self.assertIsInstance(row_id, int)
        self.assertGreater(row_id, 0)

        record = get_daily_review(review_date=review_date, db_path=self.db_path)
        self.assertIsNotNone(record)
        self.assertEqual(record["id"], row_id)
        self.assertEqual(record["review_date"], review_date)
        self.assertEqual(record["delivery_status"], "sent")
        self.assertIn("generated_at", record)

        parsed_json = json.loads(record["summary_json"])
        self.assertEqual(parsed_json["completed_count"], 5)
        self.assertEqual(parsed_json["total_expense"], 120000)

    def test_invalid_target_date_format(self):
        with self.assertRaises(ValueError):
            generate_morning_briefing(target_date="invalid-date", db_path=self.db_path)

        with self.assertRaises(ValueError):
            generate_evening_review(target_date="02/11/2026", db_path=self.db_path)

        with self.assertRaises(ValueError):
            save_daily_review(
                review_date="invalid-date",
                summary_data={},
                db_path=self.db_path,
            )


if __name__ == "__main__":
    unittest.main()
