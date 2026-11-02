"""Daily Automation Aggregations (Morning Briefing & Evening Review).

Collects data from tasks, finance, and reminders domains, formats into
clean deterministic WhatsApp text messages (in Indonesian, 0 LLM tokens),
and persists historical daily review records to SQLite.
"""

from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any, Optional
from zoneinfo import ZoneInfo

from config.settings import TIMEZONE
from src.database.connection import get_db_connection
from src.domain.finance import get_financial_summary
from src.domain.reminders import get_due_reminders
from src.domain.tasks import list_tasks

INDONESIAN_MONTHS = [
    "",
    "Januari",
    "Februari",
    "Maret",
    "April",
    "Mei",
    "Juni",
    "Juli",
    "Agustus",
    "September",
    "Oktober",
    "November",
    "Desember",
]


def _validate_and_parse_date(date_str: Optional[str]) -> tuple[str, datetime.date]:
    """Validates and parses YYYY-MM-DD date string or defaults to local today.

    Returns:
        tuple of (date_str, date_obj).

    Raises:
        ValueError: If date_str is provided but not in valid YYYY-MM-DD format.
    """
    local_tz = ZoneInfo(TIMEZONE)
    if date_str is None:
        local_now = datetime.now(local_tz)
        return local_now.strftime("%Y-%m-%d"), local_now.date()

    cleaned = str(date_str).strip()
    if not cleaned or not re.match(r"^\d{4}-\d{2}-\d{2}$", cleaned):
        raise ValueError(f"Invalid date format: '{date_str}'. Expected YYYY-MM-DD.")

    try:
        dt = datetime.strptime(cleaned, "%Y-%m-%d").date()
    except ValueError as e:
        raise ValueError(f"Invalid calendar date: '{date_str}'. Expected YYYY-MM-DD.") from e

    return cleaned, dt


def _get_utc_day_bounds(date_obj: datetime.date) -> tuple[str, str]:
    """Returns (start_utc_iso, end_utc_iso) for the entire 24h day in Asia/Jakarta."""
    local_tz = ZoneInfo(TIMEZONE)
    start_local = datetime.combine(date_obj, datetime.min.time(), tzinfo=local_tz)
    end_local = start_local + timedelta(days=1)
    start_utc_iso = start_local.astimezone(timezone.utc).isoformat()
    end_utc_iso = end_local.astimezone(timezone.utc).isoformat()
    return start_utc_iso, end_utc_iso


def _format_indonesian_date(date_str: str) -> str:
    """Formats 'YYYY-MM-DD' into 'DD MMMM YYYY' in Indonesian."""
    try:
        dt = datetime.strptime(date_str.strip(), "%Y-%m-%d")
        month_name = INDONESIAN_MONTHS[dt.month]
        return f"{dt.day:02d} {month_name} {dt.year}"
    except Exception:
        return date_str


def _extract_local_time(dt_str: Optional[str]) -> Optional[str]:
    """Extracts 'HH:MM' localized to Asia/Jakarta from an ISO-8601 string."""
    if not dt_str:
        return None
    try:
        clean = dt_str.strip()
        dt = datetime.fromisoformat(clean)
        local_tz = ZoneInfo(TIMEZONE)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=local_tz)
        else:
            dt = dt.astimezone(local_tz)
        return dt.strftime("%H:%M")
    except Exception:
        return None


def format_idr(amount: int) -> str:
    """Formats an integer into clean Indonesian Rupiah string: e.g. Rp 25.000 or -Rp 50.000."""
    if not isinstance(amount, (int, float)):
        try:
            amount = int(amount)
        except Exception:
            return f"Rp {amount}"

    amt_int = int(round(amount))
    if amt_int < 0:
        formatted = f"{abs(amt_int):,}".replace(",", ".")
        return f"-Rp {formatted}"
    formatted = f"{amt_int:,}".replace(",", ".")
    return f"Rp {formatted}"


