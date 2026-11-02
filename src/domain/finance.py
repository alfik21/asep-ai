"""Finance Tracker domain logic.

Provides transaction recording, category lookup/auto-creation,
strict integer Indonesian Rupiah (IDR) arithmetic, deterministic
SQL aggregations, voiding with audit log tracking, and list filtering.
"""

from datetime import datetime, timedelta, timezone
import math
import re
import sqlite3
from typing import Any, Optional
from zoneinfo import ZoneInfo

from config.settings import TIMEZONE
from src.database.connection import get_db_connection

ALLOWED_TYPES = {"income", "expense"}


def _parse_amount_idr(amount: Any) -> int:
    """Parses and validates an Indonesian Rupiah amount into a strict positive integer.

    Accepts ints, floats, or formatted strings (e.g. '25000', '25.000', 'Rp 25.000',
    '25,000.00', 'Rp 25.000,00').

    Raises:
        ValueError: If amount is non-positive (<= 0), boolean, empty, or unparseable.
    """
    if isinstance(amount, bool):
        raise ValueError("Amount cannot be boolean.")

    if amount is None:
        raise ValueError("Amount cannot be None.")

    if isinstance(amount, int):
        if amount <= 0:
            raise ValueError(f"Amount must be greater than 0, got {amount}.")
        return amount

    if isinstance(amount, float):
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError(f"Amount must be greater than 0, got {amount}.")
        return int(round(amount))

    if not isinstance(amount, str):
        raise ValueError(f"Invalid amount type: {type(amount).__name__}.")

    cleaned = amount.strip()
    if not cleaned:
        raise ValueError("Amount string cannot be empty.")

    # Reject explicit negative signs
    if "-" in cleaned:
        raise ValueError(f"Amount must be greater than 0, got negative: '{amount}'.")

    # Strip currency prefixes (e.g. Rp, Rp., IDR)
    cleaned_lower = cleaned.lower()
    for prefix in ["rp.", "rp", "idr"]:
        if cleaned_lower.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            cleaned_lower = cleaned.lower()
            break

    if not cleaned:
        raise ValueError(f"Invalid amount string: '{amount}'.")

    # Handle thousand and decimal separators:
    # 1. Both '.' and ',' present
    if "." in cleaned and "," in cleaned:
        dot_idx = cleaned.rfind(".")
        comma_idx = cleaned.rfind(",")
        if dot_idx < comma_idx:
            # Indonesian format: 25.000,00 -> '.' is thousand, ',' is decimal
            integer_part = cleaned[:comma_idx].replace(".", "")
        else:
            # International format: 25,000.00 -> ',' is thousand, '.' is decimal
            integer_part = cleaned[:dot_idx].replace(",", "")
    elif "." in cleaned:
        # Only dot present
        parts = cleaned.split(".")
        if len(parts) > 2:
            # Multiple dots: 1.500.000
            integer_part = "".join(parts)
        elif len(parts[1]) == 3:
            # Single dot with 3 digits: 25.000 (thousand separator)
            integer_part = parts[0] + parts[1]
        elif len(parts[1]) in (1, 2):
            # Decimal cents: 25000.00
            integer_part = parts[0]
        else:
            integer_part = parts[0] + parts[1]
    elif "," in cleaned:
        # Only comma present
        parts = cleaned.split(",")
        if len(parts) > 2:
            # Multiple commas: 1,500,000
            integer_part = "".join(parts)
        elif len(parts[1]) == 3:
            # Single comma with 3 digits: 25,000
            integer_part = parts[0] + parts[1]
        elif len(parts[1]) in (1, 2):
            # Decimal cents: 25000,00
            integer_part = parts[0]
        else:
            integer_part = parts[0] + parts[1]
    else:
        integer_part = cleaned

    integer_part = integer_part.strip()
    if not integer_part.isdigit():
        raise ValueError(f"Invalid amount format: '{amount}'.")

    val = int(integer_part)
    if val <= 0:
        raise ValueError(f"Amount must be greater than 0, got {val}.")

    return val


def _parse_type(type_input: str) -> str:
    """Validates and normalizes transaction type to 'income' or 'expense'."""
    if not isinstance(type_input, str):
        raise ValueError("Transaction type must be a string ('income' or 'expense').")
    norm_type = type_input.strip().lower()
    if norm_type not in ALLOWED_TYPES:
        raise ValueError(
            f"Invalid transaction type '{type_input}'. Must be 'income' or 'expense'."
        )
    return norm_type


