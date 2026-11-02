# ASEP-AI MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a robust, lightweight, local-first Python foundation for ASEP-AI (SQLite WAL database, deterministic productivity & finance domain engines, and Hermes skill CLI tool wrappers) optimized for a 1 GB VPS and zero-token deterministic paths.

**Architecture:** A decoupled Python package where SQLite is the single source of truth with strict transaction serialization and WAL mode. Business logic for tasks, finances, notes, reminders, and reports is isolated in `src/domain/`, tested via standard `pytest`/`unittest`, and exposed to Hermes Agent through low-overhead CLI commands and native skill definitions.

**Tech Stack:** Python 3.10+, SQLite3 (standard library), pytest / unittest, zero heavy external framework dependencies.

**Spec:** `docs/superpowers/specs/2026-11-02-asep-ai-mvp-design.md`

## Global Constraints
- Target platform: Ubuntu Server LTS with 1 GB RAM / 20 GB Disk.
- Database: SQLite with WAL mode enabled, foreign keys ON, write serialization, and schema versioning.
- Timezone: `Asia/Jakarta` (WIB) for all user-facing schedules, relative dates, and reporting. Stored timestamps in UTC ISO-8601.
- Currency: Indonesian Rupiah (`IDR`) represented strictly as integer values (no floating point calculations).
- Memory & Performance: Zero unnecessary daemons; deterministic operations (`/today`, `/rekap`, reminders) must bypass LLM invocation.

## Review Focus
- **Integer overflow / float corruption in finance**: User logs transaction with commas or dots (e.g. `Rp 25.000`), ensure it parses to clean integer `25000` without string or float conversion bugs.
- **Reminder duplicate delivery on restart**: Process restarts while a reminder is due; ensure `idempotency_key` prevents duplicate message dispatches.
- **Timezone skew in due dates**: User says "besok jam 09.00", verify correct calculation in `Asia/Jakarta` even when server hardware runs UTC.
- **SQL Injection in Notes search**: User searches notes containing special characters (`'`, `"`, `*`, `%`); ensure strict parameterization.
- **Graceful DB lock handling on 1GB VPS**: Concurrent read/write attempts handled via SQLite busy timeout without throwing database locked errors.

---

### Task 1: Project Setup, Settings, and SQLite Connection with Migrations Engine

**Files:**
- Create: `config/settings.py`
- Create: `src/database/connection.py`
- Create: `src/database/migrations.py`
- Create: `src/database/migrations/001_initial_schema.sql`
- Test: `tests/test_database.py`

**Interfaces:**
- Produces: `get_db_connection(db_path: str = None) -> sqlite3.Connection`
- Produces: `run_migrations(db_path: str = None) -> int` (returns number of newly applied migrations)

- [ ] **Step 1: Write the failing test for connection factory and migration runner**
  Test in `tests/test_database.py` verifying that `get_db_connection` sets `foreign_keys=ON`, `journal_mode=WAL`, and `run_migrations` creates all tables and updates `schema_migrations`.

- [ ] **Step 2: Run test to verify it fails**
  Run: `python -m unittest tests/test_database.py`
  Expected: FAIL with `ModuleNotFoundError` or missing functions.

- [ ] **Step 3: Implement settings, SQLite connection factory, migration runner, and initial SQL schema**
  Implement `config/settings.py`, `src/database/connection.py`, `src/database/migrations.py`, and `001_initial_schema.sql`.

- [ ] **Step 4: Run test to verify it passes**
  Run: `python -m unittest tests/test_database.py`
  Expected: PASS

- [ ] **Step 5: Commit**
  Run:
  ```bash
  git add config/ src/database/ tests/test_database.py
  git commit -m "feat(db): implement SQLite connection factory and versioned migrations engine"
  ```

---

### Task 2: Task & Schedule Manager Domain

**Files:**
- Create: `src/domain/tasks.py`
- Test: `tests/test_tasks.py`

**Interfaces:**
- Consumes: `get_db_connection` from `src.database.connection`
- Produces:
  - `create_task(title: str, description: str = None, due_at: str = None, priority: str = "normal") -> int`
  - `list_tasks(status: str = "open", due_date: str = None) -> list[dict]`
  - `update_task(task_id: int, **kwargs) -> bool`
  - `mark_task_status(task_id: int, status: str) -> bool`

- [ ] **Step 1: Write the failing test for Task Manager operations**
  In `tests/test_tasks.py`, test creation, filtering by date/status, and updating task statuses.

- [ ] **Step 2: Run test to verify it fails**
  Run: `python -m unittest tests/test_tasks.py`
  Expected: FAIL with missing module.