def generate_morning_briefing(
    target_date: Optional[str] = None,
    db_path: Optional[str] = None,
    date_str: Optional[str] = None,
) -> dict[str, Any]:
    """Aggregates data for morning briefing at 07:00 WIB.

    Includes:
    - Target date's tasks (agenda for today).
    - Overdue open tasks and high-priority / urgent open tasks.
    - Today's pending reminders (enriched with entity titles).
    - Yesterday's finance recap (total expense and category breakdown).

    Args:
        target_date: Optional YYYY-MM-DD date string (defaults to local today).
        db_path: Optional SQLite database file path.
        date_str: Alias for target_date.

    Returns:
        dict: Morning briefing summary data.
    """
    actual_date_str = target_date if target_date is not None else date_str
    target_date_str, target_date_obj = _validate_and_parse_date(actual_date_str)

    yesterday_obj = target_date_obj - timedelta(days=1)
    yesterday_date_str = yesterday_obj.strftime("%Y-%m-%d")

    start_utc, end_utc = _get_utc_day_bounds(target_date_obj)
    y_start_utc, y_end_utc = _get_utc_day_bounds(yesterday_obj)

    # 1. Target date's scheduled tasks
    today_tasks = list_tasks(status="all", due_date=target_date_str, db_path=db_path)

    # 2. Overdue and High Priority Tasks
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()

        # Overdue open tasks
        cursor.execute(
            """
            SELECT * FROM tasks
            WHERE status = 'open' AND due_at IS NOT NULL AND due_at < ?
            ORDER BY due_at ASC;
            """,
            (start_utc,),
        )
        overdue_tasks = [dict(row) for row in cursor.fetchall()]

        # High priority / urgent open tasks
        cursor.execute(
            """
            SELECT * FROM tasks
            WHERE status = 'open' AND priority IN ('high', 'urgent')
            ORDER BY CASE priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 2 ELSE 3 END, due_at ASC;
            """,
        )
        high_priority_tasks = [dict(row) for row in cursor.fetchall()]

        # 3. Today's pending reminders (due up to end of today)
        raw_reminders = get_due_reminders(now_utc=end_utc, limit=100, db_path=db_path)
        reminders: list[dict[str, Any]] = []
        for r in raw_reminders:
            item = dict(r)
            entity_type = item.get("entity_type")
            entity_id = item.get("entity_id")
            entity_title = None

            if entity_type == "task" and entity_id is not None:
                cursor.execute("SELECT title FROM tasks WHERE id = ?;", (entity_id,))
                t_row = cursor.fetchone()
                if t_row:
                    entity_title = t_row["title"]
            elif entity_type == "note" and entity_id is not None:
                cursor.execute("SELECT title FROM notes WHERE id = ?;", (entity_id,))
                n_row = cursor.fetchone()
                if n_row:
                    entity_title = n_row["title"]

            item["entity_title"] = entity_title
            reminders.append(item)

        # 4. Yesterday's finance recap
        y_fin_summary = get_financial_summary(
            start_date=yesterday_date_str,
            end_date=yesterday_date_str,
            db_path=db_path,
        )

        # Top expense categories for yesterday
        cursor.execute(
            """
            SELECT COALESCE(c.name, 'Lain-lain') AS category, SUM(t.amount_idr) AS total
            FROM transactions t
            LEFT JOIN categories c ON t.category_id = c.id
            WHERE t.type = 'expense' AND t.status != 'voided' AND t.occurred_at >= ? AND t.occurred_at < ?
            GROUP BY COALESCE(c.name, 'Lain-lain')
            ORDER BY total DESC;
            """,
            (y_start_utc, y_end_utc),
        )
        top_categories = [
            {"category": row["category"], "amount": int(row["total"])}
            for row in cursor.fetchall()
        ]

        yesterday_finance = {
            "total_expense": y_fin_summary["total_expense"],
            "total_income": y_fin_summary["total_income"],
            "net_cash_flow": y_fin_summary["net_cash_flow"],
            "category_breakdown": y_fin_summary["category_breakdown"],
            "top_categories": top_categories,
        }
    finally:
        conn.close()

    return {
        "target_date": target_date_str,
        "date": target_date_str,
        "yesterday_date": yesterday_date_str,
        "today_tasks": today_tasks,
        "overdue_tasks": overdue_tasks,
        "high_priority_tasks": high_priority_tasks,
        "reminders": reminders,
        "pending_reminders": reminders,
        "yesterday_finance": yesterday_finance,
    }