def _parse_occurred_at(dt_input: Optional[str]) -> str:
    """Parses occurred_at timestamp to an ISO-8601 UTC string.

    If dt_input is None or empty: returns current UTC timestamp.
    If dt_input is YYYY-MM-DD: resolves at 00:00:00 in Asia/Jakarta, converted to UTC.
    If dt_input is ISO format: localizes naive timestamps to Asia/Jakarta, converted to UTC.
    """
    if dt_input is None or not str(dt_input).strip():
        return datetime.now(timezone.utc).isoformat()

    cleaned = str(dt_input).strip()
    local_tz = ZoneInfo(TIMEZONE)

    if len(cleaned) == 10 and cleaned.count("-") == 2:
        try:
            local_date = datetime.strptime(cleaned, "%Y-%m-%d").date()
            local_dt = datetime.combine(local_date, datetime.min.time(), tzinfo=local_tz)
            return local_dt.astimezone(timezone.utc).isoformat()
        except ValueError as e:
            raise ValueError(f"Invalid date format: '{dt_input}'. Expected YYYY-MM-DD.") from e

    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError as e:
        raise ValueError(f"Invalid datetime format: '{dt_input}'. Expected ISO-8601 or YYYY-MM-DD format.") from e

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=local_tz)

    return dt.astimezone(timezone.utc).isoformat()


def _parse_date_filter_bounds(
    start_date: Optional[str],
    end_date: Optional[str],
) -> tuple[Optional[str], Optional[str], bool]:
    """Parses start and end date bounds into ISO-8601 UTC strings.

    Returns:
        (start_utc_iso, end_utc_iso, end_is_exclusive)
    """
    local_tz = ZoneInfo(TIMEZONE)
    start_utc_iso: Optional[str] = None
    end_utc_iso: Optional[str] = None
    end_is_exclusive = False

    if start_date is not None and str(start_date).strip():
        cleaned_start = str(start_date).strip()
        if len(cleaned_start) == 10 and cleaned_start.count("-") == 2:
            try:
                local_d = datetime.strptime(cleaned_start, "%Y-%m-%d").date()
                dt_local = datetime.combine(local_d, datetime.min.time(), tzinfo=local_tz)
                start_utc_iso = dt_local.astimezone(timezone.utc).isoformat()
            except ValueError as e:
                raise ValueError(f"Invalid start_date format: '{start_date}'. Expected YYYY-MM-DD.") from e
        else:
            try:
                dt = datetime.fromisoformat(cleaned_start)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=local_tz)
                start_utc_iso = dt.astimezone(timezone.utc).isoformat()
            except ValueError as e:
                raise ValueError(f"Invalid start_date format: '{start_date}'. Expected ISO-8601 or YYYY-MM-DD.") from e

    if end_date is not None and str(end_date).strip():
        cleaned_end = str(end_date).strip()
        if len(cleaned_end) == 10 and cleaned_end.count("-") == 2:
            try:
                local_d = datetime.strptime(cleaned_end, "%Y-%m-%d").date()
                # Include entire day: up to next day 00:00:00 (exclusive)
                next_day = local_d + timedelta(days=1)
                dt_local = datetime.combine(next_day, datetime.min.time(), tzinfo=local_tz)
                end_utc_iso = dt_local.astimezone(timezone.utc).isoformat()
                end_is_exclusive = True
            except ValueError as e:
                raise ValueError(f"Invalid end_date format: '{end_date}'. Expected YYYY-MM-DD.") from e
        else:
            try:
                dt = datetime.fromisoformat(cleaned_end)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=local_tz)
                end_utc_iso = dt.astimezone(timezone.utc).isoformat()
                end_is_exclusive = False
            except ValueError as e:
                raise ValueError(f"Invalid end_date format: '{end_date}'. Expected ISO-8601 or YYYY-MM-DD.") from e

    return start_utc_iso, end_utc_iso, end_is_exclusive


