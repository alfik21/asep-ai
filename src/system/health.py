"""System Manager, Health Check, and Audit Logger for ASEP-AI.

Provides lightweight OS resource checks (RAM, Disk, CPU), database integrity
and statistics monitoring, secret-redacting audit logging, and deterministic
Indonesian health report formatting (0 LLM tokens).
"""

import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import DATABASE_PATH
from src.database.connection import get_db_connection

# Regular expression patterns for sensitive data redaction
_API_KEY_PATTERN = re.compile(
    r"\b(sk-[a-zA-Z0-9_\-]{16,}|key-[a-zA-Z0-9_\-]{16,}|AIza[0-9A-Za-z\-_]{35})\b"
)
_BEARER_PATTERN = re.compile(r"(?i)\b(Bearer\s+)[a-zA-Z0-9_\-\.]{15,}\b")
_SECRET_KV_PATTERN = re.compile(
    r"(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)\s*([:=])\s*([\"']?)([^\s\"',;]+)\3"
)
_PHONE_PATTERN = re.compile(r"(?:\+?62|0)8[1-9][0-9]{7,10}\b|\+\d{10,15}\b")


def redact_sensitive_text(text: Optional[str]) -> str:
    """Strips or masks secrets (API keys, tokens, passwords, phone numbers).

    Preserves safe and normal text while replacing identified sensitive patterns
    with redaction markers.

    Args:
        text: Input string potentially containing sensitive credentials.

    Returns:
        Sanitized string with credentials redacted.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)

    # 1. Redact Bearer tokens
    sanitized = _BEARER_PATTERN.sub(r"\g<1>[REDACTED_TOKEN]", text)

    # 2. Redact key-value secrets (e.g. password=..., secret: ...)
    sanitized = _SECRET_KV_PATTERN.sub(r"\1\2\3[REDACTED]\3", sanitized)

    # 3. Redact API keys (sk-..., AIza...)
    sanitized = _API_KEY_PATTERN.sub("[REDACTED_API_KEY]", sanitized)

    # 4. Redact phone numbers
    sanitized = _PHONE_PATTERN.sub("[REDACTED_PHONE]", sanitized)

    return sanitized


def log_audit_action(
    actor: str,
    action: str,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    result: Optional[str] = None,
    db_path: Optional[str] = None,
) -> int:
    """Records an action to the audit_logs table with sensitive info redacted.

    Args:
        actor: Identity of actor performing action (e.g. 'owner', 'system').
        action: Description or name of action performed.
        entity_type: Optional domain entity type (e.g. 'task', 'transaction').
        entity_id: Optional entity ID.
        result: Optional outcome description.
        db_path: Optional database path override.

    Returns:
        int: The inserted audit log ID.
    """
    safe_actor = redact_sensitive_text(actor.strip() if actor else "unknown")
    safe_action = redact_sensitive_text(action.strip() if action else "")
    safe_entity_type = entity_type.strip() if entity_type else None
    safe_entity_id = str(entity_id).strip() if entity_id is not None else None
    safe_result = redact_sensitive_text(result) if result is not None else None

    now_utc = datetime.now(timezone.utc).isoformat()

    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO audit_logs (actor, action, entity_type, entity_id, timestamp, result)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (
                safe_actor,
                safe_action,
                safe_entity_type,
                safe_entity_id,
                now_utc,
                safe_result,
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_recent_audit_logs(
    limit: int = 20, db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Retrieves recent audit logs in descending chronological order.

    Args:
        limit: Maximum number of log records to return (default: 20).
        db_path: Optional database path override.

    Returns:
        List of audit log dictionaries.
    """
    effective_limit = max(1, int(limit))
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, actor, action, entity_type, entity_id, timestamp, result
            FROM audit_logs
            ORDER BY id DESC
            LIMIT ?;
            """,
            (effective_limit,),
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _get_cpu_metrics() -> Dict[str, Any]:
    """Collects CPU core count and load averages if supported by host OS."""
    cores = os.cpu_count() or 1
    load_avg = None
    if hasattr(os, "getloadavg"):
        try:
            load_avg = list(os.getloadavg())
        except (OSError, AttributeError):
            load_avg = None

    return {
        "platform": sys.platform,
        "cpu_cores": cores,
        "load_avg": load_avg,
    }


def _get_memory_metrics() -> Dict[str, Any]:
    """Lightweight portable memory metrics inspection across Linux and Windows."""
    # 1. psutil if installed
    try:
        import psutil  # type: ignore

        vmem = psutil.virtual_memory()
        return {
            "total_mb": round(vmem.total / (1024 * 1024), 1),
            "used_mb": round(vmem.used / (1024 * 1024), 1),
            "free_mb": round(vmem.available / (1024 * 1024), 1),
            "percent": round(vmem.percent, 1),
        }
    except (ImportError, Exception):
        pass

    # 2. Linux /proc/meminfo (target 1GB VPS environment)
    if sys.platform.startswith("linux") and os.path.exists("/proc/meminfo"):
        try:
            meminfo: Dict[str, int] = {}
            with open("/proc/meminfo", "r", encoding="utf-8") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        val_str = parts[1].strip().split()[0]
                        meminfo[parts[0].strip()] = int(val_str)
            total_kb = meminfo.get("MemTotal", 0)
            avail_kb = meminfo.get(
                "MemAvailable",
                meminfo.get("MemFree", 0)
                + meminfo.get("Buffers", 0)
                + meminfo.get("Cached", 0),
            )
            used_kb = max(0, total_kb - avail_kb)
            percent = (
                round((used_kb / total_kb) * 100, 1) if total_kb > 0 else 0.0
            )
            return {
                "total_mb": round(total_kb / 1024.0, 1),
                "used_mb": round(used_kb / 1024.0, 1),
                "free_mb": round(avail_kb / 1024.0, 1),
                "percent": percent,
            }
        except Exception:
            pass

    # 3. Windows ctypes GlobalMemoryStatusEx fallback
    if sys.platform == "win32":
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                total_bytes = stat.ullTotalPhys
                avail_bytes = stat.ullAvailPhys
                used_bytes = max(0, total_bytes - avail_bytes)
                return {
                    "total_mb": round(total_bytes / (1024 * 1024), 1),
                    "used_mb": round(used_bytes / (1024 * 1024), 1),
                    "free_mb": round(avail_bytes / (1024 * 1024), 1),
                    "percent": float(stat.dwMemoryLoad),
                }
        except Exception:
            pass

    # 4. Graceful default fallback
    return {"total_mb": 0.0, "used_mb": 0.0, "free_mb": 0.0, "percent": 0.0}


def _get_disk_metrics(path: Optional[str] = None) -> Dict[str, Any]:
    """Inspects storage disk metrics using standard Python shutil.disk_usage."""
    check_path = Path.cwd()
    if path and path != ":memory:":
        try:
            p = Path(path).resolve()
            check_path = p.parent if p.is_file() or not p.exists() else p
        except Exception:
            check_path = Path.cwd()

    try:
        total, used, free = shutil.disk_usage(check_path)
        percent = round((used / total) * 100, 1) if total > 0 else 0.0
        return {
            "total_gb": round(total / (1024**3), 2),
            "used_gb": round(used / (1024**3), 2),
            "free_gb": round(free / (1024**3), 2),
            "percent": percent,
        }
    except Exception:
        return {"total_gb": 0.0, "used_gb": 0.0, "free_gb": 0.0, "percent": 0.0}


def _get_database_metrics(target_path: str) -> Dict[str, Any]:
    """Queries SQLite database health, table count, reminder counts, and file size."""
    conn = None
    try:
        conn = get_db_connection(target_path)
        cursor = conn.cursor()

        # Integrity check via PRAGMA quick_check
        cursor.execute("PRAGMA quick_check;")
        check_row = cursor.fetchone()
        integrity = str(check_row[0]) if check_row else "unknown"

        # Table count
        cursor.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
        )
        table_count = int(cursor.fetchone()[0])

        # Pending reminders count
        pending_reminders = 0
        cursor.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='reminders';"
        )
        if cursor.fetchone()[0] > 0:
            cursor.execute("SELECT count(*) FROM reminders WHERE status='pending';")
            pending_reminders = int(cursor.fetchone()[0])

        # Active installed features count
        active_features = 0
        cursor.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='installed_features';"
        )
        if cursor.fetchone()[0] > 0:
            cursor.execute("SELECT count(*) FROM installed_features WHERE status='active';")
            active_features = int(cursor.fetchone()[0])

        # File size calculation
        if target_path != ":memory:" and os.path.exists(target_path):
            size_bytes = os.path.getsize(target_path)
        else:
            cursor.execute("PRAGMA page_count;")
            page_count = int(cursor.fetchone()[0])
            cursor.execute("PRAGMA page_size;")
            page_size = int(cursor.fetchone()[0])
            size_bytes = page_count * page_size

        return {
            "status": "ok",
            "integrity": integrity,
            "path": target_path,
            "size_bytes": size_bytes,
            "size_kb": round(size_bytes / 1024.0, 1),
            "size_mb": round(size_bytes / (1024 * 1024), 2),
            "table_count": table_count,
            "pending_reminders": pending_reminders,
            "active_features": active_features,
        }
    except Exception as e:
        return {
            "status": "error",
            "integrity": f"error: {e}",
            "path": target_path,
            "size_bytes": 0,
            "size_kb": 0.0,
            "size_mb": 0.0,
            "table_count": 0,
            "pending_reminders": 0,
            "active_features": 0,
        }
    finally:
        if conn is not None:
            conn.close()


def get_system_health(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Collects comprehensive health and performance metrics for ASEP-AI.

    Args:
        db_path: Optional SQLite database path. Defaults to DATABASE_PATH.

    Returns:
        dict: System health metrics including OS, memory, disk, and database.
    """
    target_db_path = str(DATABASE_PATH) if db_path is None else str(db_path)

    os_metrics = _get_cpu_metrics()
    mem_metrics = _get_memory_metrics()
    disk_metrics = _get_disk_metrics(target_db_path)
    db_metrics = _get_database_metrics(target_db_path)

    # Determine overall system status
    if db_metrics.get("status") == "ok" and str(db_metrics.get("integrity")).lower() == "ok":
        if disk_metrics.get("percent", 0.0) >= 95.0 or mem_metrics.get("percent", 0.0) >= 95.0:
            status = "degraded"
        else:
            status = "healthy"
    else:
        status = "unhealthy"

    return {
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "os": os_metrics,
        "memory": mem_metrics,
        "disk": disk_metrics,
        "database": db_metrics,
    }


