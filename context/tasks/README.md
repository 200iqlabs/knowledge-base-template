# Task registry — conventions

> **Last updated:** 2026-08-31
> **Review cycle:** whenever the conventions change

This directory is the root of the task registry: it holds organisation-wide tasks that
belong to no entity, plus the generated files. There are no hand-maintained list files
here — nothing to keep in step with the tasks themselves.

**`context/tasks/` is not an entity.** It has no `status.md`, no `catalog.md`, and no
row in any scope's `_index.md`.

The task model — where directories may live, the full set of header fields, the closed
list of `status` values, and the "the entity follows from the path" rule — is described
in [`CLAUDE.md`](../../CLAUDE.md) → **Task Registry**. What follows here is templates
and rituals; the field descriptions are not repeated.

## What is in this directory

```
context/tasks/
  README.md                # this file
  _index.md                # AUTO — every live task from every entity
  _today.md                # the report, when written with --write (gitignored)
  _review-2026-08.md       # monthly pool review, until its proposals are taken up
  <slug>.md                # an organisation-wide task with no entity
  _archive/                # organisation-wide tasks, closed and archived
```

## Task template

```markdown
---
id: REPO-1
title: "Inventory the export — what the outgoing file carries"
owner: alice
status: todo
priority: high
created: 2026-08-04
start:
due: 2026-08-14
blocked_by:
group:
source:
closed:
---

## Context

Why we are doing this, where it came from.

## Definition of done

How we will know the task is closed.

## Notes
```

Leave empty fields empty or delete them — the generator treats both the same way. The
filename is a kebab-case slug of the title: `inventory-the-export.md`. No date, no
number — **the id does not go into the filename**. Putting it there would create a second
copy of the identifier that a `git mv` could contradict, and the whole point of the field
is that nothing about the file's location can contradict it.

**Get the id from the generator**, never by counting rows in the index:

```bash
python tools/tasks/regen.py --next-id      # → REPO-143
```

It answers with the highest number ever handed out plus one, `_archive/` included.
Archiving does not return a number to the pool.

**Always quote `title`.** Without quotes, a title starting with `[` is read by YAML as a
list, and a colon inside one (`Production probe: phrases…`) breaks the field mapping.
Both failures are silent enough to cost you an afternoon.

## Picking a task up

Write today's date into `start`. That is all of it — there is no list to add a line to,
because there is no list. The rules for reading the window (`start` / `due`) are in
[`CLAUDE.md`](../../CLAUDE.md) → **Task Registry**; what matters here is the habit:

```bash
# taking REPO-1 on today
start: 2026-08-31
```

Do **not** invent a `due` at the same time. A task with `start` and no `due` is current
until it is closed, which is what you actually mean by "I am on this". A deadline nobody
will enforce devalues the ones that are real, and the overdue section is only worth
reading while every date in it was meant.

Dropping a task is the reverse: clear `start`. The task stays live and indexed, and comes
back through the monthly pool review.

## Groups

A package of tasks cutting across entities (an epic, a rollout, a compliance package) is
a value in the group field of the task headers — e.g. `group: compliance-o1`. The group
view is **generated** into `_index.md`; there are no hand-maintained list files at all.

The reason: a hand-maintained list of links rots. A field cannot fall out of step with
anything, because there is nothing for it to fall out of step with.

## Archiving happens at session close

Not as a weekly ritual anybody has to remember. `/close-session` does it, automatically
and without asking, for the entities that session touched. The order matters — archive
before writing history, because the `🟢` rows are written from the `done` files.

1. **Archive** — `git mv` the files with `status: done` into `<entity>/tasks/_archive/`
   (organisation-wide tasks → `context/tasks/_archive/`). The header is not edited: the
   id survives archiving unchanged, and its number stays spoken for.
2. **History** — for each archived task, add a `🟢` row to the `status.md` of its entity,
   dated from the `closed` field.
3. **Regenerate and commit** — the pre-commit hook recomputes `_index.md` and the `AUTO`
   sections.

**Only the session's own scope.** A task closed during a session is a changed file in its
entity, so that entity is in scope by definition — on a day-to-day basis the limit costs
nothing. It bites only on a backlog of closures from earlier sessions, and archiving those
globally would produce commits touching entities the session never opened, which would
ruin `git log -- <path>` as an independent trace of what happened.

Nothing breaks if a session ends without archiving: `done` tasks stay in `tasks/` and the
index keeps them in a separate "closed, awaiting archiving" section.

## Monthly pool review

A task with no `start` and no `due` never appears in the daily report — deliberately, so
that the report stays a plan for the day. What brings it back is a routine that runs once
a month from the local scheduler: it reads every live task without dates and writes
`_review-YYYY-MM.md` proposing, for each, a start date, a deadline, or closing it as no
longer relevant.

The routine **never edits task files**. Deciding what to take on is the one thing in this
process that cannot be derived from the data — if a routine set `start` by itself, within
three months the field would mean "an algorithm thought so" rather than "I decided", and
the in-play section would stop being a commitment.

The routine writes the file and stops — it does not commit. Several sessions work in this
repository at once and each stages only its own scope; a commit from a process blind to
the working directory would sweep up somebody else's work. The file simply appears in the
working tree, the way a task changed from another repository does, and is committed by a
session working here — then deleted in the same commit that carries its proposals into the
task files. The `_` prefix keeps it out of the generator's and the linter's scan, exactly
like `_index.md`.

Tasks with `status: blocked` are skipped: something waiting on an outside party is not
something to take up.

## `_archive/` — kept forever, out of default reach

Archived tasks are never pruned. It is plain text, it weighs nothing, and "what did we
close in April" is a real question — a time cutoff would take away the answer in
exchange for nothing.

There is exactly one price for keeping everything, and it is paid by a reading rule:
**`_archive/` is in the "do NOT read" class**, alongside `archive/`, `output/` and
`communication/`. The generator skips it, the index does not list it, and an agent
working on an entity does not look there on its own initiative. Otherwise, after a year,
entering any project would mean reading a hundred closed matters to reach the five open
ones.

One exception, and it reads exactly one field: **id uniqueness** (lint check #14, and the
generator's `--next-id`) scans `_archive/` too. Archiving does not return a number to the
pool, so a new task reusing an archived id would be a real collision — and an identifier
that has already left the repository has to keep pointing at one thing. Nothing else in
those files is read.

Access is on demand only — a question like "what did we close in April", "what were we
working on last quarter", "have we done anything with export metadata yet". Then the
agent reads `_archive/` and answers.

## Generated files

| File | Generator | Trigger |
|---|---|---|
| `_index.md` (live tasks, groups, `ID → path`) | `tools/tasks/regen.py` | pre-commit hook on changes to `**/tasks/*.md`, plus manually |
| the `AUTO` section of an entity's `status.md` | `tools/tasks/regen.py` | same |
| the `/today` report | `tools/tasks/regen_today.py` | on demand, printed into the session |

The report is not a repo file — it prints and vanishes. `--write` saves it to `_today.md`
(gitignored) only when you want to diff two runs.

Content between the `<!-- AUTO:START -->` and `<!-- AUTO:END -->` markers is never edited
by hand. Content outside the markers can be edited freely — the generator does not touch
it.
