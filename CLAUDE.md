# Knowledge Base — Working Rules

How this knowledge base is organised and how an agent is expected to work in it.
Every rule here carries its reasoning, because a rule you cannot argue with is a rule
you cannot correctly bend.

One idea runs through all of it: **every fact has exactly one home.** A value copied
into a second file drifts the first time somebody updates one and not the other, and
nothing tells you which copy is stale. References don't drift.

## Entity Context Management

An **entity** is anything you track over time and want state for: a project, a client,
a customer, a partnership. Each entity is a folder under a scope root
(`context/projects/`, and whatever other scopes you add), laid out per that scope's
`_template.md`.

| Path | What lives there | Reading rule |
|---|---|---|
| `<ENTITY>/status.md` | state of play | read **in full, first** (see `Status Protocol`) |
| `<ENTITY>/inbox/` | drop zone for raw incoming files | processed, not read directly |
| `<ENTITY>/data/` | processed, durable knowledge | read freely |
| `<ENTITY>/deliverables/` | work made **for** the recipient, in Markdown | read when needed |
| `<ENTITY>/output/` | sealed generated artefacts (PDFs, scripts) | **do NOT read** |
| `<ENTITY>/archive/` | processed raw material | **do NOT read** unless explicitly asked |
| `<ENTITY>/communication/` | text of messages you sent. Optional | **do NOT read** (see `Communication Files`) |
| `<ENTITY>/tasks/` | one file per task. Optional | via the `AUTO` section of `status.md` (see `Task Registry`) |
| `<ENTITY>/catalog.md` | map of the entity's files | read as a map, never as state |

The "do NOT read" directories are not secrets — they are **volume control**. An entity
folder that loads everything on every question spends its context on sealed PDFs and
last quarter's raw exports instead of on what is true today. What matters from those
files is supposed to have been extracted into the files that *are* read by default.

Some scopes are file-shaped rather than folder-shaped: one Markdown file per entity, no
folder, no `status.md`. That is a legitimate shape for a lightweight scope — but such an
entity has no task directory and no state board, so its state has to live in a field
inside the file.

## Status Protocol

`status.md` in the entity folder is the **only** source of truth about the state of
work. This holds for every entity in every scope.

### Separation of roles — never three copies of state

| File | Role | What is NOT in it |
|---|---|---|
| `status.md` | state of work: what is done, what we wait on, the narrative | file descriptions, **hand-written task rows** |
| `tasks/<slug>.md` | a task: owner, due date, priority (see `Task Registry`) | the state of the entity as a whole |
| `catalog.md` | map of the entity's files | **status** — no column, no section |
| the scope's `_index.md` | one row per entity + date of last activity | any entity's state spelled out |

Same rule as "No duplicates — one source of truth" below: state copied into three places
diverges after the first change somebody forgets to carry across.

**A task is not state.** An atomic item with an owner and a due date lives in `tasks/`,
and `status.md` shows it **only** through the section generated between the
`<!-- AUTO:START -->` / `<!-- AUTO:END -->` markers. A hand-written task row is a
configuration error — the linter reports it as an ERROR.

### The legend — closed, four states, two parts of the file

`🟢 DONE` · `🟡 OPEN` (in progress, the ball is on our side) · `🔴 BLOCKED` (waiting on
an outside party) · `⚪ TODO` (queued, not started).

| Part of `status.md` | Allowed markers | Where they come from |
|---|---|---|
| hand-written | `🔴` `🟢` | written by a human or an agent |
| generated (`AUTO`) | `⚪` `🟡` | the `status` field in `tasks/` files |

`⚪` and `🟡` never appear outside the `AUTO` section — they correspond to the `todo`
and `open` values in the task registry and have no hand-written variant.

A `🟡` or `🔴` row **must name whose side the ball is on** — a specific client,
contractor, authority, or colleague. "Waiting for a reply" without a subject is useless.

**Do not guess `🟡` vs `🔴`.** When generating a `status.md` from existing prose: if the
files do not say who holds the ball, ask the user rather than picking.

### Reading

1. Read the entity's `status.md` **in full** before proposing any next steps.
2. Do **not** infer state from the prose of `catalog.md` or `_index.md`. They describe
   files, not state.
