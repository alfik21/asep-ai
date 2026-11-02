import os
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from src.database.migrations import run_migrations
from src.agent.cli import dispatch_command, main


class TestCLIDispatcher(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_cli.db")
        run_migrations(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_dispatch_tasks_flow(self):
        # 1. Initially empty
        res = dispatch_command("/tasks", db_path=self.db_path)
        self.assertIn("Tidak ada tugas", res)

        # 2. Create task with UTC due time
        res_create = dispatch_command(
            '/tasks --create "Beli Susu Kental Manis" --priority high --due 2026-11-05T03:00:00+00:00',
            db_path=self.db_path,
        )
        self.assertIn("Tugas berhasil dibuat", res_create)
        self.assertIn("Beli Susu Kental Manis", res_create)
        # 03:00 UTC should be formatted as 10:00 WIB (Asia/Jakarta +07:00)
        self.assertIn("2026-11-05 10:00 WIB", res_create)

        # 3. List open tasks
        res_list = dispatch_command("/tasks --status open", db_path=self.db_path)
        self.assertIn("Beli Susu Kental Manis", res_list)
        self.assertIn("[High]", res_list)
        self.assertIn("2026-11-05 10:00 WIB", res_list)

        # 4. Mark task as done
        # Extract task id from res_create or list
        res_done = dispatch_command("/tasks --done 1", db_path=self.db_path)
        self.assertIn("Tugas #1 berhasil ditandai selesai", res_done)

        # 5. List done tasks
        res_done_list = dispatch_command("/tasks --status done", db_path=self.db_path)
        self.assertIn("Beli Susu Kental Manis", res_done_list)

    def test_dispatch_today_shortcut(self):
        # Create a task for today (in local timezone)
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from config.settings import TIMEZONE

        local_today = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")
        dispatch_command(
            f'/tasks --create "Tugas Hari Ini" --due {local_today}',
            db_path=self.db_path,
        )

        res = dispatch_command("/today", db_path=self.db_path)
        self.assertIn("Agenda Hari Ini", res)
        self.assertIn("Tugas Hari Ini", res)

    def test_dispatch_finance_and_rekap(self):
        # Add income
        res_inc = dispatch_command(
            '/finance --add --type income --amount 5000000 --desc "Gaji Pokok" --category Gaji',
            db_path=self.db_path,
        )
        self.assertIn("Transaksi berhasil dicatat", res_inc)
        self.assertIn("Rp 5.000.000", res_inc)

        # Add expense with formatted string
        res_exp = dispatch_command(
            '/finance --add --type expense --amount "50.000" --desc "Makan Siang" --category Konsumsi',
            db_path=self.db_path,
        )
        self.assertIn("Transaksi berhasil dicatat", res_exp)
        self.assertIn("Rp 50.000", res_exp)

        # Add expense with Rp prefix formatted string
        res_exp_rp = dispatch_command(
            '/finance --add --type expense --amount "Rp 75.000" --desc "Bensin" --category Transport',
            db_path=self.db_path,
        )
        self.assertIn("Transaksi berhasil dicatat", res_exp_rp)
        self.assertIn("Rp 75.000", res_exp_rp)

        # Financial rekap shortcut
        res_rekap = dispatch_command("/rekap", db_path=self.db_path)
        self.assertIn("Rekap Keuangan", res_rekap)
        self.assertIn("Rp 5.000.000", res_rekap)
        self.assertIn("Rp 50.000", res_rekap)

        # Financial rekap with custom date bounds
        res_rekap_bounds = dispatch_command(
            "/finance --rekap --start 2026-01-01 --end 2026-12-31",
            db_path=self.db_path,
        )
        self.assertIn("Rekap Keuangan", res_rekap_bounds)

    def test_dispatch_notes_flow(self):
        # Save note
        res_save = dispatch_command(
            '/notes --save "Catatan penting arsitektur sistem" --title "Arsitektur" --tag ide --category Tech',
            db_path=self.db_path,
        )
        self.assertIn("Catatan berhasil disimpan", res_save)

        # Search note by keyword
        res_search = dispatch_command('/notes --search "arsitektur"', db_path=self.db_path)
        self.assertIn("Arsitektur", res_search)
        self.assertIn("Catatan penting arsitektur sistem", res_search)

        # Search note by tag
        res_tag = dispatch_command('/notes --tag ide', db_path=self.db_path)
        self.assertIn("Arsitektur", res_tag)

        # List recent notes
        res_all = dispatch_command('/notes', db_path=self.db_path)
        self.assertIn("Arsitektur", res_all)

        # Shortcut alias /note
        res_alias = dispatch_command('/note --search "arsitektur"', db_path=self.db_path)
        self.assertIn("Arsitektur", res_alias)

    def test_dispatch_reminders_flow(self):
        # Create reminder with UTC timestamp (02:00 UTC = 09:00 WIB)
        res_create = dispatch_command(
            '/reminders --create --entity-type task --entity-id 1 --remind-at 2026-11-02T02:00:00+00:00',
            db_path=self.db_path,
        )
        self.assertIn("Pengingat berhasil dibuat", res_create)
        self.assertIn("2026-11-02 09:00 WIB", res_create)

        # Check due reminders - should display localized WIB time
        res_check = dispatch_command('/reminders --check-due', db_path=self.db_path)
        self.assertIn("Pengingat", res_check)
        self.assertIn("2026-11-02 09:00 WIB", res_check)

    def test_dispatch_briefing_and_review(self):
        # Morning briefing
        res_briefing = dispatch_command('/briefing --date 2026-11-02', db_path=self.db_path)
        self.assertIn("Morning Briefing", res_briefing)

        # Evening review
        res_review = dispatch_command('/review --date 2026-11-02', db_path=self.db_path)
        self.assertIn("Evening Review", res_review)

    def test_dispatch_status(self):
        res_status = dispatch_command('/status', db_path=self.db_path)
        self.assertIn("Status Kesehatan Sistem ASEP-AI", res_status)
        self.assertIn("Database SQLite", res_status)

    def test_dispatch_unknown_command(self):
        res = dispatch_command('/unknown_command', db_path=self.db_path)
        self.assertIn("Perintah tidak dikenali", res)
        self.assertIn("/tasks", res)
        self.assertIn("/today", res)

    def test_cli_main_stdout(self):
        # Test CLI main invocation writing to stdout
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = main(["status", "--db-path", self.db_path])
            self.assertEqual(code, 0)
            output = fake_out.getvalue()
            self.assertIn("Status Kesehatan Sistem ASEP-AI", output)

        # Test CLI main for tasks create & list
        with patch("sys.stdout", new=StringIO()) as fake_out:
            code = main(["tasks", "--create", "Tugas CLI", "--db-path", self.db_path])
            self.assertEqual(code, 0)
            output = fake_out.getvalue()
            self.assertIn("Tugas berhasil dibuat", output)

    def test_dispatch_empty_and_help(self):
        res_empty = dispatch_command("", db_path=self.db_path)
        self.assertIn("Perintah Tersedia", res_empty)

        res_help = dispatch_command("/help", db_path=self.db_path)
        self.assertIn("Perintah Tersedia", res_help)

        res_q = dispatch_command("?", db_path=self.db_path)
        self.assertIn("Perintah Tersedia", res_q)

    def test_dispatch_tasks_edge_cases(self):
        # Non-existent task ID for done
        res_done_none = dispatch_command("/tasks --done 99999", db_path=self.db_path)
        self.assertIn("tidak ditemukan", res_done_none)

        # Invalid task ID string
        res_bad_id = dispatch_command("/tasks --done abc", db_path=self.db_path)
        self.assertIn("tidak valid", res_bad_id)

    def test_dispatch_finance_edge_cases(self):
        # Missing type
        res_no_type = dispatch_command("/finance --add --amount 5000", db_path=self.db_path)
        self.assertIn("Tipe transaksi wajib", res_no_type)

        # Missing amount
        res_no_amt = dispatch_command("/finance --add --type expense", db_path=self.db_path)
        self.assertIn("Jumlah transaksi wajib", res_no_amt)

    def test_dispatch_reminders_edge_cases(self):
        # Missing remind-at when creating
        res_no_time = dispatch_command("/reminders --create --entity-id 1", db_path=self.db_path)
        self.assertIn("Waktu pengingat wajib", res_no_time)

    def test_cli_subprocess_run(self):
        import subprocess
        res = subprocess.run(
            [sys.executable, "-m", "src.agent.cli", "status", "--db-path", self.db_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("Status Kesehatan Sistem ASEP-AI", res.stdout)


if __name__ == "__main__":
    unittest.main()
