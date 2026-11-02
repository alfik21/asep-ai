"""Deterministic Reminder Engine for ASEP-AI.

Provides deterministic scheduling, querying, and lifecycle transitions for reminders.
All timestamps are normalized and stored in ISO-8601 UTC while accepting local
Asia/Jakarta or explicit timezone inputs. Employs unique idempotency keys to ensure
duplicate dispatches and redundant inserts are strictly prevented.
"""

from datetime import datetime, timezone
import sqlite3
from typing import Any, Optional
from zoneinfo import ZoneInfo

from config.settings import TIMEZONE
from src.database.connection import get_db_connection

ALLOWED_STATUSES = {"pending", "processing", "sent", "failed", "cancelled"}


def _parse_to_utc_iso(dt_input: Optional[str]) -> str:
    """Parses a datetime string and converts it to an ISO-8601 UTC string (+00:00).

    If input has no timezone info, it is assumed to be in the local application
    timezone (Asia/Jakarta) and converted to UTC.

    Raises:
        ValueError: If input is None, empty, or cannot be parsed as an ISO datetime.
    """
    if dt_input is None:
        raise ValueError("Datetime string is required.")

    cleaned = dt_input.strip() if isinstance(dt_input, str) else str(dt_input)
    if not cleaned:
        raise ValueError("Datetime string cannot be empty.")

    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError as e:
        raise ValueError(f"Invalid datetime format: '{dt_input}'. Expected ISO-8601 format.") from e

    if dt.tzinfo is None:
        local_tz = ZoneInfo(TIMEZONE)
        dt = dt.replace(tzinfo=local_tz)

    utc_dt = dt.astimezone(timezone.utc)
    return utc_dt.isoformat()


def create_reminder(
    entity_type: str,
    entity_id: int,
    remind_at: str,
    idempotency_key: Optional[str] = None,
    db_path: Optional[str] = None,
) -> int:
    """Creates a new reminder deterministically or returns existing reminder id.

    Args:
        entity_type: Associated entity category (e.g. 'task', 'note', 'transaction').
        entity_id: Identifier of the target entity.
        remind_at: Scheduled trigger timestamp in ISO-8601 (naive assumes Asia/Jakarta).
        idempotency_key: Optional unique key. Defaults to f"{entity_type}:{entity_id}:{utc_remind_at}".
        db_path: Optional database file path for isolation/testing.

    Returns:
        int: The unique identifier of the scheduled reminder.

    Raises:
        ValueError: If entity_type is empty, entity_id is invalid, or remind_at cannot be parsed.
    """
    if not isinstance(entity_type, str) or not entity_type.strip():
        raise ValueError("entity_type must be a non-empty string.")

    if not isinstance(entity_id, int) or isinstance(entity_id, bool):
        raise ValueError("entity_id must be an integer.")

    utc_remind_at = _parse_to_utc_iso(remind_at)

    if idempotency_key is not None and isinstance(idempotency_key, str) and idempotency_key.strip():
        actual_key = idempotency_key.strip()
    else:
        actual_key = f"{entity_type.strip()}:{entity_id}:{utc_remind_at}"

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        # Check if reminder with this idempotency key already exists
        cursor.execute(
            "SELECT id FROM reminders WHERE idempotency_key = ?;",
            (actual_key,),
        )
        existing = cursor.fetchone()
        if existing:
            return existing["id"]

        try:
            with conn:
                cursor.execute(
                    """
                    INSERT INTO reminders (entity_type, entity_id, remind_at, status, idempotency_key, retry_count)
                    VALUES (?, ?, ?, 'pending', ?, 0);
                    """,
                    (entity_type.strip(), entity_id, utc_remind_at, actual_key),
                )
                return cursor.lastrowid
        except sqlite3.IntegrityError:
            # Handle concurrent insert race condition on unique idempotency_key
            cursor.execute(
                "SELECT id FROM reminders WHERE idempotency_key = ?;",
                (actual_key,),
            )
            existing = cursor.fetchone()
            if existing:
                return existing["id"]
            raise
    finally:
        conn.close()