3. Run `git log --oneline -10 -- <entity path>` and reconcile it against the OPEN/TODO
   rows. A commit may report something shipped that the board never recorded —
   **flag that divergence to the user** before proposing next steps. This works only
   because of the "a commit message names the effect" rule in `Commit Convention`.

### Writing

After every delivery, sent deliverable, or other state-changing event, record the change
in **exactly one place**:

- event covered by a task in the registry → the `status` field in the task file
  (`done` plus a date in `closed`),
- waiting on an outside party, or closing a thread with no task → a row in the
  hand-written part of `status.md`.

This is part of the definition of "done": a task is not finished until the record has
been written. A `🟢` row in `status.md` for a registry task appears when the sprint is
closed, not when the `status` field changes.

`status.md` should fit on one screen — **a threshold of 80 lines, counted excluding the
`AUTO` section**. Past that, condense the closed items or move them into `data/`, so the
file never has to be truncated when read.

## Task Registry

A task is a **file**, not a row in a table. The registry lives in the repository and is
the only place a task exists.

### Where a task lives

```
context/tasks/<slug>.md                        ← organisation-wide task, no entity
context/projects/<PROJECT>/tasks/<slug>.md     ← task belonging to an entity
```

**The entity follows from the path.** The header has no field naming the entity — it
would be a second copy of what the file's location already says, and would drift on the
first `git mv`. Moving a task between entities is a `git mv`, with no edit to the
content.

File-shaped scopes have **no** `tasks/` directory — work concerning such an entity
belongs to the registry of whichever project runs it.

`tasks/_archive/` holds tasks closed in past sprints. **Do NOT read** — the same rule as
for `archive/`, `output/` and `communication/`. Keep it **indefinitely** (it is plain
text, and "what did we close in April" is a real question), but reach for it only when
the user explicitly asks about closing history. Moving `done` tasks into `_archive/` is
an **on-demand** step, not an automatic one.

The filename is a kebab-case slug of the title, with no date prefix and no number. The
slug is the identifier — references use a relative path, not a separate ID.

### Header

```yaml
---
title: "Inventory the export — what the outgoing file carries"   # always quoted
owner: alice            # values come from schema.yaml
status: todo            # todo | open | blocked | done
priority: high          # urgent | high | normal | low
created: 2026-08-04
due: 2026-08-14         # optional
blocked_by:             # required when status: blocked — who or what
group: compliance-o1    # optional — groups tasks across entities
source:                 # optional — URL, file, meeting
closed:                 # required when status: done
---
```

Required: `title`, `owner`, `status`, `priority`, `created`. The rest is optional, with
two conditions: `blocked` without `blocked_by`, and `done` without `closed`, are lint
errors.

`status` has a **closed list of four values** matching the `status.md` legend: `todo`
(⚪), `open` (🟡), `blocked` (🔴), `done` (🟢). There is no fifth state.

The title lives only in the `title` field — never repeated as an `# H1`, so the
generator reads one format. Everything after the header is free-form: context, the
definition of done, links.

The allowed values, the field names, and where task directories may live are **not
repeated in this file** — they live in `tools/tasks/schema.yaml`, which both the
generator and the linter read. Change them there.

### Sprint and backlog

One active sprint **for the whole repository**: `context/tasks/sprint-<YYYY>-W<WW>.md`.
The file holds a header and **only a list of links** to task files — it does not repeat
state, owner, or due date. A task has no `sprint` field; membership lives only in the
sprint file.

**The backlog is defined negatively:** the backlog is every live task the active sprint
does not link to. There is no separate file and no backlog field.

Thematic grouping (epics, compliance packages) goes through the group field — the group
view is generated. There are no hand-maintained list files besides sprints.

Conventions, templates, and the sprint-closing ritual:
[`context/tasks/README.md`](context/tasks/README.md).

### What is generated

| File | Contents | When it is produced |
|---|---|---|
| `context/tasks/_index.md` | every live task from every entity | pre-commit hook on changes to `**/tasks/*.md`, plus manually |
| the `AUTO` section of an entity's `status.md` | that entity's live tasks | same |

Generator: `tools/tasks/regen.py`. Content between the `AUTO` markers is never edited by
hand.

### Report — `/today`

`/today [owner|all]` prints a report into the session: overdue, due today, the rest of
the sprint, external blockers taken from `status.md`, and entities quiet for longer than
the threshold. The task sections are filtered to the named person plus every shared
task; blockers and quiet entities are not filtered.

