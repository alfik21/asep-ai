# ASEP-AI: Personal AI Assistant - MVP Technical Design

- **Date**: 2026-11-02
- **Topic**: Architecture, Data Foundation & Domain Tools Implementation (Local-First MVP)
- **Status**: Draft for Review

---

## 1. Intent and The Why

The goal of **ASEP-AI** is to provide an always-available personal assistant on WhatsApp that manages tasks, finance, reminders, and notes for a single owner without requiring manual spreadsheet management or heavy infrastructure.

Running on a constrained 1 GB RAM / 20 GB VPS necessitates a lean, resilient architecture. Routine queries, scheduling, and accounting must execute deterministically without invoking costly LLM API calls, preserving memory, latency, and token budgets.

---

## 2. Goals, Non-Goals, and Anti-Goals

### Goals
- Single-user productivity assistant accessible via WhatsApp (Hermes Agent + Baileys gateway).
- Core modules:
  - **Task & Schedule Manager**: Creation, status updates, due dates, reminders.
  - **Finance Tracker**: Strict integer IDR income/expense logging, categorization, deterministic SQL aggregation.
  - **Quick Notes**: Full-text / tag-based searchable notes.
  - **Deterministic Reminder Engine**: Timezone-aware (`Asia/Jakarta`), idempotent delivery without LLM calls on trigger.
  - **Morning Briefing & Evening Review**: Daily summaries pulled directly from SQLite with deterministic fallback templates.
  - **System Manager & Audit Trail**: Health checks, disk/RAM monitoring, and redacted activity audit logs.
  - **Self-Development Staging Pipeline**: Structured workflow (proposal -> isolated staging -> sandbox validation -> owner approval -> activation/rollback) for user-requested custom features.

### Non-Goals (MVP)
- No local LLM hosting on the VPS (relies on 9Router OpenAI-compatible endpoint).
- No direct banking API / automated financial transactions.
- No multi-user support or public open access.
- No full web dashboard or complex GUI.
- No unrestricted root execution by AI.

### Anti-Goals
- Never let the LLM be the source of truth for financial balances, task statuses, or reminder schedules.
- Never write or execute untrusted generated code directly into production without approval and sandbox verification.
- Never crash the system or cause OOM due to uncontrolled concurrency, parallel subagents, or memory bloat.

---

## 3. Constraints
- **Resource Limits**: VPS Ubuntu with 1 GB RAM and 20 GB Storage. Python processes must be lightweight and memory-monitored.
- **Database**: SQLite with Write-Ahead Logging (`WAL`), strict serialization of write transactions, foreign key constraints, and automated schema migrations.
- **Timezone**: All user-facing schedules, relative dates ("besok", "jam 9"), and briefings adhere strictly to `Asia/Jakarta` (WIB, UTC+7), while stored timestamps remain in UTC (ISO-8601).
- **Communication Security**: Strict allowlist filtering for the owner's WhatsApp number. Rejection and zero data leakage for unauthorized numbers.

---

## 4. Approaches Considered

1. **Approach 1: Local-First Architecture & Testing (Selected)**
   - *Description*: Construct the project architecture, database schemas, deterministic domain modules, and comprehensive automated test suite in the local workspace first before deploying to VPS.
   - *Pros*: Fast development cycle, zero risk to live server, 100% test verification of domain rules and SQL constraints.
   - *Cons*: Live WhatsApp message delivery is verified at the VPS deployment stage.

2. **Approach 2: VPS-Direct Setup**
   - *Description*: Connect to VPS directly from Day 1, install Hermes and 9Router, pair WhatsApp QR, and write code on the server.
   - *Pros*: Immediate live messaging feedback.
   - *Cons*: Prone to connection drops, higher chance of OOM issues during iterative development on a 1 GB VPS, harder debugging.

*Decision*: Approach 1 was selected to establish solid data models, tools, and unit tests prior to deployment.

---

## 5. Architectural Components & Module Specifications

