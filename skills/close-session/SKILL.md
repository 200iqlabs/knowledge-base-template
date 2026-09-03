---
name: close-session
description: "Session-closing ritual — detect the scope from git, extract decisions from
  communication/ and from the conversation into status.md/data/, put the tasks the session
  moved to the user and archive the ones it closed, update the three index files
  (status/catalog/_index), run the linter and fix ERRORs inside the scope, review
  stale data with the user's approval, summarise, and commit once per scope using the
  scope table in CLAUDE.md. Triggers on '/close-session', 'close the session', 'wrap up',
  'close this entity', 'commit per scope'. Sends no messages, creates no deliverables,
  touches no files outside the detected scope."
license: Apache-2.0
---

# /close-session — the session-closing ritual

A deterministic order of steps — **the order matters**:
**scope → extraction → tasks → the three files → lint → sweep → summary → commit.**

The tasks come **before** the three files, because the board is written from the task
files: a closure recorded after the board has been written is a closure the board does
not show. Lint runs **after** the three files are updated, because it catches what the
agent missed. The sweep runs **after** `status.md`, because only then is it visible which
files contradict it.

The skill is **read-only towards the world**: it sends no messages, creates no new
deliverables, and modifies no files outside the detected scope (one exception: shared
`_index.md` files).

## Invocation

- `/close-session` — detect the scope from git, run the full ritual.

---

## Step 1 — Detect the scope from git and confirm it

Goal: the list of touched entities is **derived**, not named by hand.

```bash
git status --porcelain          # working-tree changes (not yet committed)
git log --oneline -20           # recent commits
```

- **Scope = the entity folders** touched by (a) working-tree changes **and** (b) commits
  since the last close. Entity folders sit under the scope roots configured in
  `tools/context-lint/config.yaml`.
- **File-shaped entities are files, not folders.** They have no `status.md` and no
  `catalog.md`; their state lives in a field inside the file. If the session touched
  them, treat them as one scope and skip steps 2–6 for them apart from the lint.
- Proxy for "the last close": the most recent commit that modified any `_index.md`
  (a close always updates an index). When in doubt, show the user the `git log` range
  and ask which commit to count from.
- Map each path to its **canonical scope** per the `Commit Convention` table in
  `CLAUDE.md`. An entity's scope is its folder name, literally.
- **Put it to the user for confirmation**, e.g.:
  > "This session touched: **PROJECT_A**, **PROJECT_B** — close both?"

  Wait for confirmation before going further.
- **No changes** (clean working tree, no unclosed commits) → say there is nothing to
  close, and **stop**.

For each confirmed scope, run steps 2–6. The commits are split in step 8.

---

## Step 2 — Extract the decisions

Sources: (a) files in the scope's `communication/` with `extracted: false` or no
frontmatter, (b) decisions from the **current conversation** — commitments, dates,
amounts.

1. Find the unextracted messages:
   ```bash
   python tools/context-lint/lint.py "<scope path>" --json
   ```
   (findings under the `extraction` check) — or read `communication/` directly.
2. For each, read the content and pull out what it settles:
   - changes of state and of who holds the ball → **`status.md`**
   - durable facts (amounts, scope, decisions) → **`data/`**
3. Set `extracted: true` in the message's frontmatter.
4. A message that settles nothing → `extracted: true` straight away, with no entries
   anywhere.

The content stays in `communication/` — nothing is deleted. Full convention:
`CLAUDE.md` → `Communication Files`.

---

## Step 3 — Reconcile the tasks with what the session did

The session has just changed the world; the registry does not know it yet. This step asks
whether it should.

1. **Take the live tasks of the entities in scope** from the `AUTO` section of each
   `status.md` — it is generated, so it already *is* the list, and reading it costs one
   read instead of a scan of `tasks/`. Organisation-wide tasks: the generated section of
   the registry's own `_index.md`.
2. **Match them against what the session actually did** — the diff and the commits from
   Step 1, the decisions extracted in Step 2, and the conversation itself.
3. **Propose only the ones you have a reason to touch**, one at a time, with
   **AskUserQuestion** and the options that fit the task's current state:
   `Closed` · `Picked up (start = today)` · `Blocked on <party>` · `Leave it`.
4. **Carry out only what the user accepted.** Which field each answer writes — `status`,
   `closed`, `blocked_by`, `start` — belongs to the `tasks` skill (`State change`,
   `Pick up`); this step does not restate it, and does not invent a `due` for a task it
   has just picked up.
5. **Leave the `AUTO` sections alone.** They are recomputed from the files you just
   changed.

**This step proposes; it never decides.** A commit naming an effect is evidence that
something shipped — not that a task is closed, because a task usually outlives the commit
that moved it. That is the `tasks` skill's boundary ("you do not flip `status: done` on
the user's behalf"), applied at the moment the temptation is strongest: everything around
it in this ritual runs without asking, and the pull is to let this run too.

