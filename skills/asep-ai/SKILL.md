---
name: asep-ai
description: Personal AI assistant deterministic CLI dispatcher for task management, financial tracking, notes, reminders, briefings, and VPS system health monitoring.
---

# ASEP-AI Hermes Native Skill Manifest

The `asep-ai` skill provides deterministic, zero-token command shortcuts and execution tools for personal productivity on a 1GB RAM Ubuntu VPS.

When the owner triggers slash commands (`/today`, `/tasks`, `/rekap`, `/status`, `/notes`) or natural language queries requiring deterministic data management, use the unified CLI entrypoint:

```bash
python -m src.agent.cli <subcommand> [flags]
```

Or call Python directly:
```python
from src.agent.cli import dispatch_command

output_text = dispatch_command(cmd_string)
```

---

## Available Subcommands & Tool Invocations

### 1. `today`
Returns the daily agenda: scheduled tasks for today and pending/due reminders.
- **CLI**: `python -m src.agent.cli today`
- **Slash Shortcut**: `/today`
- **Output**: Formatted WhatsApp message with today's tasks and pending reminders.

### 2. `tasks`
Manages tasks and schedules.
- **List tasks**:
  `python -m src.agent.cli tasks [--status open|done|all] [--due YYYY-MM-DD]`
- **Create task**:
  `python -m src.agent.cli tasks --create "<title>" [--due <ISO/YYYY-MM-DD>] [--priority low|normal|high|urgent] [--desc "<notes>"]`
- **Mark task done**:
  `python -m src.agent.cli tasks --done <task_id>`
- **Slash Shortcuts**:
  - `/tasks` -> lists open tasks
  - `/tasks --status done` -> lists completed tasks
  - `/tasks --create "Perpanjang STNK" --due 2026-11-15 --priority high`
  - `/tasks --done 3`

### 3. `finance`
Records transactions (income / expense) and views financial summaries.
- **Add transaction**:
  `python -m src.agent.cli finance --add --type <income|expense> --amount <amount_idr> [--desc "<desc>"] [--category "<cat>"] [--payment "<method>"]`
- **View rekap**:
  `python -m src.agent.cli finance --rekap [--start YYYY-MM-DD] [--end YYYY-MM-DD]`
- **Slash Shortcuts**:
  - `/finance --add --type expense --amount 35000 --desc "Makan Siang" --category Konsumsi`
  - `/finance --rekap --start 2026-11-01 --end 2026-11-30`

### 4. `rekap`
Shortcut to view financial recap for the current month or a specified date range.
- **CLI**: `python -m src.agent.cli rekap [--start YYYY-MM-DD] [--end YYYY-MM-DD]`
- **Slash Shortcut**:
  - `/rekap` (defaults to current month)
  - `/rekap --start 2026-11-01 --end 2026-11-07`

### 5. `notes`
Saves, searches, and organizes quick notes and ideas.
- **Save note**:
  `python -m src.agent.cli notes --save "<content>" [--title "<title>"] [--category "<cat>"] [--tag "<tag1,tag2>"]`
- **Search notes**:
  `python -m src.agent.cli notes --search "<keyword>"`
- **List notes by tag or recent**:
  `python -m src.agent.cli notes [--tag "<tag>"]`
- **Slash Shortcuts**:
  - `/notes --save "Setup cron backup db di VPS" --title "VPS Backup" --tag devops`
  - `/notes --search "backup"`
  - `/notes --tag devops`
  - `/notes`

### 6. `reminders`
Schedules and inspects deterministic reminders.
- **Create reminder**:
  `python -m src.agent.cli reminders --create --entity-type <task|note> --entity-id <id> --remind-at <ISO-8601>`
- **Check due reminders**:
  `python -m src.agent.cli reminders --check-due`
- **Slash Shortcuts**:
  - `/reminders --check-due`
  - `/reminders --create --entity-type task --entity-id 12 --remind-at 2026-11-03T09:00:00`

### 7. `briefing`
Generates deterministic Morning Briefing (07:00 WIB agenda, overdue alerts, yesterday's recap).
- **CLI**: `python -m src.agent.cli briefing [--date YYYY-MM-DD]`
- **Slash Shortcut**: `/briefing` or `/briefing --date 2026-11-02`

### 8. `review`
Generates deterministic Evening Review (21:00 WIB completed vs pending tasks, daily expenses, tomorrow's agenda).
- **CLI**: `python -m src.agent.cli review [--date YYYY-MM-DD]`
- **Slash Shortcut**: `/review` or `/review --date 2026-11-02`

### 9. `status`
Inspects VPS resources (RAM, CPU load, Disk) and SQLite database health.
- **CLI**: `python -m src.agent.cli status`
- **Slash Shortcut**: `/status`

---

## Parameter Guidelines & Constraints

1. **Amounts**: Currency amounts are strictly positive integer Indonesian Rupiah (`IDR`). Parsing supports raw digits (`50000`) or standard punctuation (`50.000` / `Rp 50.000`).
2. **Dates & Times**: Standard format is `YYYY-MM-DD` for dates and `YYYY-MM-DDTHH:MM:SS` for timestamps. Stored in ISO-8601 UTC, localized to `Asia/Jakarta` (WIB).
3. **Priorities**: Allowed values are `low`, `normal`, `high`, `urgent`. Default is `normal`.
4. **Statuses**:
   - Tasks: `open`, `done`, `cancelled`, `archived`.
   - Reminders: `pending`, `processing`, `sent`, `failed`, `cancelled`.
5. **Zero-Token Path**:
   When users send slash commands (`/today`, `/rekap`, etc.), Hermes routes directly to `dispatch_command` without invoking LLM completions, conserving VPS memory and token quota.