### 5.1 Project Layout (Standard Python Package)
```text
asep-ai/
├── config/
│   └── settings.py              # Environment variables, timezone, allowlist config
├── src/
│   ├── __init__.py
│   ├── database/
│   │   ├── __init__.py
│   │   ├── connection.py        # SQLite connection manager with WAL and foreign keys
│   │   ├── migrations.py        # Schema migration runner
│   │   └── migrations/          # Versioned SQL migration scripts (001_initial.sql, etc.)
│   ├── domain/
│   │   ├── tasks.py             # Task CRUD & query logic
│   │   ├── finance.py           # Income/expense transactions, IDR integer math, summaries
│   │   ├── notes.py             # Quick notes CRUD, tags, FTS search
│   │   ├── reminders.py         # Reminder scheduling, state machine, idempotency keys
│   │   └── reports.py           # Morning briefing & evening review aggregations
│   ├── system/
│   │   ├── health.py            # Disk, RAM, process status, audit logging
│   │   └── self_dev.py          # Feature proposal, staging validator, rollback mechanism
│   └── agent/
│       ├── tools.py             # Hermes/OpenAI tool definitions and wrappers
│       └── dispatcher.py        # Deterministic command router (e.g. /tasks, /rekap)
├── tests/
│   ├── test_database.py
│   ├── test_tasks.py
│   ├── test_finance.py
│   ├── test_notes.py
│   ├── test_reminders.py
│   ├── test_reports.py
│   └── test_self_dev.py
├── scripts/
│   └── migrate.py               # CLI tool to run migrations & integrity checks
└── PRD_ASEP-AI.pdf
```

### 5.2 Database Schema (SQLite)
* `schema_migrations`: `(version TEXT PRIMARY KEY, applied_at TEXT, checksum TEXT)`
* `categories`: `(id INTEGER PRIMARY KEY, domain TEXT, name TEXT, is_active INTEGER)`
* `tasks`: `(id INTEGER PRIMARY KEY, title TEXT, description TEXT, due_at TEXT, priority TEXT, status TEXT, created_at TEXT, updated_at TEXT)`
* `transactions`: `(id INTEGER PRIMARY KEY, type TEXT, amount_idr INTEGER, category_id INTEGER, description TEXT, occurred_at TEXT, payment_method TEXT, status TEXT, created_at TEXT)`
* `notes`: `(id INTEGER PRIMARY KEY, title TEXT, content TEXT, category_id INTEGER, tags_json TEXT, created_at TEXT, updated_at TEXT, archived_at TEXT)`
* `reminders`: `(id INTEGER PRIMARY KEY, entity_type TEXT, entity_id INTEGER, remind_at TEXT, status TEXT, idempotency_key TEXT UNIQUE, sent_at TEXT, retry_count INTEGER)`
* `daily_reviews`: `(id INTEGER PRIMARY KEY, review_date TEXT, summary_json TEXT, generated_at TEXT, delivery_status TEXT)`
* `installed_features`: `(id INTEGER PRIMARY KEY, name TEXT UNIQUE, version TEXT, status TEXT, manifest_json TEXT, installed_at TEXT)`
* `feature_requests`: `(id INTEGER PRIMARY KEY, request_text TEXT, proposal_json TEXT, status TEXT, created_at TEXT, approved_at TEXT)`
* `audit_logs`: `(id INTEGER PRIMARY KEY, actor TEXT, action TEXT, entity_type TEXT, entity_id TEXT, timestamp TEXT, result TEXT)`

---

## 6. Implementation Strategy & Next Steps
1. **Phase 1 (Data Foundation)**:
   - Setup migration engine, initial schema `001_initial_schema.sql`, connection factory with SQLite WAL mode.
   - Implement unit tests verifying table constraints, foreign keys, and indexes.
2. **Phase 2 (Productivity & Finance Domains)**:
   - Implement pure domain logic with comprehensive tests (Tasks, Notes, Finance, Reminders).
   - Ensure finance calculates IDR as whole integers (no float rounding errors).
3. **Phase 3 (Hermes Tool Interfaces & Deterministic Dispatcher)**:
   - Expose domain functions as lightweight callable tools.
   - Implement deterministic command dispatcher to handle standard shortcuts (`/today`, `/rekap`, etc.) bypassing the LLM.
4. **Phase 4 (Self-Development Pipeline Foundation)**:
   - Build staging directory validator and safety checks (no path traversal, parameterized SQL checks).