- [ ] **Step 3: Implement Task Manager domain logic in `src/domain/tasks.py`**
  Implement CRUD with parameterized queries and status validation (`open`, `done`, `cancelled`, `archived`).

- [ ] **Step 4: Run test to verify it passes**
  Run: `python -m unittest tests/test_tasks.py`
  Expected: PASS

- [ ] **Step 5: Commit**
  Run:
  ```bash
  git add src/domain/tasks.py tests/test_tasks.py
  git commit -m "feat(tasks): implement Task & Schedule Manager domain logic"
  ```

---

### Task 3: Finance Tracker Domain

**Files:**
- Create: `src/domain/finance.py`
- Test: `tests/test_finance.py`

**Interfaces:**
- Consumes: `get_db_connection` from `src.database.connection`
- Produces:
  - `add_transaction(type: str, amount_idr: int, description: str, category: str = None, occurred_at: str = None, payment_method: str = None) -> int`
  - `get_financial_summary(start_date: str = None, end_date: str = None) -> dict` (returns total_income, total_expense, net_cash_flow, breakdown_by_category)
  - `void_transaction(transaction_id: int, reason: str = None) -> bool`

- [ ] **Step 1: Write the failing test for Finance Tracker**
  In `tests/test_finance.py`, test integer IDR math, positive/negative validation, category aggregation, and void auditing.

- [ ] **Step 2: Run test to verify it fails**
  Run: `python -m unittest tests/test_finance.py`
  Expected: FAIL with missing module.

- [ ] **Step 3: Implement Finance Tracker in `src/domain/finance.py`**
  Implement integer accounting logic, validation (type must be 'income' or 'expense', amount > 0), and deterministic SQL aggregation.

- [ ] **Step 4: Run test to verify it passes**
  Run: `python -m unittest tests/test_finance.py`
  Expected: PASS

- [ ] **Step 5: Commit**
  Run:
  ```bash
  git add src/domain/finance.py tests/test_finance.py
  git commit -m "feat(finance): implement Finance Tracker domain with integer IDR arithmetic"
  ```

---

### Task 4: Quick Notes & Tag Search Domain

**Files:**
- Create: `src/domain/notes.py`
- Test: `tests/test_notes.py`

**Interfaces:**
- Consumes: `get_db_connection` from `src.database.connection`
- Produces:
  - `save_note(content: str, title: str = None, category: str = None, tags: list[str] = None) -> int`
  - `search_notes(keyword: str = None, tag: str = None, category: str = None) -> list[dict]`
  - `archive_note(note_id: int) -> bool`

- [ ] **Step 1: Write the failing test for Quick Notes**
  In `tests/test_notes.py`, test saving notes with JSON tags, searching by keyword and tag, and archiving.

- [ ] **Step 2: Run test to verify it fails**
  Run: `python -m unittest tests/test_notes.py`
  Expected: FAIL with missing module.

- [ ] **Step 3: Implement Quick Notes domain in `src/domain/notes.py`**
  Implement note storage with JSON tags serialization, parameterized search queries, and prompt-injection safety annotations.

- [ ] **Step 4: Run test to verify it passes**
  Run: `python -m unittest tests/test_notes.py`
  Expected: PASS

- [ ] **Step 5: Commit**
  Run:
  ```bash
  git add src/domain/notes.py tests/test_notes.py
  git commit -m "feat(notes): implement Quick Notes domain with parameterized tag & text search"
  ```

---

### Task 5: Deterministic Reminder Engine

**Files:**
- Create: `src/domain/reminders.py`
- Test: `tests/test_reminders.py`

**Interfaces:**
- Consumes: `get_db_connection` from `src.database.connection`
- Produces:
  - `create_reminder(entity_type: str, entity_id: int, remind_at: str, idempotency_key: str = None) -> int`
  - `get_due_reminders(now_iso: str = None) -> list[dict]`
  - `mark_reminder_sent(reminder_id: int) -> bool`
  - `mark_reminder_failed(reminder_id: int) -> bool`

- [ ] **Step 1: Write the failing test for Reminders**
  In `tests/test_reminders.py`, test creation, due date checking, state machine transitions (`pending` -> `sent` / `failed`), and idempotency uniqueness.

- [ ] **Step 2: Run test to verify it fails**
  Run: `python -m unittest tests/test_reminders.py`
  Expected: FAIL with missing module.

- [ ] **Step 3: Implement Reminder engine in `src/domain/reminders.py`**
  Implement reminder scheduling with ISO-8601 timestamps, idempotency validation, and retry counter tracking.

- [ ] **Step 4: Run test to verify it passes**
  Run: `python -m unittest tests/test_reminders.py`
  Expected: PASS

