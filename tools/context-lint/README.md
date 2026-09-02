# context-lint

Deterministic, read-only consistency checks over the `context/` knowledge base
(projects, clients, whatever scopes you keep). **Facts about files only** — semantic
judgement (is this data stale? was the decision in that message ever extracted?) belongs
to the agent, in `/close-session`. The lint never modifies files and has no auto-fix mode.

## Run

```bash
# whole repo
python tools/context-lint/lint.py

# one subtree (entity folder)
python tools/context-lint/lint.py "context/projects/EXAMPLE_PROJECT"

# machine-readable
python tools/context-lint/lint.py --json
```

Run it through **Bash, not PowerShell** — PowerShell's default codepage mangles the
non-ASCII output. The `/lint` command enforces this.

Requires **Python 3.10+** and **PyYAML** (`pip install pyyaml`). Missing PyYAML prints
a one-line install hint and exits `2` (not a traceback).

## Output

One finding per line, tab-separated — readable and parsable:

```
LEVEL   CHECK   PATH    MESSAGE
```

`LEVEL` is `ERROR` or `WARN`. A trailing summary (`N findings: X ERROR, Y WARN`) goes
to stderr. Findings are sorted ERROR-first, then by check and path.

**Exit code:** `0` when there are no ERRORs (WARNs are fine), `1` when at least one
ERROR is present, `2` on a dependency/config failure.

## Checks

| # | Check id | Level | What it catches |
|---|----------|-------|-----------------|
| 1 | `catalog` | ERROR | Content file with no entry in `catalog.md`; catalog link pointing at a missing file; self-indexing subtree with no folder-level entry. Directories in `catalog_exclude_dirs` are skipped — `tasks/` among them: the registry indexes itself in `context/tasks/_index.md`. |
| 2 | `index` | ERROR | Entity folder with no row in `_index.md`; ghost row with no folder. **File variant** for `file_scopes`: prospect file with no row; index link pointing at a missing file. |
| 3 | `naming` | WARN | File in `communication/` or `archive/` without a `YYYY-MM-DD` prefix. |
| 4 | `freshness` | WARN | `status.md` `Last updated` marker older than the threshold (default 30 days) — the living board went quiet. Reference anchors (`project.md`, `customer.md`, `client.md`) are exempt: stable by design. |
| 5 | `inbox` | WARN | Non-empty `inbox/` — reminder to run `/ingest`. |
| 6 | `comm-place` | ERROR | Sent-message file (`*-mail-*`, `*-wa-*`, `*-linkedin-*`, …) sitting in `deliverables/`. |
| 7 | `extraction` | WARN | File in `communication/` with `extracted: false` or no frontmatter. |
| 8 | `status-size` | WARN | `status.md` longer than the threshold (default 80 lines), counted **without** the generated `AUTO` section — a long task list is not the author's to shorten. **Backstop for #16**: it reacts to bulk, #16 to the number of closed rows. |
| 9 | `structure` | ERROR | Missing required file (`status.md`, `catalog.md`, `project.md`, …) per the folder template. |
| 10 | `task-header` | ERROR | Task file with a missing required field (`id` among them), an `id` not matching `<id_prefix>-<number>`, an `owner`/`status`/`priority` outside the allowed set, `status: blocked` without `blocked_by`, `status: done` without `closed`, a field the contract forbids (`sprint`, `entity`), or a non-ISO date. |
| 11 | `task-window` | ERROR | Task whose `start` is later than its `due` — a window that closes before it opens, which nothing can ever be inside. The number is reused from the retired sprint check rather than freed: renumbering 12–17 would change the meaning of numbers `CLAUDE.md` and the config already refer to. |
| 12 | `task-overdue` | WARN | Task whose `due` has passed and whose `status` is not `done`. |
| 13 | `manual-task` | ERROR | `⚪` or `🟡` table row in `status.md` outside the `AUTO` section — a task written by hand instead of created in `tasks/`. The legend naming both icons in prose is not flagged; only table rows are. |
| 14 | `task-id` | ERROR | Two task files carrying the same `id`. **Scans `_archive/` as well**, unlike every other check: archiving does not return a number to the pool, so a new task reusing an archived id is a real collision. An identifier that has left the repository must keep pointing at one thing. |
| 15 | `task-id` | ERROR | `id_prefix` still set to the template's own default. Skipped while the template's example entity is still on disk (`template_example_entity` in the schema) — a fresh clone must not greet its first user with an error about a value the template shipped. |
| 16 | `status-closed-rows` | WARN | More `🟢` rows in the hand-written part of `status.md` than `status_closed_rows_kept` — `/close-session` should age the surplus into `<ENTITY>/status_archive.md`. Only the first table cell counts, so the icon appearing inside a row's text is not a second closure. |
| 17 | `status-row-length` | WARN | A single `🟢` row in `status.md` longer than `status_row_max_chars` — a closed row is a headline plus a link into `data/`, and this one grew into a paragraph. Complements #16: that one bounds how many closed rows a board keeps, this one how much each costs to read. Only `status.md` — a long row in `status_archive.md` costs nothing, since nothing loads the archive by default. One finding per row, capped at five per file with the remainder summarised in one line. |