def _get_or_create_category_id(
    category_name: Optional[str],
    cursor: sqlite3.Cursor,
) -> Optional[int]:
    """Finds or creates a category with domain='finance'."""
    if category_name is None:
        return None
    cleaned = category_name.strip()
    if not cleaned:
        return None

    cursor.execute(
        "SELECT id FROM categories WHERE domain = 'finance' AND LOWER(name) = LOWER(?);",
        (cleaned,),
    )
    row = cursor.fetchone()
    if row is not None:
        return row[0] if isinstance(row, tuple) else row["id"]

    cursor.execute(
        "INSERT INTO categories (domain, name, is_active) VALUES ('finance', ?, 1);",
        (cleaned,),
    )
    return cursor.lastrowid


def add_transaction(
    type: str,
    amount_idr: Any,
    description: Optional[str] = None,
    category: Optional[str] = None,
    occurred_at: Optional[str] = None,
    payment_method: Optional[str] = None,
    db_path: Optional[str] = None,
) -> int:
    """Adds a financial transaction into the database.

    Args:
        type: 'income' or 'expense' (case-insensitive).
        amount_idr: Transaction amount strictly parsed to positive integer IDR.
        description: Optional notes/description.
        category: Optional category name (auto-created in categories table if not exists).
        occurred_at: Optional datetime string (ISO-8601 or YYYY-MM-DD). Defaults to now UTC.
        payment_method: Optional payment method string (e.g. 'cash', 'qris', 'transfer').
        db_path: Optional SQLite database path.

    Returns:
        int: The inserted transaction ID.

    Raises:
        ValueError: If type or amount is invalid.
    """
    norm_type = _parse_type(type)
    parsed_amount = _parse_amount_idr(amount_idr)
    utc_occurred_at = _parse_occurred_at(occurred_at)
    now_utc_iso = datetime.now(timezone.utc).isoformat()
    clean_desc = description.strip() if isinstance(description, str) and description.strip() else None
    clean_payment = payment_method.strip() if isinstance(payment_method, str) and payment_method.strip() else None

    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            category_id = _get_or_create_category_id(category, cursor)
            cursor.execute(
                """
                INSERT INTO transactions (
                    type,
                    amount_idr,
                    category_id,
                    description,
                    occurred_at,
                    payment_method,
                    status,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'completed', ?);
                """,
                (
                    norm_type,
                    parsed_amount,
                    category_id,
                    clean_desc,
                    utc_occurred_at,
                    clean_payment,
                    now_utc_iso,
                ),
            )
            return cursor.lastrowid
    finally:
        conn.close()