- [ ] **Step 5: Commit**
  Run:
  ```bash
  git add src/domain/reminders.py tests/test_reminders.py
  git commit -m "feat(reminders): implement deterministic reminder engine with idempotency keys"
  ```

---

### Task 6: Daily Automation Aggregations (Morning Briefing & Evening Review)

**Files:**
- Create: `src/domain/reports.py`
- Test: `tests/test_reports.py`

**Interfaces:**
- Consumes: domain logic from `tasks`, `finance`, `notes`, `reminders`
- Produces:
  - `generate_morning_briefing(date_str: str = None) -> dict` (returns agenda, pending tasks, reminders, yesterday's spend)
  - `generate_evening_review(date_str: str = None) -> dict` (returns completed tasks, uncompleted tasks, today's spend/income, tomorrow's agenda)
  - `format_report_text(report_data: dict, report_type: str) -> str` (produces deterministic clean WhatsApp-ready message)

- [ ] **Step 1: Write the failing test for Daily Reports**
  In `tests/test_reports.py`, test generation of morning briefing and evening review dictionaries and their deterministic formatting without any LLM calls.

- [ ] **Step 2: Run test to verify it fails**
  Run: `python -m unittest tests/test_reports.py`
  Expected: FAIL with missing module.

- [ ] **Step 3: Implement Daily Reports in `src/domain/reports.py`**
  Implement data aggregation across domains and fallback deterministic template formatting in Indonesian.

- [ ] **Step 4: Run test to verify it passes**
  Run: `python -m unittest tests/test_reports.py`
  Expected: PASS

- [ ] **Step 5: Commit**
  Run:
  ```bash
  git add src/domain/reports.py tests/test_reports.py
  git commit -m "feat(reports): implement deterministic morning briefing and evening review generator"
  ```

---

### Task 7: System Manager, Health Check, and Audit Logger

**Files:**
- Create: `src/system/health.py`
- Test: `tests/test_health.py`

**Interfaces:**
- Consumes: `get_db_connection` from `src.database.connection`
- Produces:
  - `get_system_health() -> dict` (disk usage, memory estimation, database status, pending reminders count)
  - `log_audit_action(actor: str, action: str, entity_type: str, entity_id: str, result: str) -> int`

- [ ] **Step 1: Write the failing test for Health & Audit**
  In `tests/test_health.py`, verify disk/memory metrics collection and secret-redacted audit logging.

- [ ] **Step 2: Run test to verify it fails**
  Run: `python -m unittest tests/test_health.py`
  Expected: FAIL with missing module.

- [ ] **Step 3: Implement Health and Audit logging in `src/system/health.py`**
  Implement lightweight OS statistics using standard Python `shutil` / `os`, database status check, and audit logger with parameter sanitization.

- [ ] **Step 4: Run test to verify it passes**
  Run: `python -m unittest tests/test_health.py`
  Expected: PASS

- [ ] **Step 5: Commit**
  Run:
  ```bash
  git add src/system/health.py tests/test_health.py
  git commit -m "feat(system): implement lightweight system health metrics and audit logger"
  ```

---

### Task 8: Hermes Native Skill Manifest & Unified CLI Dispatcher

**Files:**
- Create: `src/agent/cli.py`
- Create: `skills/asep-ai/SKILL.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: All domain functions (`tasks`, `finance`, `notes`, `reminders`, `reports`, `health`)
- Produces: Command Line Interface `python -m src.agent.cli <command> [args]` and Hermes skill package in `skills/asep-ai/`

- [ ] **Step 1: Write the failing test for CLI commands**
  In `tests/test_cli.py`, test CLI invocation for `/today`, `/tasks`, `/rekap`, `/status`, and `/note`.

- [ ] **Step 2: Run test to verify it fails**
  Run: `python -m unittest tests/test_cli.py`
  Expected: FAIL with missing module.

- [ ] **Step 3: Implement CLI router and Hermes skill manifest**
  Create `src/agent/cli.py` with standard `argparse` and `skills/asep-ai/SKILL.md` detailing tool instructions for Hermes Agent.

- [ ] **Step 4: Run test to verify it passes**
  Run: `python -m unittest tests/test_cli.py`
  Expected: PASS

- [ ] **Step 5: Run full test suite and verify 100% pass rate**
  Run: `python -m unittest discover -s tests -p "test_*.py"`
  Expected: All tests PASS.

- [ ] **Step 6: Commit**
  Run:
  ```bash
  git add src/agent/cli.py skills/ tests/test_cli.py
  git commit -m "feat(agent): implement CLI dispatcher and Hermes native skill package"
  ```
