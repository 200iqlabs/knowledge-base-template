---
description: Run the deterministic context-lint over the knowledge base (whole repo or a path)
---

Run the context-lint consistency checker and report findings. Argument (optional path to limit scope, e.g. `context/projects/EXAMPLE_PROJECT`): $ARGUMENTS

## Steps

1. Run the linter with the **Bash tool** (never PowerShell — it mangles the UTF-8 output and the findings come back unreadable):
   - No argument → whole repo: `python tools/context-lint/lint.py`
   - With a path argument → scoped: `python tools/context-lint/lint.py "<path>"`
   - The script is **read-only** — it never edits files. Exit code is `1` when any ERROR is present, `0` when only WARNs (or clean), `2` on a missing-dependency/config error.

2. Summarize the findings for the user, **ERROR-first**:
   - Lead with the count: `N ERROR, M WARN`.
   - Group ERRORs by check (`catalog`, `index`, `structure`, `comm-place`, `task-header`, `sprint`, `manual-task`) and list the offending paths concisely. These block a clean close.
   - Then WARNs by check (`freshness`, `inbox`, `extraction`, `naming`, `status-size`, `task-overdue`) — advisory.
   - `manual-task` in bulk means a `status.md` still holds hand-written ⚪/🟡 rows that belong in `tasks/` — report it as one migration item per entity, not as N separate findings.
   - If the exit code is `2`, relay the dependency/config message verbatim (most likely missing PyYAML → `pip install pyyaml`).
   - **Hundreds of ERRORs from one directory is a config signal, not a repo signal** — say so instead of proposing to catalogue them one by one. The fix belongs in `config.yaml` (`catalog_exclude_dirs`, `self_index_marker`).

3. Do **not** fix anything here — `/lint` only reports. Point the user to `/close-session` (or a targeted fix) if they want ERRORs cleaned up. If they explicitly ask you to fix a specific finding now, do that one thing.

## Notes
- Checks and thresholds live in `tools/context-lint/config.yaml`; the check catalogue is in `tools/context-lint/README.md`.
- Findings are the machine layer (facts about files). Semantic cleanup — stale data, decisions never extracted from messages — is the agent's job in `/close-session`. See `CLAUDE.md` → `Session Hygiene`.