def get_transaction(
    transaction_id: int,
    db_path: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Retrieves a single transaction by ID.

    Returns:
        dict representation of the transaction or None if not found.
    """
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT 
                t.id,
                t.type,
                t.amount_idr,
                t.category_id,
                c.name AS category,
                t.description,
                t.occurred_at,
                t.payment_method,
                t.status,
                t.created_at
            FROM transactions t
            LEFT JOIN categories c ON t.category_id = c.id
            WHERE t.id = ?;
            """,
            (transaction_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def void_transaction(
    transaction_id: int,
    reason: Optional[str] = None,
    db_path: Optional[str] = None,
) -> bool:
    """Voids a transaction and records an audit log.

    Voided transactions are excluded from financial summaries.

    Args:
        transaction_id: ID of the transaction to void.
        reason: Optional reason for voiding.
        db_path: Optional SQLite database path.

    Returns:
        bool: True if transaction was successfully voided, False if not found or already voided.
    """
    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, description, status FROM transactions WHERE id = ?;",
                (transaction_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return False
            if row["status"] == "voided":
                return False

            now_utc_iso = datetime.now(timezone.utc).isoformat()
            current_desc = row["description"] or ""
            clean_reason = reason.strip() if isinstance(reason, str) and reason.strip() else None

            if clean_reason:
                if current_desc:
                    updated_desc = f"{current_desc} [VOID: {clean_reason}]"
                else:
                    updated_desc = f"[VOID: {clean_reason}]"
            else:
                updated_desc = current_desc

            cursor.execute(
                "UPDATE transactions SET status = 'voided', description = ? WHERE id = ?;",
                (updated_desc, transaction_id),
            )

            # Record in audit_logs
            cursor.execute(
                """
                INSERT INTO audit_logs (actor, action, entity_type, entity_id, timestamp, result)
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (
                    "system",
                    "void_transaction",
                    "transaction",
                    str(transaction_id),
                    now_utc_iso,
                    clean_reason or "voided",
                ),
            )
            return True
    finally:
        conn.close()


def get_financial_summary(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    """Calculates financial totals and category breakdown using SQL aggregation.

    Excludes voided transactions.

    Args:
        start_date: Optional start date/datetime string (YYYY-MM-DD or ISO-8601).
        end_date: Optional end date/datetime string (YYYY-MM-DD or ISO-8601).
        db_path: Optional SQLite database path.

    Returns:
        dict with total_income, total_expense, net_cash_flow, category_breakdown.
    """
    start_utc, end_utc, end_exclusive = _parse_date_filter_bounds(start_date, end_date)

    where_clauses = ["t.status != 'voided'"]
    params: list[Any] = []

    if start_utc:
        where_clauses.append("t.occurred_at >= ?")
        params.append(start_utc)

    if end_utc:
        if end_exclusive:
            where_clauses.append("t.occurred_at < ?")
        else:
            where_clauses.append("t.occurred_at <= ?")
        params.append(end_utc)

    where_sql = " WHERE " + " AND ".join(where_clauses)

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()

        # Query total income and expense
        total_query = f"""
            SELECT 
                COALESCE(SUM(CASE WHEN t.type = 'income' THEN t.amount_idr ELSE 0 END), 0) AS total_income,
                COALESCE(SUM(CASE WHEN t.type = 'expense' THEN t.amount_idr ELSE 0 END), 0) AS total_expense
            FROM transactions t
            {where_sql};
        """
        cursor.execute(total_query, tuple(params))
        row = cursor.fetchone()
        total_income = int(row["total_income"]) if row else 0
        total_expense = int(row["total_expense"]) if row else 0
        net_cash_flow = total_income - total_expense

        # Query category breakdown
        breakdown_query = f"""
            SELECT 
                COALESCE(c.name, 'Uncategorized') AS category,
                SUM(t.amount_idr) AS total_amount
            FROM transactions t
            LEFT JOIN categories c ON t.category_id = c.id
            {where_sql}
            GROUP BY COALESCE(c.name, 'Uncategorized')
            ORDER BY total_amount DESC;
        """
        cursor.execute(breakdown_query, tuple(params))
        cat_rows = cursor.fetchall()
        category_breakdown = {cat_row["category"]: int(cat_row["total_amount"]) for cat_row in cat_rows}

        return {
            "total_income": total_income,
            "total_expense": total_expense,
            "net_cash_flow": net_cash_flow,
            "category_breakdown": category_breakdown,
            "breakdown_by_category": category_breakdown,
        }
    finally:
        conn.close()


def list_transactions(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    type: Optional[str] = None,
    limit: int = 50,
    db_path: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Lists transactions with optional date range and type filtering.

    Args:
        start_date: Optional start date/datetime string.
        end_date: Optional end date/datetime string.
        type: Optional filter by 'income' or 'expense'.
        limit: Max number of transactions to return (default 50).
        db_path: Optional SQLite database path.

    Returns:
        list of transaction dicts sorted by occurred_at DESC, id DESC.
    """
    start_utc, end_utc, end_exclusive = _parse_date_filter_bounds(start_date, end_date)

    where_clauses: list[str] = []
    params: list[Any] = []

    if type is not None and str(type).strip():
        norm_type = _parse_type(type)
        where_clauses.append("t.type = ?")
        params.append(norm_type)

    if start_utc:
        where_clauses.append("t.occurred_at >= ?")
        params.append(start_utc)

    if end_utc:
        if end_exclusive:
            where_clauses.append("t.occurred_at < ?")
        else:
            where_clauses.append("t.occurred_at <= ?")
        params.append(end_utc)

    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    query = f"""
        SELECT 
            t.id,
            t.type,
            t.amount_idr,
            t.category_id,
            c.name AS category,
            t.description,
            t.occurred_at,
            t.payment_method,
            t.status,
            t.created_at
        FROM transactions t
        LEFT JOIN categories c ON t.category_id = c.id
        {where_sql}
        ORDER BY t.occurred_at DESC, t.id DESC
        LIMIT ?;
    """
    params.append(max(1, limit))

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