**Nothing to propose → skip the step silently.** Plenty of sessions move no task at all.

**No new tasks here.** Work that surfaced during the session and has no task does not get
one at close — name it in the summary (Step 8) and let the user open it deliberately. A
registry filling up with items nobody ordered stops being read, and closing time, when the
user wants to be finished, is the worst moment to ask them to triage.

The one task that does get opened at close is the **successor of a recurring one**, and it
is opened in Step 4 rather than here — because that is the step every closure passes
through, including the ones this session never proposed.

What this step leaves behind — a task marked `done` with a filled `closed` field — is
exactly what Step 4 consumes.

---

## Step 4 — Archive the closed tasks, then update the three index files

**Archive first.** The `🟢` rows are written from the `done` files, so moving them has to
happen before the board is written, not after.

For every entity in the detected scope, take the task files with `status: done` and a
filled `closed` field, and:

1. `git mv <entity>/tasks/<slug>.md <entity>/tasks/_archive/` — organisation-wide tasks
   go to the registry's own `_archive/`. The header is **not** edited: the id survives
   archiving unchanged, and its number stays spoken for.
2. Add a `🟢` row to that entity's `status.md`, dated from the `closed` field. A row is a
   headline plus a link into `data/`, not the detail itself.
3. **If the header carries `repeat:`, propose the successor** — **AskUserQuestion**, with
   `Open the next one` · `Stop the series`. Only that field triggers it; a task without it
   never raises the question. On acceptance, open the task the way the `tasks` skill
   describes (`New task`): the same title, owner and group field, the body carried over,
   `created` today, `status: todo`, no `start`, the same `repeat:`, and `due` advanced by
   one period of the cadence — a closed task with no `due` gives a successor with none,
   because a deadline nobody set is not one to invent here either. The id comes from the
   generator; the closed task's number is never carried forward.

**This step does not ask — except about a successor.** The closure itself was decided when
the task was marked `done` — in Step 3, if this session closed it — so asking again is an
empty click. Opening the next occurrence is a **different decision**, and one nobody has
made yet.

Two reasons it sits here rather than in Step 3. A task closed by a session in **another
repository** arrives already `done` and is never proposed in Step 3 — so a recurrence
handled there would be skipped in exactly the case where the work was carried out
elsewhere, which is a normal week, not an edge case. And archiving is the moment the
previous task leaves every board: after it, nothing in the registry says the duty comes
back, and an occurrence that is never opened produces no overdue row, no warning and no
trace — the absence of a task is the one state no report can show.

**Only the entities in scope.** A task closed during this session is a changed file in its
own entity, so that entity is in scope by definition and the limit costs nothing day to
day. It bites only on closures left over from earlier sessions — and archiving those would
produce commits touching entities this session never opened, which is exactly what makes
`git log -- <path>` untrustworthy as a trace of what happened. Leftovers outside the scope
stay where they are; the index keeps them in its "closed, awaiting archiving" section, and
the session that touches those entities next will archive them.

Then the three roles, **never three copies of state**:

- **`status.md`** — the only source of truth. Flip rows according to what happened in
  the session: what shipped, what was sent, where the ball went. Legend: 🟢 DONE ·
  🟡 OPEN · 🔴 BLOCKED · ⚪ TODO. A 🟡/🔴 row **must name the party** being waited on.
- **`catalog.md`** — add new and moved files. It is a map of files, **not** status. A
  directory with its own `_index.md` is entered at folder level, not file by file.
- The scope's **`_index.md`** — one row per entity plus the date of last activity. No
  state spelled out; that is what `status.md` is for.

Do not guess 🟡 vs 🔴 — when the material does not say whose side the ball is on, ask.

**Age the closed rows out.** Count the `| 🟢` rows outside the `AUTO` section, then:

1. Over `status_closed_rows_kept` (`tools/context-lint/config.yaml`) → move
   the **oldest surplus** rows, by the date column, oldest first, into
   `<ENTITY>/status_archive.md`. Create the file when missing: title
   `# <ENTITY> — Status archive`, one line saying it holds closed rows aged out of
   `status.md` and is **not read** unless somebody asks about closing history, the 🟢
   legend — then `## YYYY-MM` sections newest first, each carrying the same table header
   as the closed table in `status.md`, rows newest first inside the month.
2. Rows move **verbatim** — no rewriting, no merging, no re-dating.
3. A remaining row that is a paragraph rather than a headline — longer than
   `status_row_max_chars` in the same config, which is what lint check #17 flags →
   propose condensing it to a headline plus a link into `data/`, and act only **with the
   user's approval** — the Step 6 rule, applied to `status.md`.
4. Update `Last updated` in both files, and leave the `AUTO` section untouched.
5. Backstop: if the hand-written part is still over `status_max_lines` afterwards, say so
   and propose what else to condense. Do not condense it unasked.