## Scopes

Two shapes, because a knowledge base usually has both:

- **`scan_roots`** — entities are folders. Checks #1–#13, #16 and #17 apply.
  Configured in `config.yaml`; the template ships with `context/projects`.
  An empty scan root is not an error.
- **`file_scopes`** — entities are single `.md` files, no folder. **Only check #2**
  applies, in its file variant. Empty by default.
- **`task_registry`** — `context/tasks/`, which is not an entity: no `status.md`, no
  `catalog.md`, no row in any `_index.md`. Checks #10, #11 and #12 run over the company-level
  task files, #14 and #15 over the whole repository. Runs once
  per lint, not per entity.

A file-shaped entity has no `status.md` and no `catalog.md` by design — its state lives
in a field inside the file — so the folder-shaped checks would only produce noise. What
does carry value is index correspondence: when such a scope is maintained mechanically
(by a sync from an external system, say), drift between the index table and the directory
means the sync broke, not that somebody forgot a row. The check works off the markdown
link in each row's first cell, so an index row reads `[Name](slug.md)`.

## Self-indexing subtrees

A directory containing `_index.md` (`self_index_marker`) catalogues itself. Check #1
stops at its boundary and instead requires the entity's `catalog.md` to name the folder.

This was derived from a real run, not guessed. One research directory produced 2 907 of
2 936 findings, because most of its sub-directories already kept their own `_index.md`.
Listing the exceptions by name would have covered that day's directories and none of the
next month's. Naming the rule covers both. After the change: 55 findings, all genuine.

## Configuration

All paths, thresholds, patterns, and exceptions live in `config.yaml`:

- `scan_roots` — folder-shaped scopes, each with required files and index name.
- `file_scopes` — file-shaped scopes (check #2 only).
- `thresholds` — `freshness_days`, `status_max_lines`, `status_closed_rows_kept` (how
  many `🟢` rows `status.md` keeps before `/close-session` moves the rest into
  `status_archive.md`) and `status_row_max_chars` (how long any one of those rows may
  get). Both are read with a fallback, so a config predating check #16 or #17 still
  runs instead of crashing on a missing key. The shipped values are in `config.yaml`
  with the reasoning that set them — this list does not repeat the numbers.
- `self_index_marker` — filename that marks a self-cataloguing subtree.
- `communication_patterns` — filename markers for check #6.
- `catalog_exclude_dirs`, `date_prefix_dirs`, `structural_files`, `freshness_files`.
- `name_exceptions` — basenames/globs exempt from the date-prefix rule (check #3).
- `deliverables_comm_exceptions` — process docs whose name merely contains `mail`/`wa`
  but which are genuine deliverables (check #6 false positives).
- `external_checks` — checks belonging to the consuming repository, run alongside the
  core ones. Each entry gives a `label` (shown in the result), a `command` (a list of
  argv parts, or one string), an optional `path` naming the subtree it speaks about so
  a scoped `lint <path>` run stays scoped, and an optional `timeout` in seconds. The
  command prints findings in this tool's own text format — level, check, path, message,
  tab-separated — and follows the same exit convention (0 clean, 1 when its findings
  include an ERROR). Its findings are merged into the report under `<label>:<check>`
  and its ERRORs set the exit code exactly like a core check's. A command that cannot
  be started, crashes, times out, or prints something unreadable is reported once,
  against `config.yaml`, as `<label>:config` — a fault in the wiring, not in the repo —
  and the remaining checks still run. Declaring none leaves the output byte-identical.
  This exists so a check that has to know a real scope of the consuming repository can
  stay outside this published template and still run on every `/lint`.
- `task_registry.schema` — path to `tools/tasks/schema.yaml`, which owns the task
  contract (required fields, `id_prefix`, allowed `owner`/`status`/`priority` values,
  forbidden fields, directory layout). `tools/tasks/regen.py` reads the same file, so
  checks #10–#12, #14–#15 and the generator can never disagree about what a valid task is.

## Determinism

Same repo state → same findings and same exit code. The only time-varying input is
"today" (freshness check #4), so two runs on the same day are identical.

## What this tool will not do

No auto-fix. Ever. A linter that edits files stops being a trustworthy signal: you can
no longer tell whether a clean run means the repo was fine or the tool papered over it.
Repairs are the agent's job in `/close-session`, where a human is in the loop.