def get_reminder(reminder_id: int, db_path: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Retrieves a reminder by its ID.

    Args:
        reminder_id: Primary key of the reminder.
        db_path: Optional database file path.

    Returns:
        dict: Reminder attributes if found, None otherwise.
    """
    if not isinstance(reminder_id, int) or isinstance(reminder_id, bool) or reminder_id <= 0:
        return None

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM reminders WHERE id = ?;", (reminder_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(row)
    finally:
        conn.close()


def get_due_reminders(
    now_utc: Optional[str] = None,
    limit: int = 50,
    db_path: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Retrieves pending reminders scheduled at or before the given reference time.

    Args:
        now_utc: Optional reference timestamp. Defaults to current UTC time.
        limit: Maximum number of reminders to fetch (default 50).
        db_path: Optional database file path.

    Returns:
        list[dict]: Ordered list of due reminder dictionaries.
    """
    if limit <= 0:
        return []

    if now_utc is None:
        target_utc = datetime.now(timezone.utc).isoformat()
    else:
        target_utc = _parse_to_utc_iso(now_utc)

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM reminders
            WHERE status = 'pending' AND (remind_at <= ? OR replace(remind_at, 'Z', '+00:00') <= ?)
            ORDER BY remind_at ASC
            LIMIT ?;
            """,
            (target_utc, target_utc, limit),
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def mark_reminder_processing(reminder_id: int, db_path: Optional[str] = None) -> bool:
    """Transitions a pending reminder to processing state during dispatch.

    Args:
        reminder_id: Primary key of the reminder.
        db_path: Optional database file path.

    Returns:
        bool: True if state was transitioned, False otherwise.
    """
    if not isinstance(reminder_id, int) or isinstance(reminder_id, bool):
        return False

    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE reminders SET status = 'processing' WHERE id = ? AND status = 'pending';",
                (reminder_id,),
            )
            return cursor.rowcount > 0
    finally:
        conn.close()


def mark_reminder_sent(reminder_id: int, db_path: Optional[str] = None) -> bool:
    """Marks a reminder as successfully sent.

    Sets status to 'sent' and sent_at to current UTC time.

    Args:
        reminder_id: Primary key of the reminder.
        db_path: Optional database file path.

    Returns:
        bool: True if reminder was updated, False if reminder not found or not active.
    """
    if not isinstance(reminder_id, int) or isinstance(reminder_id, bool):
        return False

    current_utc = datetime.now(timezone.utc).isoformat()
    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE reminders
                SET status = 'sent', sent_at = ?
                WHERE id = ? AND status IN ('pending', 'processing');
                """,
                (current_utc, reminder_id),
            )
            return cursor.rowcount > 0
    finally:
        conn.close()


def mark_reminder_failed(
    reminder_id: int,
    max_retries: int = 3,
    db_path: Optional[str] = None,
) -> bool:
    """Handles reminder dispatch failure, incrementing retries or marking failed.

    If retry_count + 1 >= max_retries, sets status to 'failed'.
    Otherwise resets or keeps status as 'pending' for retry.

    Args:
        reminder_id: Primary key of the reminder.
        max_retries: Retry ceiling before marking permanently failed (default 3).
        db_path: Optional database file path.

    Returns:
        bool: True if updated, False if reminder not found or not eligible.
    """
    if not isinstance(reminder_id, int) or isinstance(reminder_id, bool):
        return False

    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE reminders
                SET retry_count = retry_count + 1,
                    status = CASE WHEN retry_count + 1 >= ? THEN 'failed' ELSE 'pending' END
                WHERE id = ? AND status IN ('pending', 'processing');
                """,
                (max_retries, reminder_id),
            )
            return cursor.rowcount > 0
    finally:
        conn.close()


def cancel_reminder(reminder_id: int, db_path: Optional[str] = None) -> bool:
    """Cancels an active reminder.

    Args:
        reminder_id: Primary key of the reminder.
        db_path: Optional database file path.

    Returns:
        bool: True if reminder was cancelled, False if already sent/cancelled or not found.
    """
    if not isinstance(reminder_id, int) or isinstance(reminder_id, bool):
        return False

    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE reminders
                SET status = 'cancelled'
                WHERE id = ? AND status NOT IN ('sent', 'cancelled');
                """,
                (reminder_id,),
            )
            return cursor.rowcount > 0
    finally:
        conn.close()


def snooze_reminder(
    reminder_id: int,
    new_remind_at: str,
    db_path: Optional[str] = None,
) -> bool:
    """Snoozes a reminder to a new scheduled time, resetting status to pending.

    Args:
        reminder_id: Primary key of the reminder.
        new_remind_at: New trigger time in ISO-8601 (naive assumes Asia/Jakarta).
        db_path: Optional database file path.

    Returns:
        bool: True if snoozed successfully, False if reminder cancelled or not found.

    Raises:
        ValueError: If new_remind_at format is invalid.
    """
    if not isinstance(reminder_id, int) or isinstance(reminder_id, bool):
        return False

    utc_new_time = _parse_to_utc_iso(new_remind_at)

    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE reminders
                SET remind_at = ?,
                    status = 'pending',
                    retry_count = 0,
                    sent_at = NULL
                WHERE id = ? AND status != 'cancelled';
                """,
                (utc_new_time, reminder_id),
            )
            return cursor.rowcount > 0
    finally:
        conn.close()
