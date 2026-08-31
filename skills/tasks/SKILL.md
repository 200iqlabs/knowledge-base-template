---
name: tasks
description: Manage the task registry in the repo — create a task in the right entity's tasks/ directory, review "what do I have today" filtered by owner and window, change state, pick a task up by dating it, review the pool of tasks nobody has picked up, prioritise, hand a task to a session working in another repository. Use when the user asks about their tasks, wants to write something down to do, takes something on, plans what to work on next, or sends work outside.
---

# /tasks — the task registry

The task model (where files live, the fields, the `status` values, the "entity follows
from the path" rule, the `start`/`due` window) is described in `CLAUDE.md` →
**Task Registry**. Conventions and rituals:
[`context/tasks/README.md`](../../context/tasks/README.md). The machine contract:
[`tools/tasks/schema.yaml`](../../tools/tasks/schema.yaml).
**Do not repeat those rules here — read them from there.**

The mode follows from what the user asks for. Argument: `$ARGUMENTS`.

| Request | Mode |
|---|---|
| "write this down", "add a to-do" | **New task** |
| "what do I have today", "what are my tasks", "what is Alice on" | **Review** |
| "done", "blocked on X" | **State change** |
| "I'm picking this up", "starting on this", "I'll take it" | **Pick up** |
| "what has fallen off the radar", "go through the pool with me" | **Pool review** |
| "sort this out for me", "where do I start" | **Prioritisation** |
| "what did we close in April", "have we done X already" | **History** |
| "hand this to the other repo", "run this in the product repo" | **Hand a task outside** |

## New task

1. **Establish the entity** — which project or client, or an organisation-wide task with
   no entity. Do not guess: if the conversation does not settle it, ask. A file-shaped
   entity owns no tasks — work concerning one goes into the `tasks/` of the project that
   runs it.
2. **Collect the required fields.** Fill the gaps like this:
   - `owner` — **ask** if it does not follow from context. An owner is not decoration.
   - `priority` — propose a value and name the reason; the user confirms.
   - `due` — only when a deadline actually exists. An empty due date beats an invented one.
   - `start` — leave it empty. A new task is not automatically a task being worked on;
     dating it is a separate decision the user makes when they take it on.
   - `created` — today's date.
   - `id` — **ask the generator**: `python tools/tasks/regen.py --next-id`. Never invent
     one and never count rows in the index; the generator knows about `_archive/`, where
     numbers stay spoken for forever.
3. **Write the file** `<entity>/tasks/<slug>.md`. Slug from the title, kebab-case, no
   date and no id. Check that the file does not already exist — a duplicated task is
   worse than a missing one.
4. **Run the generator**: `python tools/tasks/regen.py`.
5. **Commit** with the entity's scope (an organisation-wide task → the registry scope).
   The commit message names the task, not the file.

## Review

Read **only** [`context/tasks/_index.md`](../../context/tasks/_index.md) — the generated
section. Do not scan `tasks/` directories: the index exists so that a review costs one
read.

Apply filters to the table rows: owner, due date (`overdue` / `today` / `this week`),
whether the task has been picked up (the Start column), group (the Groups section).

When the index is empty and the user expects tasks, check whether the generator has run
since the last change (`python tools/tasks/regen.py`) before answering "you have
nothing".

## State change

You change **only the `status` field** in the task file, plus:
- `blocked` → fill `blocked_by` with a concrete party (person, company, authority),
- `done` → fill `closed` with today's date.

What you do **not** do on a state change: you do not add a row to `status.md` — the
generated section is recomputed by the generator, and the `🟢` row is written at session
close, in the archiving step. You also do not touch `start`: whether a task is current is
a separate question from what state it is in.

Then: generator, then commit.

## Pick up

Write **today's date** into `start`, and nothing else.

Do not add a `due` at the same time unless the user names a real deadline. A task with
`start` and no `due` is current until it is closed, which is exactly what "I am on this"
means; a deadline invented to fill the field devalues every deadline that was meant.

Dropping a task is the reverse: clear `start`. Do not change `status` — a task put down
is not a task that failed, and it stays live in the registry.

Then: generator, then commit with the entity's scope.

## Pool review

The tasks with no `start` and no `due` — the ones the daily report deliberately leaves
out. A routine writes `context/tasks/_review-YYYY-MM.md` once a month with proposals; the
user may also ask for this on the spot.

1. Read `_index.md` and take the live tasks with neither date.
2. Group them **by entity** and go entity by entity. Twenty batches of decisions beat one
   list of two hundred, and the user can stop after any batch without losing work.
3. For each task propose exactly one of three: a start date, a deadline, or closing it as
   no longer relevant. Say which and why in one line.
4. Skip tasks with `status: blocked` — something waiting on an outside party is not
   something to take up. Say how many you skipped and why.
5. Write only what the user approves, batch by batch. Run the generator after each batch,
   so an interrupted review leaves the registry consistent.
6. When the review came from `_review-YYYY-MM.md`, delete that file in the same commit
   that carries its proposals into the task files.

## Prioritisation

Input: the live pool from `_index.md` **plus** the `🔴` rows from the `status.md` of the
entities that appear in that pool. A task can be formally `todo` while actually blocked
by an outside party recorded in `status.md` — without that second source the ordering
comes out false.

Order by: hard external deadline → `due` → `priority` → blocked status (blocked items
sink, because the work will not move anyway). **Say what not to do this week** — a list
with no rejections is not prioritisation.

The result is a proposal. You change files only after the user accepts it.

## History

Closed tasks live in `<entity>/tasks/_archive/` and `context/tasks/_archive/`, kept
indefinitely. That directory is in the **"do NOT read"** class — you read it **only**
when the user explicitly asks about closings: what we did in a given period, whether
something has been done before, what a particular stretch of work looked like.

Filter on the `closed` field in the headers, not on file dates. When answering, give the
entity and the closing date — without those, a list of titles says nothing about context.

Do not look there during ordinary work on an entity: after a year that would mean
reading a hundred closed matters to reach five open ones.

## Hand a task outside

When a task from this registry is going to be carried out by a session working in a
different repository, the outgoing content — a prompt, a briefing file, a message — has
to carry everything that session needs to report back. It will not have this repository's
rules loaded, and it must not have to go looking for them.

1. **Verify the id is unique.** `python tools/tasks/regen.py --check` reports a collision
   with both paths. Do it *before* the id leaves: once it is out, correcting it means
   chasing it through content you no longer control.
2. **Establish the absolute path** to this repository's root — the reader is in a
   different working directory, so a relative path resolves to the wrong place or to
   nothing.
3. **Paste the contract block** into the outgoing content, filled in:

   ```markdown
   ## Task update contract

   Task: <ID> — <Title>
   Registry: <absolute path to this repo's root>

   When you are done (or blocked), update the task in that repository:

   1. Open `context/tasks/_index.md` there and find <ID> in the `ID → path` table.
      Open the file it points at. If <ID> is not in the table, change nothing and say so
      in your result — do not create a file and do not add the id anywhere.
   2. Change only these header fields:
      - `status:` → `done` (then fill `closed:` with today's date, YYYY-MM-DD)
                  → `blocked` (then fill `blocked_by:` with who or what blocks it)
      Leave `id`, `title`, `owner`, `priority`, `created`, `due` and the group field
      exactly as they are.
   3. Append a report at the end of the file, under a `## Reports from outside` heading
      — that exact heading, so a second report has somewhere to go. Create it if it is
      not there; never overwrite an existing entry:

      ### <YYYY-MM-DD> · <repository name>
      <what was done, in two or three sentences>
      Artefact: <link to the PR, commit, or change on your side>

   4. Do NOT run that repository's generators or linters. Its boards are rebuilt by a
      session working there, which knows how to invoke them. Your job ends at the task
      file.

   5. Do NOT run `git add`, `git commit` or `git push` in that repository. Leave the
      change in the working directory — somebody working there will commit it with the
      rest of that entity's scope.
   ```

   Step 4 is not tidiness. Those generators take a contract as an argument and ship a
   neutral default beside themselves; invoked without it, they judge every real task
   invalid. A session that reaches for one because it seems helpful is reaching for a
   tool it has no way to invoke correctly.

4. **Check the direction of any access rule you are about to quote.** A repository this
   one may only read is still allowed to write back here; that rule constrains outgoing
   writes, not incoming ones. Quoting it symmetrically would shut the channel.

## Boundaries

- You do not create tasks "while you are at it" during other work without the user's
  agreement. A registry stuffed with items nobody ordered stops being read.
- You do not flip `status: done` on the user's behalf because you assume something got
  finished. A closing is confirmed by a human, or by a commit that names it.
- You do not edit content between the `AUTO` markers — not in `status.md`, not in
  `_index.md`.
- You do not create a `tasks/` directory for a file-shaped entity.
