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
| 18 | `loopback-task-url` | WARN | A task referred to by a local view's address instead of its identifier — a link matching `http://<loopback>[:port]/<id_prefix>-<number>` in a `.md` file. Deliberately narrow: a note documenting how to start a local tool is supposed to carry a loopback URL, and only an address that **resolves an identifier** is flagged. It is the worst kind of dead reference, because on the machine that wrote it the link works and nothing signals a problem, while elsewhere the port is held by something else, by nothing, or by a view serving a different checkout — which answers with a different task under the same number. Neither host nor port is pinned: that would be a second copy of a value living in the tool. |
| 19 | `dead-link` | **ERROR** | A Markdown link whose target does not exist, anywhere in the repository's tracked `.md` files outside sealed material (`archive/`, `communication/`, `output/`, `inbox/` — links there are part of the record) and outside build output (files `.gitattributes` marks `linguist-generated`, whose links are checked in the source they are built from). The commonest cause is a move, archiving above all: the finding then carries the repair, because `tools/tasks/relink.py --apply` can prove it (the task is in `_archive/` next to where the link looks; the file holding the link moved one directory down). Every other dead link carries a lead — a target one `../` away, the only file with that name, or no such file at all — and waits for a decision: which file an author meant is judgement, see the `relink` skill. The definition of a dead link, and of which ones are repairable, lives in `relink.py` and this check imports it, so the tool and the check cannot disagree. An ERROR: a dead link is an instruction to open something that is not there. It was a WARN for as long as the repository carried a backlog of them — an ERROR announced over an existing debt makes the linter permanently red and hands `/close-session` scopes the session never touched, which is the one property this linter exists to have. The backlog was paid first, and only then did the level change. Closed history nobody reads by default (`tasks/_archive/`, `status_archive.md`) is not checked at all: a link in material put down describes the day it was put down, so repairing it would falsify the record — what is still reported there, as a WARN, is only the subset relink repoints by itself. Capped at five findings per file, the remainder summarised in one line. |
| 20 | `orphan` | WARN | An `.md` file in the working tree that no other file links to — the working tree and not the index, because staging must not change what a check reports about content that did not change. A file created and not yet `git add`ed is therefore a node like any other, and can be reported here; it can never be reported as a dead link, which is counted against exactly the set `relink.py` sees, so that the two tools cannot return different numbers for one question. The exemption rule is **not implemented here**: the check builds the graph with `tools/context-graph/graph.py` and reports exactly what its `orphans` command reports, because two answers to "how many orphans do we have" is the failure this whole arrangement exists to prevent. A file is exempt when it sits in a subtree holding its own `self_index_marker` file, with the search upwards stopping **at the entity root** — a scope-level index lists entities, not their internals, so letting it exempt anything would exempt everything (measured: 100% of candidates). Directories already in `catalog_exclude_dirs` are exempt too, being referenced as a folder rather than file by file. A WARN, not an ERROR, because the damage is different: a dead link misdirects, an orphan is merely unreachable — often legitimately, and often only until the index that will name it is written. Configured under `link_graph` in `config.yaml`; `orphan_findings_listed` caps how many are listed one by one before the rest become a single count. Skipped silently when `link_graph` is absent — that is a repository saying it does not run this check. When it is configured and the machinery then fails (no `graph.py`, an unreadable graph config, a build that returns nothing), the finding is an **ERROR** rather than a WARN: what the check reports is mild, but a check that did not run and exits 0 reports health nobody measured. |

