---
description: Show the task report for right now — overdue, due today, in play with no due date, external blockers, quiet entities
---

Task report from the registry. Argument (optional — an owner id from `tools/tasks/schema.yaml`, or `all`; defaults to the schema's `default_owner`): $ARGUMENTS

## Steps

1. Run the generator with the **Bash tool, not PowerShell** (PowerShell mangles UTF-8 and
   the status icons come back unreadable). The generator ships with this core; the schema
   belongs to the repository that consumes it, so **pass it explicitly**:

   ```bash
   python <core>/tools/tasks/regen_today.py --schema <repo>/tools/tasks/schema.yaml
   ```

   `<core>` is `.` when this core *is* the repository, and `template` when the core is
   vendored into a working repository — the same split that governs the linter. Add
   `--owner "<argument>"` when an argument was given.

   **Without `--schema` the generator falls back to the schema sitting next to the
   script** — the core's own example entity — whose owner ids and task directories are
   not the consuming repo's. A real owner id is then rejected outright
   (`--owner 'X' is not one of [...]`), and an id that happens to collide with an example
   one yields an empty report instead of an error. Do not omit the flag.

   The script is **read-only towards the repo** — it prints the report and writes
   nothing. The `--write` flag additionally saves `context/tasks/_today.md` (gitignored,
   for diffing two runs), but you do not normally need it.

2. **Before showing the report**, check that the index is current:
   `python <core>/tools/tasks/regen.py --schema <repo>/tools/tasks/schema.yaml`. A task
   created in this session but never regenerated will not appear in the report, and the
   user would be shown something untrue.

3. Show the report in full. Do not summarize sections and do not reorder them — the
   sections are ordered by urgency, and shortening them destroys the one thing the
   report contributes.

4. After the report, add **one sentence** of direction: what to pick up first and why
   (a due date, a hard external deadline from `status.md`, or a blocker standing in
   somebody else's way). Do not lay out a plan for the day — the user did not ask for one.

## What is filtered

Sections 1–3 (overdue, due today, in play) show the named person's tasks plus every
shared task. Sections 4–5 (external blockers, quiet entities) are **not filtered** — a
blocker sitting with a colleague is exactly what you want to see, and an entity that has
gone quiet belongs to nobody in particular.

## What this command does not do

- **It does not send the report to anyone.** The report is for whoever ran it; everyone
  else generates their own with `/today <owner>`. There is no delivery channel, because
  none is needed.
- **It does not change task state.** That is what `/tasks` is for.
- **It does not commit.** The report is ephemeral and reads the working directory, not
  `origin`.