def generate_evening_review(
    target_date: Optional[str] = None,
    db_path: Optional[str] = None,
    date_str: Optional[str] = None,
) -> dict[str, Any]:
    """Aggregates data for evening review at 21:30 WIB.

    Includes:
    - Today's completed tasks count vs remaining pending tasks count + completion rate percentage.
    - Today's financial summary (income, expense, net cash flow).
    - Preview of tomorrow's scheduled tasks/agenda.

    Args:
        target_date: Optional YYYY-MM-DD date string (defaults to local today).
        db_path: Optional SQLite database file path.
        date_str: Alias for target_date.

    Returns:
        dict: Evening review summary data.
    """
    actual_date_str = target_date if target_date is not None else date_str
    target_date_str, target_date_obj = _validate_and_parse_date(actual_date_str)

    tomorrow_obj = target_date_obj + timedelta(days=1)
    tomorrow_date_str = tomorrow_obj.strftime("%Y-%m-%d")

    start_utc, end_utc = _get_utc_day_bounds(target_date_obj)

    # 1. Today's task metrics
    tasks_today = list_tasks(status="all", due_date=target_date_str, db_path=db_path)

    completed_map: dict[int, dict[str, Any]] = {}
    pending_map: dict[int, dict[str, Any]] = {}

    for t in tasks_today:
        if t["status"] == "done":
            completed_map[t["id"]] = t
        elif t["status"] == "open":
            pending_map[t["id"]] = t

    # Also capture any tasks marked done today even if without due_at or different due_at
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM tasks
            WHERE status = 'done' AND updated_at >= ? AND updated_at < ?;
            """,
            (start_utc, end_utc),
        )
        for row in cursor.fetchall():
            task_dict = dict(row)
            completed_map[task_dict["id"]] = task_dict
    finally:
        conn.close()

    completed_tasks = list(completed_map.values())
    pending_tasks = list(pending_map.values())

    completed_count = len(completed_tasks)
    pending_count = len(pending_tasks)
    total_tasks = completed_count + pending_count

    if total_tasks > 0:
        completion_rate = round((completed_count / total_tasks) * 100, 1)
    else:
        completion_rate = 0.0

    # 2. Today's financial summary
    fin_summary = get_financial_summary(
        start_date=target_date_str,
        end_date=target_date_str,
        db_path=db_path,
    )
    finance = {
        "total_income": fin_summary["total_income"],
        "total_expense": fin_summary["total_expense"],
        "net_cash_flow": fin_summary["net_cash_flow"],
        "category_breakdown": fin_summary["category_breakdown"],
    }

    # 3. Tomorrow's scheduled agenda
    tomorrow_tasks = list_tasks(status="all", due_date=tomorrow_date_str, db_path=db_path)

    return {
        "target_date": target_date_str,
        "date": target_date_str,
        "tomorrow_date": tomorrow_date_str,
        "completed_tasks": completed_tasks,
        "pending_tasks": pending_tasks,
        "completed_count": completed_count,
        "pending_count": pending_count,
        "completion_rate": completion_rate,
        "finance": finance,
        "tomorrow_tasks": tomorrow_tasks,
    }


def format_report_text(report_data: dict[str, Any], report_type: str) -> str:
    """Formats clean, human-readable WhatsApp messages in Indonesian (0 LLM tokens).

    Args:
        report_data: Dictionary returned by generate_morning_briefing or generate_evening_review.
        report_type: 'morning' or 'evening'.

    Returns:
        str: Formatted WhatsApp message with bold titles and emojis.

    Raises:
        ValueError: If report_type is not 'morning' or 'evening'.
    """
    if not isinstance(report_type, str) or report_type.lower().strip() not in ("morning", "evening"):
        raise ValueError(f"Invalid report_type: '{report_type}'. Expected 'morning' or 'evening'.")

    norm_type = report_type.lower().strip()
    target_date = report_data.get("target_date") or report_data.get("date", "")
    date_display = _format_indonesian_date(target_date)

    lines: list[str] = []

    if norm_type == "morning":
        lines.append(f"🌅 *Morning Briefing - {date_display}*")
        lines.append("Semangat pagi! Berikut ringkasan agenda & catatan untuk hari ini:\n")

        # 1. Agenda Hari Ini
        lines.append("📋 *Agenda Hari Ini:*")
        today_tasks = report_data.get("today_tasks", [])
        if today_tasks:
            for t in today_tasks:
                priority_tag = (
                    f" [{t['priority'].capitalize()}]"
                    if t.get("priority") and t.get("priority") != "normal"
                    else ""
                )
                due_time = _extract_local_time(t.get("due_at"))
                time_tag = f" ({due_time})" if due_time else ""
                status_icon = "✅ " if t.get("status") == "done" else "• "
                lines.append(f"{status_icon}{t['title']}{time_tag}{priority_tag}")
        else:
            lines.append("- Tidak ada tugas terjadwal untuk hari ini.")
        lines.append("")

        # 2. Perlu Perhatian (Overdue & High Priority)
        overdue_tasks = report_data.get("overdue_tasks", [])
        high_priority = report_data.get("high_priority_tasks", [])
        today_ids = {t["id"] for t in today_tasks if "id" in t}
        overdue_ids = {t["id"] for t in overdue_tasks if "id" in t}
        extra_urgent = [
            t for t in high_priority
            if t.get("id") not in today_ids and t.get("id") not in overdue_ids
        ]

        if overdue_tasks or extra_urgent:
            lines.append("⚠️ *Perlu Perhatian:*")
            for t in overdue_tasks:
                lines.append(f"• [Overdue] {t['title']}")
            for t in extra_urgent:
                lines.append(f"• [{t['priority'].capitalize()}] {t['title']}")
            lines.append("")

        # 3. Pengingat Hari Ini
        lines.append("⏰ *Pengingat Hari Ini:*")
        reminders = report_data.get("reminders") or report_data.get("pending_reminders", [])
        if reminders:
            for r in reminders:
                r_time = _extract_local_time(r.get("remind_at"))
                time_prefix = f"{r_time} - " if r_time else ""
                title = r.get("entity_title") or f"{r.get('entity_type', 'item').capitalize()} #{r.get('entity_id', '')}"
                lines.append(f"• {time_prefix}{title}")
        else:
            lines.append("- Tidak ada pengingat hari ini.")
        lines.append("")

        # 4. Pengeluaran Kemarin
        yesterday_date_str = report_data.get("yesterday_date", "")
        y_display = _format_indonesian_date(yesterday_date_str) if yesterday_date_str else "Kemarin"
        y_fin = report_data.get("yesterday_finance", {})
        total_exp = y_fin.get("total_expense", 0)
        lines.append(f"💰 *Pengeluaran Kemarin ({y_display}):*")
        lines.append(f"• Total: {format_idr(total_exp)}")
        cats = y_fin.get("category_breakdown", {})
        if cats:
            cat_items = [f"{k}: {format_idr(v)}" for k, v in list(cats.items())[:3]]
            lines.append(f"• Kategori Teratas: {', '.join(cat_items)}")
        lines.append("")

        lines.append("Semoga harimu produktif dan berkah! 🚀")

    elif norm_type == "evening":
        lines.append(f"🌙 *Evening Review - {date_display}*")
        lines.append("Selamat malam! Berikut rangkuman aktivitasmu hari ini:\n")

        # 1. Produktivitas Tugas
        done_count = report_data.get("completed_count", 0)
        open_count = report_data.get("pending_count", 0)
        comp_rate = report_data.get("completion_rate", 0.0)
        lines.append("📊 *Produktivitas Tugas:*")
        lines.append(f"• Selesai: {done_count} tugas")
        lines.append(f"• Tertunda: {open_count} tugas")
        lines.append(f"• Tingkat Penyelesaian: {comp_rate:.1f}%")

        completed_tasks = report_data.get("completed_tasks", [])
        if completed_tasks:
            for t in completed_tasks[:3]:
                lines.append(f"  ✅ {t['title']}")
        pending_tasks = report_data.get("pending_tasks", [])
        if pending_tasks:
            for t in pending_tasks[:3]:
                lines.append(f"  ⏳ {t['title']}")
        lines.append("")

        # 2. Keuangan Hari Ini
        fin = report_data.get("finance", {})
        inc = fin.get("total_income", 0)
        exp = fin.get("total_expense", 0)
        net = fin.get("net_cash_flow", 0)
        lines.append("💰 *Keuangan Hari Ini:*")
        lines.append(f"• Pemasukan: {format_idr(inc)}")
        lines.append(f"• Pengeluaran: {format_idr(exp)}")
        lines.append(f"• Arus Kas Bersih: {format_idr(net)}")
        lines.append("")

        # 3. Agenda Besok
        tomorrow_date_str = report_data.get("tomorrow_date", "")
        t_display = _format_indonesian_date(tomorrow_date_str) if tomorrow_date_str else "Besok"
        tomorrow_tasks = report_data.get("tomorrow_tasks", [])
        lines.append(f"📅 *Agenda Besok ({t_display}):*")
        if tomorrow_tasks:
            for t in tomorrow_tasks:
                priority_tag = (
                    f" [{t['priority'].capitalize()}]"
                    if t.get("priority") and t.get("priority") != "normal"
                    else ""
                )
                due_time = _extract_local_time(t.get("due_at"))
                time_tag = f" ({due_time})" if due_time else ""
                lines.append(f"• {t['title']}{time_tag}{priority_tag}")
        else:
            lines.append("- Belum ada tugas dijadwalkan untuk besok.")
        lines.append("")

        lines.append("Selamat beristirahat dan siapkan energimu untuk besok! 😴")

    return "\n".join(lines)


def save_daily_review(
    review_date: str,
    summary_data: dict[str, Any],
    delivery_status: str = "sent",
    db_path: Optional[str] = None,
) -> int:
    """Persists a daily review record into the SQLite daily_reviews table.

    Args:
        review_date: YYYY-MM-DD date string.
        summary_data: Review / briefing dictionary.
        delivery_status: Delivery status string (default 'sent').
        db_path: Optional database path.

    Returns:
        int: The auto-generated daily review record ID.

    Raises:
        ValueError: If review_date format is invalid or summary_data is not a dict.
    """
    valid_date_str, _ = _validate_and_parse_date(review_date)

    if not isinstance(summary_data, dict):
        raise ValueError("summary_data must be a dictionary.")

    norm_status = delivery_status.lower().strip() if delivery_status else "sent"
    summary_json = json.dumps(summary_data, ensure_ascii=False)
    generated_at_utc = datetime.now(timezone.utc).isoformat()

    conn = get_db_connection(db_path)
    try:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO daily_reviews (review_date, summary_json, generated_at, delivery_status)
                VALUES (?, ?, ?, ?);
                """,
                (valid_date_str, summary_json, generated_at_utc, norm_status),
            )
            return cursor.lastrowid
    finally:
        conn.close()


def get_daily_review(
    review_date: str,
    db_path: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Retrieves the latest daily review record for the specified date.

    Args:
        review_date: YYYY-MM-DD date string.
        db_path: Optional database path.

    Returns:
        dict representation of the record or None if not found.
    """
    valid_date_str, _ = _validate_and_parse_date(review_date)

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM daily_reviews
            WHERE review_date = ?
            ORDER BY id DESC
            LIMIT 1;
            """,
            (valid_date_str,),
        )
        row = cursor.fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()
