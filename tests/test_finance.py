import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from config.settings import TIMEZONE
from src.database.connection import get_db_connection
from src.database.migrations import run_migrations
from src.domain.finance import (
    add_transaction,
    get_financial_summary,
    get_transaction,
    list_transactions,
    void_transaction,
)


class TestFinanceTrackerDomain(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_finance.db")
        run_migrations(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_add_transaction_success(self):
        tx_id = add_transaction(
            type="expense",
            amount_idr=25000,
            description="Nasi Goreng Spesial",
            category="Makanan",
            occurred_at="2026-11-02T12:30:00+07:00",
            payment_method="qris",
            db_path=self.db_path,
        )
        self.assertIsInstance(tx_id, int)
        self.assertGreater(tx_id, 0)

        tx = get_transaction(tx_id, db_path=self.db_path)
        self.assertIsNotNone(tx)
        self.assertEqual(tx["id"], tx_id)
        self.assertEqual(tx["type"], "expense")
        self.assertEqual(tx["amount_idr"], 25000)
        self.assertEqual(tx["description"], "Nasi Goreng Spesial")
        self.assertEqual(tx["category"], "Makanan")
        self.assertEqual(tx["payment_method"], "qris")
        self.assertEqual(tx["status"], "completed")
        self.assertTrue(tx["occurred_at"].endswith("+00:00") or tx["occurred_at"].endswith("Z"))
        self.assertIsNotNone(tx["created_at"])

    def test_add_transaction_category_auto_creation(self):
        tx1_id = add_transaction(
            type="expense",
            amount_idr=15000,
            category="Transportasi",
            db_path=self.db_path,
        )
        tx1 = get_transaction(tx1_id, db_path=self.db_path)
        self.assertEqual(tx1["category"], "Transportasi")
        cat1_id = tx1["category_id"]
        self.assertIsNotNone(cat1_id)

        # Check category exists in categories table with domain='finance'
        conn = get_db_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM categories WHERE id = ?;", (cat1_id,))
            row = cursor.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["domain"], "finance")
            self.assertEqual(row["name"], "Transportasi")
        finally:
            conn.close()

        # Adding another transaction with same category (different case) reuses category
        tx2_id = add_transaction(
            type="expense",
            amount_idr=20000,
            category="transportasi",
            db_path=self.db_path,
        )
        tx2 = get_transaction(tx2_id, db_path=self.db_path)
        self.assertEqual(tx2["category_id"], cat1_id)

    def test_idr_integer_parsing_cleanly(self):
        # Plain integer
        tx_id1 = add_transaction(type="income", amount_idr=50000, db_path=self.db_path)
        self.assertEqual(get_transaction(tx_id1, db_path=self.db_path)["amount_idr"], 50000)

        # Float
        tx_id2 = add_transaction(type="expense", amount_idr=75000.0, db_path=self.db_path)
        self.assertEqual(get_transaction(tx_id2, db_path=self.db_path)["amount_idr"], 75000)

        # String integer
        tx_id3 = add_transaction(type="expense", amount_idr="25000", db_path=self.db_path)
        self.assertEqual(get_transaction(tx_id3, db_path=self.db_path)["amount_idr"], 25000)

        # String with dots (Indonesian thousand separator)
        tx_id4 = add_transaction(type="expense", amount_idr="25.000", db_path=self.db_path)
        self.assertEqual(get_transaction(tx_id4, db_path=self.db_path)["amount_idr"], 25000)

        # String with Rp prefix and dots
        tx_id5 = add_transaction(type="expense", amount_idr="Rp 25.000", db_path=self.db_path)
        self.assertEqual(get_transaction(tx_id5, db_path=self.db_path)["amount_idr"], 25000)

        # String with Rp. prefix and commas
        tx_id6 = add_transaction(type="income", amount_idr="Rp. 1,500,000", db_path=self.db_path)
        self.assertEqual(get_transaction(tx_id6, db_path=self.db_path)["amount_idr"], 1500000)

        # String Indonesian format with decimal cents (e.g. 25.000,00)
        tx_id7 = add_transaction(type="expense", amount_idr="Rp 25.000,00", db_path=self.db_path)
        self.assertEqual(get_transaction(tx_id7, db_path=self.db_path)["amount_idr"], 25000)

        # String US format with decimal cents (e.g. 25,000.00)
        tx_id8 = add_transaction(type="expense", amount_idr="25,000.00", db_path=self.db_path)
        self.assertEqual(get_transaction(tx_id8, db_path=self.db_path)["amount_idr"], 25000)

    def test_amount_validation_non_positive_and_invalid(self):
        with self.assertRaises(ValueError):
            add_transaction(type="expense", amount_idr=0, db_path=self.db_path)

        with self.assertRaises(ValueError):
            add_transaction(type="expense", amount_idr=-5000, db_path=self.db_path)

        with self.assertRaises(ValueError):
            add_transaction(type="expense", amount_idr="-25.000", db_path=self.db_path)

        with self.assertRaises(ValueError):
            add_transaction(type="expense", amount_idr="Rp -10.000", db_path=self.db_path)

        with self.assertRaises(ValueError):
            add_transaction(type="expense", amount_idr=True, db_path=self.db_path)

        with self.assertRaises(ValueError):
            add_transaction(type="expense", amount_idr="bukan_angka", db_path=self.db_path)

        with self.assertRaises(ValueError):
            add_transaction(type="expense", amount_idr="", db_path=self.db_path)

        with self.assertRaises(ValueError):
            add_transaction(type="expense", amount_idr=None, db_path=self.db_path)

    def test_type_validation(self):
        # Case insensitivity
        tx_id1 = add_transaction(type="INCOME", amount_idr=100000, db_path=self.db_path)
        self.assertEqual(get_transaction(tx_id1, db_path=self.db_path)["type"], "income")

        tx_id2 = add_transaction(type=" Expense ", amount_idr=50000, db_path=self.db_path)
        self.assertEqual(get_transaction(tx_id2, db_path=self.db_path)["type"], "expense")

        # Invalid type
        with self.assertRaises(ValueError):
            add_transaction(type="transfer", amount_idr=50000, db_path=self.db_path)

        with self.assertRaises(ValueError):
            add_transaction(type="", amount_idr=50000, db_path=self.db_path)

    def test_occurred_at_parsing(self):
        # Default occurred_at
        tx_id1 = add_transaction(type="income", amount_idr=10000, db_path=self.db_path)
        tx1 = get_transaction(tx_id1, db_path=self.db_path)
        self.assertIsNotNone(tx1["occurred_at"])

        # Date only (YYYY-MM-DD) resolved in Asia/Jakarta
        tx_id2 = add_transaction(
            type="expense",
            amount_idr=20000,
            occurred_at="2026-11-02",
            db_path=self.db_path,
        )
        tx2 = get_transaction(tx_id2, db_path=self.db_path)
        # 2026-11-02 00:00:00 WIB (+07:00) is 2026-11-01T17:00:00+00:00
        dt_utc = datetime.fromisoformat(tx2["occurred_at"])
        dt_local = dt_utc.astimezone(ZoneInfo(TIMEZONE))
        self.assertEqual(dt_local.strftime("%Y-%m-%d"), "2026-11-02")

        # Invalid date format
        with self.assertRaises(ValueError):
            add_transaction(type="expense", amount_idr=20000, occurred_at="kemarin", db_path=self.db_path)

    def test_get_financial_summary_empty(self):
        summary = get_financial_summary(db_path=self.db_path)
        self.assertEqual(summary["total_income"], 0)
        self.assertEqual(summary["total_expense"], 0)
        self.assertEqual(summary["net_cash_flow"], 0)
        self.assertEqual(summary["category_breakdown"], {})

    def test_get_financial_summary_calculation(self):
        add_transaction(type="income", amount_idr=1000000, category="Gaji", db_path=self.db_path)
        add_transaction(type="income", amount_idr=500000, category="Bonus", db_path=self.db_path)
        add_transaction(type="expense", amount_idr=200000, category="Makanan", db_path=self.db_path)
        add_transaction(type="expense", amount_idr=100000, category="Makanan", db_path=self.db_path)
        add_transaction(type="expense", amount_idr=50000, category="Transport", db_path=self.db_path)

        summary = get_financial_summary(db_path=self.db_path)
        self.assertEqual(summary["total_income"], 1500000)
        self.assertEqual(summary["total_expense"], 350000)
        self.assertEqual(summary["net_cash_flow"], 1150000)
        self.assertEqual(summary["category_breakdown"]["Makanan"], 300000)
        self.assertEqual(summary["category_breakdown"]["Transport"], 50000)
        self.assertEqual(summary["category_breakdown"]["Gaji"], 1000000)
        self.assertEqual(summary["category_breakdown"]["Bonus"], 500000)

    def test_get_financial_summary_date_filtering(self):
        # 3 transactions on different dates
        add_transaction(type="income", amount_idr=100000, occurred_at="2026-11-01", db_path=self.db_path)
        add_transaction(type="expense", amount_idr=40000, occurred_at="2026-11-02", db_path=self.db_path)
        add_transaction(type="expense", amount_idr=60000, occurred_at="2026-11-05", db_path=self.db_path)

        # Filter strictly for 2026-11-02
        summary = get_financial_summary(
            start_date="2026-11-02",
            end_date="2026-11-02",
            db_path=self.db_path,
        )
        self.assertEqual(summary["total_income"], 0)
        self.assertEqual(summary["total_expense"], 40000)
        self.assertEqual(summary["net_cash_flow"], -40000)

    def test_void_transaction_and_audit_log(self):
        tx_id = add_transaction(
            type="expense",
            amount_idr=100000,
            description="Beli Perlengkapan",
            category="Kantor",
            db_path=self.db_path,
        )

        # Before void: expense included
        summary_before = get_financial_summary(db_path=self.db_path)
        self.assertEqual(summary_before["total_expense"], 100000)

        # Void transaction
        success = void_transaction(tx_id, reason="Transaksi salah input", db_path=self.db_path)
        self.assertTrue(success)

        # Subsequent void on already voided returns False
        self.assertFalse(void_transaction(tx_id, reason="Void lagi", db_path=self.db_path))

        # Void non-existent returns False
        self.assertFalse(void_transaction(99999, db_path=self.db_path))

        # Check transaction state
        tx = get_transaction(tx_id, db_path=self.db_path)
        self.assertEqual(tx["status"], "voided")
        self.assertIn("Transaksi salah input", tx["description"])

        # Check audit_logs
        conn = get_db_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM audit_logs WHERE entity_type = 'transaction' AND entity_id = ?;",
                (str(tx_id),),
            )
            audit_row = cursor.fetchone()
            self.assertIsNotNone(audit_row)
            self.assertEqual(audit_row["action"], "void_transaction")
            self.assertEqual(audit_row["result"], "Transaksi salah input")
        finally:
            conn.close()

        # After void: excluded from financial summary
        summary_after = get_financial_summary(db_path=self.db_path)
        self.assertEqual(summary_after["total_expense"], 0)
        self.assertEqual(summary_after["net_cash_flow"], 0)
        self.assertEqual(summary_after["category_breakdown"], {})

    def test_list_transactions(self):
        add_transaction(type="income", amount_idr=500000, occurred_at="2026-11-01", db_path=self.db_path)
        add_transaction(type="expense", amount_idr=50000, occurred_at="2026-11-02", db_path=self.db_path)
        add_transaction(type="expense", amount_idr=75000, occurred_at="2026-11-03", db_path=self.db_path)

        # List all
        all_tx = list_transactions(db_path=self.db_path)
        self.assertEqual(len(all_tx), 3)

        # Filter by type
        income_tx = list_transactions(type="income", db_path=self.db_path)
        self.assertEqual(len(income_tx), 1)
        self.assertEqual(income_tx[0]["amount_idr"], 500000)

        # Filter by date range
        filtered_tx = list_transactions(
            start_date="2026-11-02",
            end_date="2026-11-03",
            db_path=self.db_path,
        )
        self.assertEqual(len(filtered_tx), 2)

        # Test limit
        limited_tx = list_transactions(limit=1, db_path=self.db_path)
        self.assertEqual(len(limited_tx), 1)


if __name__ == "__main__":
    unittest.main()