**The report is personal and ephemeral.** It is not committed, not sent to anyone, and
reads the working directory — so a task created a minute ago is already in it. Everyone
generates their own.

## Communication Files (do NOT read)

`<ENTITY>/communication/` holds the **text of messages you sent** — chat, email, social.
The directory is optional: it does not exist until the entity has correspondence worth
keeping.

The reading rule is the same as for `archive/` and `output/`: **do NOT read unless the
user explicitly asks.** Working in the context of an entity, you do not load these files
— extraction below is what makes that safe. When the user explicitly asks about the
content or history of correspondence, you read and answer.

**Naming:** `YYYY-MM-DD-<channel>-<person>-<slug>.md`.

**Frontmatter:**

```yaml
---
channel: chat | email | social
to: <recipient>
date: YYYY-MM-DD
status: sent | draft
extracted: true | false
---
```

### Extraction is mandatory

What a message settles — decisions, dates, commitments, amounts — goes into the files
that *are* read by default: **changes of state and of who holds the ball into
`status.md`**, **durable facts into `data/`**. After extraction, set `extracted: true`.
A message that settles nothing also gets `extracted: true`, with no entries anywhere.

Without this step, `communication/` becomes an archive nobody reads, and the knowledge
in it never reaches the agent.

### The boundary: `deliverables/` vs `communication/` vs `output/`

| Directory | What goes there |
|---|---|
| `deliverables/` | things made **FOR** the recipient — proposals, reports, instructions, in Markdown |
| `communication/` | **the text of messages you sent** |
| `output/` | sealed generated artefacts — PDFs, scripts, finished outgoing files |

The same artefact never sits in two of them. Example: a proposal sent by email splits
into three files — **the proposal itself** in `deliverables/`, **the PDF** in `output/`,
**the text of the email that carried it** in `communication/`.

## Index Protocol

1. Before ANY work on an entity → read the relevant scope's `_index.md`.
2. Before working with a specific entity → read, **in this order**: the scope's
   `_index.md` → the entity's **`status.md` in full** → `catalog.md` as a map of files.
   For a file-shaped entity: the file itself.
   The order is not cosmetic — `catalog.md` says which files exist, `status.md` says
   what is done. Reading the map of files instead of the state board leads to proposing
   things that already shipped.
3. NEVER scan scope folders directly — use the indexes.
4. After creating or editing files in an entity folder → update that entity's
   `catalog.md` AND the scope's `_index.md`. State changes **only** in `status.md`
   (see `Status Protocol`).
5. Respect the entity's status in its anchor file — skip inactive entries unless the
   query targets them specifically.
6. **An entity's tasks** are read from the `AUTO` section of its `status.md`, not by
   scanning `tasks/`. The cross-entity view is `context/tasks/_index.md` (see
   `Task Registry`). Both sections are generated — do not edit them by hand.

## Context Files & Data Storage Rules

### Rules

1. **No duplicates — one source of truth** — each fact, rule, threshold or number lives
   in exactly one file (its source of truth). Everywhere else — other context files
   **and skills** — references it (`see [file](path)`), so the agent reads the source to
   discover the value instead of finding it copied. Never restate or duplicate the
   value; if a skill already reads the source file in its workflow, point to it rather
   than repeating its rules. Copied values drift out of sync; references don't.
2. **Every context file must have** a `Last updated` header and a `Review cycle`.
   Update the date when you modify the file.
3. **Subdirectories for detail** — if a root file grows too large, extract sections into
   `context/<domain>/` and add a cross-reference from the root file.
4. **Outputs ≠ context** — agent-generated analyses go to `outputs/`, not `context/`.
   Only promote an output to context if the user explicitly confirms it as a lasting
   fact.
5. **External references** — for data held in an external system, store a link and a
   brief description in the relevant context file. Do not duplicate the external
   document's contents unless the user asks for a local snapshot.
6. **Naming** — subdirectory files use descriptive slugs. Outputs use
   `YYYY-MM-DD-slug.md`.

Skills load root-level context files by exact path — never rename or move them without
updating the skills that read them.

### If this core is consumed from another repository

