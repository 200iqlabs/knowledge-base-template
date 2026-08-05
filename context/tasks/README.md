# Task registry — conventions

> **Last updated:** 2026-08-05
> **Review cycle:** whenever the conventions change

This directory is the root of the task registry: it holds organisation-wide tasks that
belong to no entity, the sprint files, and one generated file (`_index.md`).

**`context/tasks/` is not an entity.** It has no `status.md`, no `catalog.md`, and no
row in any scope's `_index.md`.

The task model — where directories may live, the full set of header fields, the closed
list of `status` values, and the "the entity follows from the path" rule — is described
in [`CLAUDE.md`](../../CLAUDE.md) → **Task Registry**. What follows here is templates,
the sprint format, and the rituals; the field descriptions are not repeated.

## What is in this directory

```
context/tasks/
  README.md                # this file
  _index.md                # AUTO — every live task from every entity
  _today.md                # the report, when written with --write (gitignored)
  sprint-2026-W32.md       # the active sprint (one for the whole repo)
  sprint-2026-W31.md       # closed sprints stay where they are
  <slug>.md                # an organisation-wide task with no entity
  _archive/                # organisation-wide tasks closed in past sprints
```

## Task template

```markdown
---
title: "Inventory the export — what the outgoing file carries"
owner: alice
status: todo
priority: high
created: 2026-08-04
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
number.

**Always quote `title`.** Without quotes, a title starting with `[` is read by YAML as a
list, and a colon inside one (`Production probe: phrases…`) breaks the field mapping.
Both failures are silent enough to cost you an afternoon.

## Sprint template

```markdown
---
sprint: 2026-W32
from: 2026-08-04
to: 2026-08-10
status: active
---
# Sprint 2026-W32

- [Inventory the export](../projects/EXAMPLE_PROJECT/tasks/inventory-the-export.md)
- [Draft the onboarding note](../projects/EXAMPLE_PROJECT/tasks/draft-onboarding-note.md)
```

Header: `sprint` (ISO week identifier), `from`, `to` (ISO dates), `status`
(`active` | `closed`). Body: **only a list of links**, with relative paths to task files.

Three things a sprint file does not contain:

| What is absent | Where it lives |
|---|---|
| state, owner, priority, due date | the task file |
| a `sprint` field on the task side | only this file |
| the backlog | defined negatively — see below |

**One active sprint for the whole repo.** No per-project or per-client sprints — a small
team has one pool of attention per week. A sprint links tasks from any entities at once.

**The backlog has no file and no field.** A task is in the backlog when no link from the
active sprint points at it. Adding to the sprint = adding a line here; removing from the
sprint = deleting a line. The task file is not touched either way.

## Groups

A package of tasks cutting across entities (an epic, a rollout, a compliance package) is
a value in the group field of the task headers — e.g. `group: compliance-o1`. The group
view is **generated** into `_index.md`; there are no hand-maintained list files besides
sprints.

The reason: a hand-maintained list of links rots. A field cannot fall out of step with
anything, because there is nothing for it to fall out of step with.

## Sprint-closing ritual

Once a week, before planning the next one. The order matters — archive before writing
history, because the `🟢` rows are written from the `done` files.

1. **Archive** — `git mv` the files with `status: done` into `<entity>/tasks/_archive/`
   (organisation-wide tasks → `context/tasks/_archive/`). This step is **on demand**: if
   you skip it one week, the `done` tasks simply stay in `tasks/` and wait — the index
   keeps them in a separate "closed, awaiting archiving" section, so nothing is lost.
2. **History** — for each archived task, add a `🟢` row to the `status.md` of its
   entity, dated from the `closed` field.
3. **Close** — set `status: closed` in the sprint file being closed.
4. **New sprint** — create `sprint-<YYYY>-W<WW>.md` with `status: active` for the coming
   week.
5. **Carry-overs** — unfinished tasks either get a link in the new sprint or are
   deliberately left out and return to the backlog. Leaving one out does not change the
   task file.
6. **Regenerate and commit** — the pre-commit hook recomputes `_index.md` and the `AUTO`
   sections.

A task with `status: done` **stays where it is until the sprint is closed** — which makes
"what did we close this week" a question about one directory rather than about git.

## `_archive/` — kept forever, out of default reach

Archived tasks are never pruned. It is plain text, it weighs nothing, and "what did we
close in April" is a real question — a time cutoff would take away the answer in
exchange for nothing.

There is exactly one price for keeping everything, and it is paid by a reading rule:
**`_archive/` is in the "do NOT read" class**, alongside `archive/`, `output/` and
`communication/`. The generator skips it, the linter does not check it, the index does
not list it, and an agent working on an entity does not look there on its own initiative.
Otherwise, after a year, entering any project would mean reading a hundred closed matters
to reach the five open ones.

Access is on demand only — a question like "what did we close in April", "what were we
working on in sprint W29", "have we done anything with export metadata yet". Then the
agent reads `_archive/` and answers.

## Generated files

| File | Generator | Trigger |
|---|---|---|
| `_index.md` | `tools/tasks/regen.py` | pre-commit hook on changes to `**/tasks/*.md`, plus manually |
| the `AUTO` section of an entity's `status.md` | `tools/tasks/regen.py` | same |
| the `/today` report | `tools/tasks/regen_today.py` | on demand, printed into the session |

The report is not a repo file — it prints and vanishes. `--write` saves it to `_today.md`
(gitignored) only when you want to diff two runs.

Content between the `<!-- AUTO:START -->` and `<!-- AUTO:END -->` markers is never edited
by hand. Content outside the markers can be edited freely — the generator does not touch
it.