def format_system_health(health_data: Dict[str, Any]) -> str:
    """Formats health metrics into a deterministic WhatsApp message in Indonesian.

    Designed for 0 LLM token overhead, clean markdown bolding, and high readability.

    Args:
        health_data: Dictionary returned by get_system_health.

    Returns:
        str: WhatsApp-ready formatted message.
    """
    status = str(health_data.get("status", "unknown")).lower()
    status_label = {
        "healthy": "✅ Sehat (Normal)",
        "degraded": "⚠️ Peringatan (Beban Tinggi)",
        "unhealthy": "❌ Kritis / Ada Masalah",
    }.get(status, f"ℹ️ {status.capitalize()}")

    os_info = health_data.get("os", {})
    mem_info = health_data.get("memory", {})
    disk_info = health_data.get("disk", {})
    db_info = health_data.get("database", {})

    load_avg_str = ""
    load_avg = os_info.get("load_avg")
    if load_avg and isinstance(load_avg, (list, tuple)):
        loads = [f"{float(x):.2f}" for x in load_avg]
        load_avg_str = f" (Load: {', '.join(loads)})"

    ram_used = mem_info.get("used_mb", 0.0)
    ram_total = mem_info.get("total_mb", 0.0)
    ram_percent = mem_info.get("percent", 0.0)
    ram_str = f"{ram_used} MB / {ram_total} MB ({ram_percent}%)"

    disk_used = disk_info.get("used_gb", 0.0)
    disk_total = disk_info.get("total_gb", 0.0)
    disk_percent = disk_info.get("percent", 0.0)
    disk_free = disk_info.get("free_gb", 0.0)
    disk_str = f"{disk_used} GB / {disk_total} GB ({disk_percent}%)"

    db_size_kb = db_info.get("size_kb", 0.0)
    if db_size_kb >= 1024.0:
        db_size_str = f"{db_info.get('size_mb', 0.0)} MB"
    else:
        db_size_str = f"{db_size_kb} KB"

    integrity_val = str(db_info.get("integrity", "unknown")).upper()

    lines = [
        "📊 *Status Kesehatan Sistem ASEP-AI*",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"⚡ *Status Keseluruhan:* {status_label}",
        "",
        "🖥️ *Sistem & OS:*",
        f"• Platform: {os_info.get('platform', 'unknown')}",
        f"• CPU Cores: {os_info.get('cpu_cores', 1)}{load_avg_str}",
        f"• RAM: {ram_str}",
        "",
        "💾 *Penyimpanan (Disk):*",
        f"• Terpakai: {disk_str}",
        f"• Sisa Bebas: {disk_free} GB",
        "",
        "🗄️ *Database SQLite:*",
        f"• Integritas: {integrity_val}",
        f"• Ukuran: {db_size_str}",
        f"• Jumlah Tabel: {db_info.get('table_count', 0)}",
        f"• Pengingat Tertunda: {db_info.get('pending_reminders', 0)}",
        f"• Fitur Aktif: {db_info.get('active_features', 0)}",
    ]
    return "\n".join(lines)