These rules may live in a `template/` directory of a working repository that imports
them, rather than at the root of a repository of their own. When they do, one direction
is fixed: **nothing is ever copied from `context/`, `outputs/` or `openspec/` into the
template.** The only permitted flow is the other way — a rule written here, governing the
repository that consumes it.

The reason is that a template with no real data in it cannot leak real data. Relying on
review to catch a client name pasted in as an illustration works right up until the once
it doesn't, and by then the leak is in a public git history. So: when a rule needs an
example, write the example from scratch against the template's own fictional entity.

The same holds for tool configuration. Neutral defaults are part of the template;
anything naming real people, real scopes or real systems lives outside it and is passed
to the tools explicitly at invocation.

## Outputs — analyses and working documents

`outputs/` holds artefacts generated by advisory agents, in subdirectories by domain.
Naming: `YYYY-MM-DD-topic-slug.md`.

> **`outputs/` (at the root) vs `<ENTITY>/output/`** — two disjoint concepts, and both
> stay. `outputs/<domain>/` at the root holds **analyses by advisory agents**, grouped by
> domain and belonging to no entity. `<ENTITY>/output/` holds **that entity's sealed
> artefacts** (PDFs, scripts, finished outgoing files) and is subject to the "do NOT
> read" rule. Deciding where to write: a cross-cutting analysis → `outputs/<domain>/`;
> an artefact belonging to one entity → `<ENTITY>/output/`. Neither is ever renamed into
> the other.

## Session Hygiene (`/lint`, `/close-session`)

Two tools with disjoint roles. The split is deliberate: the linter is meant to be a
**trustworthy signal**, not to become one more place that quietly tidies up after
itself.

| | `/lint` | `/close-session` |
|---|---|---|
| What it does | states **facts about files** | **fixes and commits** |
| Edits files | **never** | yes, within the detected scope |
| Semantic decisions | none | yes, with a human in the loop |
| When | on demand, any time | at the end of a session |

**`/lint`** — `tools/context-lint/`, deterministic and read-only. The same repo state
gives the same result. Exit 0 with only WARNs, 1 with any ERROR. Check catalogue and
thresholds: [`tools/context-lint/README.md`](tools/context-lint/README.md).

Zero auto-fixes — on purpose. A linter that edits files stops answering the question
"is the repo in order", because a clean run then means either "it was fine" or "the tool
swept it away". Hundreds of ERRORs from a single directory are a signal about
`config.yaml`, not about the repo.

**`/close-session`** — `skills/close-session/`. Fixed order:
**scope → extraction → the three files → lint → sweep → summary → one commit per
scope**. Lint runs after the three files are updated, because it catches what the agent
missed. The sweep runs after `status.md`, because only then is it visible what
contradicts it.

Hard boundaries: it does not send messages, does not create deliverables, does not touch
files outside the detected scope (exception: shared `_index.md` files), and does not
flip `status: draft` to `sent`.

## Commit Convention

Format: `<prefix>(<scope>): <what was shipped>`.

**An entity's scope is its folder name, literally.** One canonical scope per entity, no
exceptions. The reason: `git log -- <path>` filters by path and always works, but
`git log --grep=<scope>` — the cheap way to review "what happened with this entity" —
falls apart once a name has five variants. The prefix (`feat`/`fix`/`docs`/…) stays
free; its drift does no harm.

Keep a table of canonical scopes in your own `CLAUDE.md`, with one row per entity plus
the system scopes for changes outside entities (tooling, skills, repo configuration).
**When you create a new entity, add its scope row in the same commit.** A table missing
a row for an existing folder is as much an error as a missing `catalog.md`.

### A commit message names the effect, not the files

The message says **what was shipped, sent, or settled** — it does not enumerate the
files touched. "sent the API integration proposal to the client", not "update
deliverables".

The reason is operational, not aesthetic: `git log --oneline -10 -- <entity path>` is an
**independent trace of status**, reconciled against `status.md` (see `Status Protocol`).
That trace works only if the commit message lets you tell whether the event was recorded
on the board. "update files" tells you nothing.

## Data Freshness

Every context file has a "Last updated" header. If the data is older than the freshness
threshold in `tools/context-lint/config.yaml`, say so before giving advice based on it.

## Communication Style

- Tone: direct, concrete, professional but friendly
- Code: always in English
- Format: short answers by default, expand on request
- Diagrams: Mermaid for processes and architecture
- Tables for comparisons
