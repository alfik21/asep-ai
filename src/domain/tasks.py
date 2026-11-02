"""Task and Schedule Manager domain logic.

Provides CRUD and lifecycle operations for tasks with timezone-aware handling,
persisting timestamps in ISO-8601 UTC while localizing schedule queries to Asia/Jakarta.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from config.settings import TIMEZONE
from src.database.connection import get_db_connection

ALLOWED_PRIORITIES = {"low", "normal", "high", "urgent"}
ALLOWED_STATUSES = {"open", "done", "cancelled", "archived"}
UPDATABLE_FIELDS = {"title", "description", "due_at", "priority", "status"}


def _parse_to_utc_iso(dt_input: Optional[str]) -> Optional[str]:
    """Parses a datetime string and converts it to an ISO-8601 UTC string.

    If input has no timezone info, it is assumed to be in the local application
    timezone (Asia/Jakarta) and converted to UTC.
    """
    if dt_input is None:
        return None

    cleaned = dt_input.strip() if isinstance(dt_input, str) else str(dt_input)
    if not cleaned:
        return None

    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError as e:
        raise ValueError(f"Invalid datetime format: '{dt_input}'. Expected ISO-8601 format.") from e

    if dt.tzinfo is None:
        local_tz = ZoneInfo(TIMEZONE)
        dt = dt.replace(tzinfo=local_tz)

    utc_dt = dt.astimezone(timezone.utc)
    return utc_dt.isoformat()


def create_task(
    title: str,
    description: Optional[str] = None,
    due_at: Optional[str] = None,
    priority: str = "normal",
    db_path: Optional[str] = None,
) -> int:
    """Creates a new task in the database.

    Args:
        title: Task title, must be a non-empty string.
        description: Optional task details.
        due_at: Optional ISO-8601 due date/time string.
        priority: 'low', 'normal', 'high', or 'urgent'. Defaults to 'normal'.
        db_path: Optional path to SQLite database.

    Returns:
        int: The auto-generated task ID.

    Raises:
        ValueError: If title is empty, or priority/due_at format is invalid.
    """
    if not title or not isinstance(title, str) or not title.strip():
        raise ValueError("Task title must be a non-empty string.")

    norm_priority = priority.lower().strip() if priority else ""
    if norm_priority not in ALLOWED_PRIORITIES:
        raise ValueError(
            f"Invalid priority '{priority}'. Allowed values: {sorted(ALLOWED_PRIORITIES)}"
        )

    utc_due_at = _parse_to_utc_iso(due_at)
    now_utc_iso = datetime.now(timezone.utc).isoformat()
    status = "open"

    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO tasks (title, description, due_at, priority, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    title.strip(),
                    description.strip() if isinstance(description, str) else description,
                    utc_due_at,
                    norm_priority,
                    status,
                    now_utc_iso,
                    now_utc_iso,
                ),
            )
            return cursor.lastrowid
    finally:
        conn.close()


def get_task(task_id: int, db_path: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Retrieves a single task by ID.

    Args:
        task_id: The ID of the task to retrieve.
        db_path: Optional path to SQLite database.

    Returns:
        dict representation of the task or None if not found.
    """
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE id = ?;", (task_id,))
        row = cursor.fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def list_tasks(
    status: Optional[str] = "open",
    due_date: Optional[str] = None,
    db_path: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Lists tasks with optional status and due date filters.

    Args:
        status: Status filter ('open', 'done', 'cancelled', 'archived', or 'all'/None).
        due_date: YYYY-MM-DD date string in local timezone (Asia/Jakarta).
        db_path: Optional path to SQLite database.

    Returns:
        list of task dicts matching the criteria.
    """
    conditions: list[str] = []
    params: list[Any] = []

    if status is not None and status.lower().strip() != "all":
        norm_status = status.lower().strip()
        if norm_status not in ALLOWED_STATUSES:
            raise ValueError(
                f"Invalid status '{status}'. Allowed values: {sorted(ALLOWED_STATUSES)}"
            )
        conditions.append("status = ?")
        params.append(norm_status)

    if due_date is not None:
        try:
            local_date = datetime.strptime(due_date.strip(), "%Y-%m-%d").date()
        except ValueError as e:
            raise ValueError(f"Invalid due_date format: '{due_date}'. Expected YYYY-MM-DD.") from e

        local_tz = ZoneInfo(TIMEZONE)
        start_local = datetime.combine(local_date, datetime.min.time(), tzinfo=local_tz)
        end_local = start_local + timedelta(days=1)
        start_utc_iso = start_local.astimezone(timezone.utc).isoformat()
        end_utc_iso = end_local.astimezone(timezone.utc).isoformat()

        conditions.append("due_at >= ? AND due_at < ?")
        params.extend([start_utc_iso, end_utc_iso])

    query = "SELECT * FROM tasks"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY id ASC;"

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def update_task(task_id: int, db_path: Optional[str] = None, **kwargs: Any) -> bool:
    """Updates fields of an existing task.

    Args:
        task_id: ID of the task to update.
        db_path: Optional path to SQLite database.
        **kwargs: Fields to update (title, description, due_at, priority, status).

    Returns:
        bool: True if task was updated, False if task does not exist.

    Raises:
        ValueError: If invalid fields or values are passed.
    """
    if not kwargs:
        return False

    invalid_fields = set(kwargs.keys()) - UPDATABLE_FIELDS
    if invalid_fields:
        raise ValueError(
            f"Invalid fields for update: {sorted(invalid_fields)}. Allowed: {sorted(UPDATABLE_FIELDS)}"
        )

    assignments: list[str] = []
    params: list[Any] = []

    if "title" in kwargs:
        title = kwargs["title"]
        if not title or not isinstance(title, str) or not title.strip():
            raise ValueError("Task title must be a non-empty string.")
        assignments.append("title = ?")
        params.append(title.strip())

    if "description" in kwargs:
        desc = kwargs["description"]
        assignments.append("description = ?")
        params.append(desc.strip() if isinstance(desc, str) else desc)

    if "due_at" in kwargs:
        utc_due_at = _parse_to_utc_iso(kwargs["due_at"])
        assignments.append("due_at = ?")
        params.append(utc_due_at)

    if "priority" in kwargs:
        prio = kwargs["priority"]
        norm_prio = prio.lower().strip() if prio else ""
        if norm_prio not in ALLOWED_PRIORITIES:
            raise ValueError(
                f"Invalid priority '{prio}'. Allowed values: {sorted(ALLOWED_PRIORITIES)}"
            )
        assignments.append("priority = ?")
        params.append(norm_prio)

    if "status" in kwargs:
        st = kwargs["status"]
        norm_st = st.lower().strip() if st else ""
        if norm_st not in ALLOWED_STATUSES:
            raise ValueError(
                f"Invalid status '{st}'. Allowed values: {sorted(ALLOWED_STATUSES)}"
            )
        assignments.append("status = ?")
        params.append(norm_st)

    now_utc_iso = datetime.now(timezone.utc).isoformat()
    assignments.append("updated_at = ?")
    params.append(now_utc_iso)

    params.append(task_id)
    set_clause = ", ".join(assignments)
    query = f"UPDATE tasks SET {set_clause} WHERE id = ?;"

    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(query, tuple(params))
            return cursor.rowcount > 0
    finally:
        conn.close()


def mark_task_status(task_id: int, status: str, db_path: Optional[str] = None) -> bool:
    """Updates the status and updated_at timestamp of a task.

    Args:
        task_id: ID of the task.
        status: New status ('open', 'done', 'cancelled', 'archived').
        db_path: Optional path to SQLite database.

    Returns:
        bool: True if status updated, False if task does not exist.

    Raises:
        ValueError: If status is not an allowed value.
    """
    norm_status = status.lower().strip() if status else ""
    if norm_status not in ALLOWED_STATUSES:
        raise ValueError(
            f"Invalid status '{status}'. Allowed values: {sorted(ALLOWED_STATUSES)}"
        )

    now_utc_iso = datetime.now(timezone.utc).isoformat()

    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?;",
                (norm_status, now_utc_iso, task_id),
            )
            return cursor.rowcount > 0
    finally:
        conn.close()