| 21 | `catalog-row-length` | WARN | A single entry in `catalog.md` longer than `catalog_row_max_chars` — an entry says what a file is and why to open it, and this one grew into a summary of the file. The counterpart of #17 for the other file read on every entry into an entity, and deliberately the **same threshold value**: the two mean the same thing, and two thresholds with one meaning and two values are two thresholds somebody eventually confuses. Both entry shapes count, because real catalogs are not uniform — a bullet (`` - `file` — what it is ``) and a table row (`| question | file |`); ignoring the table form would under-count by about a third. A **WARN**, unlike #22: shortening is editorial, the entry is sometimes the only description a file has, and it cannot be repaired without a human deciding what to keep — so while the repository still carries a backlog of them, an ERROR would make the linter permanently red and hand `/close-session` scopes the session never touched. The level rises after the debt is paid, the road #19 took; here that means the entities on `catalog_row_length_exclude` being cleared. That list holds entities skipped whatever their entries look like, each **with the reason it is exempt** — a required field, because an exception without a reason is indistinguishable from an oversight and nobody can tell later whether it still holds. Empty in the template: an entity name is a fact about a repository, never about the template it grew from. One finding per entry, capped at five per file with the remainder summarised in one line. |
| 22 | `index-log` | **ERROR** | An `_index.md` carrying a section with the history of its own changes (`## Recent Changes`, `## Historia zmian`, `## Changelog`, …) — it belongs in `_changelog.md` beside the index. The index is read on every entry into the scope; its own log answers a question almost nobody asks at that moment, so left inside it grows without bound and is paid for on every read. Measured before this check existed: `context/projects/_index.md` was 83 kB, of which 78 kB was its own log — with the rule against it already written in that file's header. A sentence in a header is not a guard. Matched against the **whole heading**, never as a substring: an index may legitimately catalogue a `change-orders/` directory or a `decision-log.md` file, and a substring match would call those a changelog. An **ERROR** from its first day, unlike #19 and #21: the repair is mechanical, lossless and has one known destination, and the entire backlog (five files) was paid in the same change that added the check — so the level never stood over an existing debt. Runs once over every directory the config declares (scan roots, file scopes, the task registry), because a nested `_index.md` deep inside `data/` is as much an index as the scope root's. One finding per file: the fix is the whole section, not a line of it. |

## Scopes

Two shapes, because a knowledge base usually has both:

- **`scan_roots`** — entities are folders. Checks #1–#13 and #16–#18 apply.
  Configured in `config.yaml`; the template ships with `context/projects`.
  An empty scan root is not an error.
- **`file_scopes`** — entities are single `.md` files, no folder. **Only check #2**
  applies, in its file variant. Empty by default.
- **`task_registry`** — `context/tasks/`, which is not an entity: no `status.md`, no
  `catalog.md`, no row in any `_index.md`. Checks #10, #11 and #12 run over the company-level
  task files, #14 and #15 over the whole repository, #18 over the registry
  directory as well. Runs once
  per lint, not per entity.
- **#19** and **#20** run once over the whole repository (or under `PATH`), not per entity —
  a link crosses entities, scopes and the registry alike. They read two different file sets,
  and that difference is deliberate: **#19** asks about every **tracked** `.md`, because a
  dead link it reports is one the repair tool can be pointed at, and that tool scans the
  index; **#20** asks about the **working tree**, because staging must not change what a
  check says about content that did not change — so a file created and not yet `git add`ed
  is a node like any other and can be reported as an orphan, while a dead link inside it
  cannot.

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
  `status_archive.md`), `status_row_max_chars` (how long any one of those rows may get)
  and `catalog_row_max_chars` (the same bound for an entry in `catalog.md`, check #21 —
  deliberately the same value as `status_row_max_chars`). All are read with a fallback,
  so a config predating check #16, #17 or #21 still runs instead of crashing on a
  missing key. The shipped values are in `config.yaml` with the reasoning that set
  them — this list does not repeat the numbers.
- `catalog_row_length_exclude` — entities skipped by check #21, mapped to the reason
  each is exempt. Empty in the template; a reason is required, never optional.
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
The repairs check #19 names are written by `tools/tasks/relink.py` — a separate tool,
run deliberately; the linter only imports its definition of a dead link.
Repairs are the agent's job in `/close-session`, where a human is in the loop.