---

## Step 5 — Lint and fix the ERRORs

```bash
python tools/context-lint/lint.py "<scope path>"
```

Run it with **Bash, not PowerShell** — PowerShell mangles the encoding of the output.

- **ERRORs inside the scope → fix them** as part of the close: missing `catalog.md`
  entries, messages sitting in `deliverables/`, structural gaps. **Re-run the lint until
  there are zero ERRORs in the scope.**
- **ERRORs outside the scope → report only.** That is a boundary, not laziness: fixing
  somebody else's entity in passing blurs the commit and ruins
  `git log -- <path>`.
- **WARNs → report without fixing** (freshness, a non-empty inbox, a deliberate
  `extracted: false`, `status.md` size). Do not touch the `Last updated` date just to
  silence the freshness check.
- **Hundreds of ERRORs from one directory is a config signal**, not a repo signal.
  Raise it as a `config.yaml` problem; do not propose cataloguing a thousand files.

---

## Step 6 — Sweep stale data (with the user's approval)

Review `data/` and `deliverables/` **in the scope folders only** for content that
contradicts the current `status.md`: expired proposals, plans superseded by newer ones,
assumptions that no longer hold.

- Build a list of **candidates**. Move nothing on your own.
- For each item use **AskUserQuestion** with the options:
  `Move to archive/` · `Mark superseded-by:` · `Leave it`.
- Carry out **only** what the user accepted:
  - archive → `git mv <file> <scope>/archive/YYYY-MM-DD_<name>` plus a `catalog.md` update
  - superseded → add `superseded-by: <path>` to the frontmatter
  - leave → nothing
- **Nothing disappears without approval.** No candidates → skip the step silently.

---

## Step 7 — Conditional: the scope has its own closing skill

Some scopes carry domain knowledge this skill deliberately does not have — a CRM, a
billing system, an external register. When such a scope defines its own closing skill:

- **Propose** running it and wait for the decision.
- **Do not perform its consistency checks yourself.** That skill knows the semantics of
  its external system; duplicating its checks here would push domain knowledge into a
  generic skill and produce two registers that drift apart.

Which scopes have one is recorded in `CLAUDE.md` → `Session Hygiene`. No such scope in
play → skip this step **silently**.

---

## Step 8 — Summary and one commit per scope

1. **A 3–5 sentence summary**: what the session shipped, sent, or settled, and what was
   left open. This becomes the commit message.
2. **One commit per scope**, per the table in `CLAUDE.md` → `Commit Convention`:
   ```bash
   git add <paths belonging to this scope only>
   git commit -m "<prefix>(<SCOPE>): <what was shipped/sent/settled>"
   ```
   - A multi-scope session → **separate commits per scope**, each covering only its own
     scope's files. That is what keeps `git log -- <path>` a trustworthy trace of status.
   - A shared `_index.md`: when one scope's row changed, include it in that scope's
     commit. When several scopes touched the same file and it cannot be split cleanly,
     make a separate commit with a system scope.
   - The commit message **names the effect**; it does not enumerate files. "sent the API
     integration proposal to the client", not "update deliverables".
   - Changes to `tools/`, `skills/`, `CLAUDE.md` → a system scope.
3. **Do not push and do not open a PR** unless the user asks.

---

## Hard boundaries

- Does **NOT** send messages through any channel.
- Does **NOT** create new deliverables.
- Does **NOT** modify files outside the detected scope. The only exception: shared
  `_index.md` files.
- Does **NOT** change `status: draft` to `sent` in `communication/` — a finished draft
  stays a draft until a human sends it.
- Does **NOT** archive 🔴 rows — they are live state, not closing history — and does
  **NOT** edit rows already sitting in `status_archive.md`.
- Does **NOT** archive `done` tasks belonging to entities outside the detected scope,
  even when it can see them waiting.
- Does **NOT** flip a task's `status` on its own judgement — Step 3 proposes, the user
  decides — and does **NOT** invent a `due` for a task it has just picked up.
- Does **NOT** open new tasks for work that surfaced during the session. It names them in
  the summary; opening them is the user's call. The single exception is the successor of a
  task carrying `repeat:` — that work was ordered when the recurrence was declared, not
  discovered here, and Step 4 proposes it rather than deciding.
- A problem in a folder outside the scope → report it, do not fix it.

## References

- Lint: `tools/context-lint/` (`README.md` = the check catalogue, `config.yaml` =
  thresholds and exceptions).
- Conventions: `CLAUDE.md` → `Status Protocol` (the `status_archive.md` rule and the
  closed-row threshold live there), `Communication Files`, `Commit Convention`,
  `Session Hygiene`.
- The task model, and which field each state change writes: `CLAUDE.md` → `Task Registry`
  and the `tasks` skill (`State change`, `Pick up`).
