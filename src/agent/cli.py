"""Unified CLI Dispatcher & Zero-Token Router for ASEP-AI.

Provides deterministic CLI subcommands and a fast zero-token `dispatch_command`
function that routes WhatsApp slash commands (e.g. /today, /tasks, /rekap, /status)
directly to domain functions without LLM token overhead on 1GB RAM Ubuntu VPS.
"""

import argparse
from datetime import datetime
import shlex
import sys
from typing import Any, List, Optional
from zoneinfo import ZoneInfo

from config.settings import TIMEZONE
from src.domain.finance import (
    _parse_amount_idr,
    add_transaction,
    get_financial_summary,
)
from src.domain.notes import list_notes, save_note, search_notes
from src.domain.reminders import create_reminder, get_due_reminders
from src.domain.reports import (
    format_idr,
    format_report_text,
    generate_evening_review,
    generate_morning_briefing,
)
from src.domain.tasks import create_task, list_tasks, mark_task_status
from src.system.health import format_system_health, get_system_health


def _format_to_wib(dt_str: Optional[str]) -> Optional[str]:
    """Formats an ISO-8601 UTC string into local Asia/Jakarta (WIB) time.

    Returns string formatted as 'YYYY-MM-DD HH:MM WIB'.
    """
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
        return dt.strftime("%Y-%m-%d %H:%M WIB")
    except Exception:
        return dt_str


class DispatchArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that raises ValueError instead of terminating the process."""

    def error(self, message: str) -> None:
        raise ValueError(message)

    def exit(self, status: int = 0, message: Optional[str] = None) -> None:
        raise ValueError(message or "Help requested")


def _get_help_text() -> str:
    return (
        "🤖 *ASEP-AI Perintah Tersedia:*\n"
        "• `/today` - Lihat agenda tugas dan pengingat hari ini\n"
        "• `/tasks` - Kelola & lihat daftar tugas (`--create`, `--done`, `--status`, `--due`)\n"
        "• `/finance` - Catat transaksi & rekap (`--add`, `--type`, `--amount`, `--desc`, `--rekap`)\n"
        "• `/rekap` - Shortcut rekap keuangan bulan ini (`--start`, `--end`)\n"
        "• `/notes` - Simpan & cari catatan (`--save`, `--search`, `--tag`, `--title`)\n"
        "• `/reminders` - Pengingat terjadwal (`--check-due`, `--create`)\n"
        "• `/briefing` - Morning briefing harian (`--date`)\n"
        "• `/review` - Evening review harian (`--date`)\n"
        "• `/status` - Cek kesehatan sistem VPS & database"
    )


def _handle_tasks(args: argparse.Namespace, db_path: Optional[str]) -> str:
    # 1. Mark task as done
    if args.done is not None:
        try:
            task_id = int(args.done)
        except ValueError:
            return f"❌ ID tugas tidak valid: '{args.done}'"

        success = mark_task_status(task_id, status="done", db_path=db_path)
        if success:
            return f"✅ Tugas #{task_id} berhasil ditandai selesai! 🎉"
        return f"❌ Tugas #{task_id} tidak ditemukan."

    # 2. Create task
    if args.create:
        title = args.create.strip()
        due_at = args.due.strip() if args.due else None
        priority = args.priority.strip() if args.priority else "normal"
        description = args.desc.strip() if args.desc else None

        try:
            task_id = create_task(
                title=title,
                description=description,
                due_at=due_at,
                priority=priority,
                db_path=db_path,
            )
            wib_due = _format_to_wib(due_at)
            due_str = f"\n• Tenggat: {wib_due}" if wib_due else ""
            return (
                f"✅ *Tugas berhasil dibuat!*\n"
                f"• ID: #{task_id}\n"
                f"• Judul: {title}\n"
                f"• Prioritas: {priority.capitalize()}{due_str}"
            )
        except Exception as e:
            return f"❌ Gagal membuat tugas: {e}"

    # 3. List tasks
    try:
        tasks = list_tasks(status=args.status or "open", due_date=args.due, db_path=db_path)
        if not tasks:
            status_label = f" (status: {args.status})" if args.status else ""
            return f"ℹ️ Tidak ada tugas yang ditemukan{status_label}."

        lines = [f"📋 *Daftar Tugas ({len(tasks)} ditemukan):*"]
        for t in tasks:
            status_icon = "✅" if t.get("status") == "done" else "◻️"
            prio_val = t.get("priority", "normal")
            prio_badge = f" [{prio_val.capitalize()}]" if prio_val != "normal" else ""
            due_at = t.get("due_at")
            wib_due = _format_to_wib(due_at)
            due_badge = f" (Tenggat: {wib_due})" if wib_due else ""
            lines.append(f"{status_icon} *#{t['id']}* {t['title']}{prio_badge}{due_badge}")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ Gagal mengambil daftar tugas: {e}"


def _handle_today(args: argparse.Namespace, db_path: Optional[str]) -> str:
    local_tz = ZoneInfo(TIMEZONE)
    local_today = datetime.now(local_tz).strftime("%Y-%m-%d")

    try:
        tasks = list_tasks(status="all", due_date=local_today, db_path=db_path)
        reminders = get_due_reminders(limit=20, db_path=db_path)

        lines = [f"📅 *Agenda Hari Ini ({local_today})*", ""]

        lines.append("📋 *Tugas Terjadwal:*")
        if tasks:
            for t in tasks:
                status_icon = "✅" if t.get("status") == "done" else "•"
                prio_badge = (
                    f" [{t['priority'].capitalize()}]"
                    if t.get("priority") and t.get("priority") != "normal"
                    else ""
                )
                wib_due = _format_to_wib(t.get("due_at"))
                due_badge = f" (Tenggat: {wib_due})" if wib_due else ""
                lines.append(f"{status_icon} *#{t['id']}* {t['title']}{prio_badge}{due_badge}")
        else:
            lines.append("- Tidak ada tugas untuk hari ini.")

        lines.append("")
        lines.append("⏰ *Pengingat Tertunda / Jatuh Tempo:*")
        if reminders:
            for r in reminders:
                wib_time = _format_to_wib(r.get("remind_at"))
                time_badge = f" (Waktu: {wib_time})" if wib_time else ""
                lines.append(
                    f"• *#{r['id']}* {r['entity_type']} #{r['entity_id']}{time_badge}"
                )
        else:
            lines.append("- Tidak ada pengingat tertunda.")

        return "\n".join(lines)
    except Exception as e:
        return f"❌ Gagal memuat agenda hari ini: {e}"


def _format_rekap_summary(summary: dict[str, Any], label: str) -> str:
    total_income = summary.get("total_income", 0)
    total_expense = summary.get("total_expense", 0)
    net_flow = summary.get("net_cash_flow", 0)
    categories = summary.get("category_breakdown", {})

    lines = [
        f"💰 *Rekap Keuangan ({label})*",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"• Total Pemasukan: {format_idr(total_income)}",
        f"• Total Pengeluaran: {format_idr(total_expense)}",
        f"• Arus Kas Bersih: {format_idr(net_flow)}",
    ]

    if categories:
        lines.append("\n📂 *Rincian Kategori:*")
        for cat, amt in categories.items():
            lines.append(f"• {cat}: {format_idr(amt)}")

    return "\n".join(lines)


def _handle_finance(args: argparse.Namespace, db_path: Optional[str]) -> str:
    # 1. Add transaction
    if args.add or (args.type and args.amount is not None):
        if not args.type:
            return "❌ Tipe transaksi wajib ditentukan (--type income/expense)."
        if args.amount is None:
            return "❌ Jumlah transaksi wajib ditentukan (--amount <nilai>)."

        try:
            parsed_amount = _parse_amount_idr(args.amount)
        except Exception as e:
            return f"❌ Jumlah transaksi tidak valid: {e}"

        try:
            tx_id = add_transaction(
                type=args.type,
                amount_idr=parsed_amount,
                description=args.desc,
                category=args.category,
                payment_method=args.payment,
                db_path=db_path,
            )
            return (
                f"✅ *Transaksi berhasil dicatat!*\n"
                f"• ID: #{tx_id}\n"
                f"• Tipe: {args.type.capitalize()}\n"
                f"• Jumlah: {format_idr(parsed_amount)}\n"
                f"• Deskripsi: {args.desc or '-'}\n"
                f"• Kategori: {args.category or '-'}"
            )
        except Exception as e:
            return f"❌ Gagal mencatat transaksi: {e}"

    # 2. View rekap
    return _handle_rekap(args, db_path)


def _handle_rekap(args: argparse.Namespace, db_path: Optional[str]) -> str:
    try:
        start_date = getattr(args, "start", None)
        end_date = getattr(args, "end", None)

        if start_date or end_date:
            label = f"{start_date or 'Awal'} s/d {end_date or 'Sekarang'}"
        else:
            local_tz = ZoneInfo(TIMEZONE)
            now = datetime.now(local_tz)
            start_date = now.strftime("%Y-%m-01")
            end_date = now.strftime("%Y-%m-%d")
            label = f"Bulan {now.strftime('%B %Y')}"

        summary = get_financial_summary(
            start_date=start_date,
            end_date=end_date,
            db_path=db_path,
        )
        return _format_rekap_summary(summary, label)
    except Exception as e:
        return f"❌ Gagal memuat rekap keuangan: {e}"


def _handle_notes(args: argparse.Namespace, db_path: Optional[str]) -> str:
    # 1. Save note
    if args.save:
        try:
            tags = [t.strip() for t in args.tag.split(",")] if args.tag else None
            note_id = save_note(
                content=args.save,
                title=args.title,
                category=args.category,
                tags=tags,
                db_path=db_path,
            )
            tag_display = f"\n• Tag: {', '.join(tags)}" if tags else ""
            title_display = f"\n• Judul: {args.title}" if args.title else ""
            return (
                f"📝 *Catatan berhasil disimpan!*\n"
                f"• ID: #{note_id}{title_display}\n"
                f"• Konten: {args.save}{tag_display}"
            )
        except Exception as e:
            return f"❌ Gagal menyimpan catatan: {e}"

    # 2. Search note
    if args.search:
        try:
            notes = search_notes(
                keyword=args.search,
                tag=args.tag,
                category=args.category,
                db_path=db_path,
            )
            if not notes:
                return f"ℹ️ Tidak ditemukan catatan dengan kata kunci '{args.search}'."

            lines = [f"📝 *Hasil Pencarian Catatan ({len(notes)} ditemukan):*"]
            for n in notes:
                tag_str = f" [#{', #'.join(n.get('tags', []))}]" if n.get("tags") else ""
                title_str = f"*{n['title']}*: " if n.get("title") else ""
                lines.append(f"• *#{n['id']}* {title_str}{n['content']}{tag_str}")
            return "\n".join(lines)
        except Exception as e:
            return f"❌ Gagal mencari catatan: {e}"

    # 3. List notes (by tag or recent)
    try:
        notes = list_notes(
            tag=args.tag,
            category=args.category,
            limit=20,
            db_path=db_path,
        )
        if not notes:
            return "ℹ️ Tidak ada catatan yang ditemukan."

        lines = [f"📝 *Daftar Catatan ({len(notes)} terbaru):*"]
        for n in notes:
            tag_str = f" [#{', #'.join(n.get('tags', []))}]" if n.get("tags") else ""
            title_str = f"*{n['title']}*: " if n.get("title") else ""
            lines.append(f"• *#{n['id']}* {title_str}{n['content']}{tag_str}")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ Gagal mengambil catatan: {e}"


def _handle_reminders(args: argparse.Namespace, db_path: Optional[str]) -> str:
    # 1. Create reminder
    if getattr(args, "create", False):
        entity_type = getattr(args, "entity_type", None) or "general"
        entity_id = getattr(args, "entity_id", None) or 1
        remind_at = getattr(args, "remind_at", None)

        if not remind_at:
            return "❌ Waktu pengingat wajib ditentukan (--remind-at <ISO-8601>)."

        try:
            rem_id = create_reminder(
                entity_type=entity_type,
                entity_id=int(entity_id),
                remind_at=remind_at,
                db_path=db_path,
            )
            wib_remind = _format_to_wib(remind_at) or remind_at
            return (
                f"⏰ *Pengingat berhasil dibuat!*\n"
                f"• ID: #{rem_id}\n"
                f"• Target: {entity_type} #{entity_id}\n"
                f"• Waktu: {wib_remind}"
            )
        except Exception as e:
            return f"❌ Gagal membuat pengingat: {e}"

    # 2. Check due reminders (default)
    try:
        reminders = get_due_reminders(limit=20, db_path=db_path)
        if not reminders:
            return "ℹ️ Tidak ada pengingat yang jatuh tempo saat ini."

        lines = [f"⏰ *Daftar Pengingat Jatuh Tempo ({len(reminders)}):*"]
        for r in reminders:
            wib_remind = _format_to_wib(r.get("remind_at"))
            time_badge = f" (Waktu: {wib_remind})" if wib_remind else ""
            lines.append(
                f"• *#{r['id']}* [{r['status']}] {r['entity_type']} #{r['entity_id']}{time_badge}"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"❌ Gagal memeriksa pengingat: {e}"


def _handle_briefing(args: argparse.Namespace, db_path: Optional[str]) -> str:
    try:
        data = generate_morning_briefing(target_date=args.date, db_path=db_path)
        return format_report_text(data, report_type="morning")
    except Exception as e:
        return f"❌ Gagal menghasilkan morning briefing: {e}"


def _handle_review(args: argparse.Namespace, db_path: Optional[str]) -> str:
    try:
        data = generate_evening_review(target_date=args.date, db_path=db_path)
        return format_report_text(data, report_type="evening")
    except Exception as e:
        return f"❌ Gagal menghasilkan evening review: {e}"


def _handle_status(args: argparse.Namespace, db_path: Optional[str]) -> str:
    try:
        health_data = get_system_health(db_path=db_path)
        return format_system_health(health_data)
    except Exception as e:
        return f"❌ Gagal memeriksa kesehatan sistem: {e}"


def _build_parser() -> argparse.ArgumentParser:
    parser = DispatchArgumentParser(
        prog="asep-ai",
        description="ASEP-AI Unified CLI & Zero-Token Command Dispatcher",
    )
    parser.add_argument("--db-path", dest="global_db_path", help="Path to SQLite database")

    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # tasks
    tasks_p = subparsers.add_parser("tasks", help="Manage and list tasks")
    tasks_p.add_argument("--create", help="Create new task with given title")
    tasks_p.add_argument("--due", help="Due date (YYYY-MM-DD) or ISO datetime")
    tasks_p.add_argument("--priority", default="normal", help="Priority (low/normal/high/urgent)")
    tasks_p.add_argument("--desc", "--description", dest="desc", help="Task description")
    tasks_p.add_argument("--status", help="Filter by status (open/done/all)")
    tasks_p.add_argument("--done", type=int, help="Mark task ID as done")
    tasks_p.add_argument("--db-path", help="Path to SQLite database")

    # today
    today_p = subparsers.add_parser("today", help="Today's agenda and reminders")
    today_p.add_argument("--db-path", help="Path to SQLite database")

    # finance
    fin_p = subparsers.add_parser("finance", help="Track finances and view recaps")
    fin_p.add_argument("--add", action="store_true", help="Add new transaction")
    fin_p.add_argument("--type", choices=["income", "expense"], help="Transaction type")
    fin_p.add_argument("--amount", help="Amount in IDR")
    fin_p.add_argument("--desc", "--description", dest="desc", help="Description")
    fin_p.add_argument("--category", help="Category name")
    fin_p.add_argument("--payment", help="Payment method")
    fin_p.add_argument("--rekap", action="store_true", help="View financial rekap")
    fin_p.add_argument("--start", help="Start date (YYYY-MM-DD)")
    fin_p.add_argument("--end", help="End date (YYYY-MM-DD)")
    fin_p.add_argument("--db-path", help="Path to SQLite database")

    # rekap
    rekap_p = subparsers.add_parser("rekap", help="Shortcut for financial recap")
    rekap_p.add_argument("--start", help="Start date (YYYY-MM-DD)")
    rekap_p.add_argument("--end", help="End date (YYYY-MM-DD)")
    rekap_p.add_argument("--db-path", help="Path to SQLite database")

    # notes (and alias note)
    notes_p = subparsers.add_parser("notes", help="Save and search notes")
    notes_p.add_argument("--save", help="Note content to save")
    notes_p.add_argument("--title", help="Note title")
    notes_p.add_argument("--category", help="Note category")
    notes_p.add_argument("--tag", help="Filter or save with tag")
    notes_p.add_argument("--search", help="Search keyword")
    notes_p.add_argument("--db-path", help="Path to SQLite database")

    # reminders
    rem_p = subparsers.add_parser("reminders", help="Manage reminders")
    rem_p.add_argument("--check-due", action="store_true", help="Check due reminders")
    rem_p.add_argument("--create", action="store_true", help="Create reminder")
    rem_p.add_argument("--entity-type", default="task", help="Target entity type")
    rem_p.add_argument("--entity-id", type=int, help="Target entity ID")
    rem_p.add_argument("--remind-at", help="Scheduled reminder ISO datetime")
    rem_p.add_argument("--db-path", help="Path to SQLite database")

    # briefing
    brief_p = subparsers.add_parser("briefing", help="Generate morning briefing")
    brief_p.add_argument("--date", help="Target date (YYYY-MM-DD)")
    brief_p.add_argument("--db-path", help="Path to SQLite database")

    # review
    review_p = subparsers.add_parser("review", help="Generate evening review")
    review_p.add_argument("--date", help="Target date (YYYY-MM-DD)")
    review_p.add_argument("--db-path", help="Path to SQLite database")

    # status
    status_p = subparsers.add_parser("status", help="System health status")
    status_p.add_argument("--db-path", help="Path to SQLite database")

    return parser


def dispatch_command(cmd_string: str, db_path: Optional[str] = None) -> str:
    """Takes a raw command string (e.g. '/today', '/tasks', '/rekap', '/status')

    and routes it directly to deterministic domain functions without invoking an LLM.
    Zero-token path for high performance and low VPS memory usage.

    Args:
        cmd_string: Raw text command, with or without leading slash.
        db_path: Optional path to SQLite database.

    Returns:
        Formatted WhatsApp-friendly string response.
    """
    if not cmd_string or not cmd_string.strip():
        return _get_help_text()

    cleaned = cmd_string.strip()
    if cleaned.startswith("/"):
        cleaned = cleaned[1:].strip()

    try:
        tokens = shlex.split(cleaned)
    except Exception as e:
        return f"❌ Format perintah tidak valid: {e}"

    if not tokens:
        return _get_help_text()

    # Normalization: handle command aliases
    subcmd = tokens[0].lower()
    if subcmd == "note":
        tokens[0] = "notes"
    elif subcmd in ("help", "?", "start"):
        return _get_help_text()

    # If db_path was provided and not present in tokens, inject it
    if db_path and "--db-path" not in tokens:
        tokens.extend(["--db-path", db_path])

    parser = _build_parser()
    try:
        args = parser.parse_args(tokens)
    except ValueError as e:
        # Check if known subcommand or completely unknown
        known_subcommands = {"tasks", "today", "finance", "rekap", "notes", "reminders", "briefing", "review", "status"}
        if subcmd not in known_subcommands:
            return f"❓ Perintah tidak dikenali: '{tokens[0]}'.\n\n{_get_help_text()}"
        return f"❌ Parameter tidak valid: {e}"

    # Determine effective db_path
    effective_db = getattr(args, "db_path", None) or getattr(args, "global_db_path", None) or db_path

    handlers = {
        "tasks": _handle_tasks,
        "today": _handle_today,
        "finance": _handle_finance,
        "rekap": _handle_rekap,
        "notes": _handle_notes,
        "reminders": _handle_reminders,
        "briefing": _handle_briefing,
        "review": _handle_review,
        "status": _handle_status,
    }

    handler = handlers.get(args.subcommand)
    if not handler:
        return f"❓ Perintah tidak dikenali: '{tokens[0]}'.\n\n{_get_help_text()}"

    return handler(args, effective_db)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI Entrypoint for ASEP-AI.

    Usage:
        python -m src.agent.cli <subcommand> [flags]
    """
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        print(_get_help_text())
        return 0

    cmd_string = shlex.join(argv)
    result = dispatch_command(cmd_string)
    try:
        print(result)
    except UnicodeEncodeError:
        try:
            sys.stdout.buffer.write(result.encode("utf-8") + b"\n")
        except Exception:
            print(result.encode("ascii", errors="replace").decode("ascii"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
