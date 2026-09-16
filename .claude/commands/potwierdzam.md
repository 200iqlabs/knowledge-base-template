---
description: Record that you, the person, confirmed the contents of a file — the only way a human confirmation is ever written
---

File to confirm (repo-relative path, required): $ARGUMENTS

## What this is

The **only** way a `human:` entry reaches a file's `verified` field. The proof that a
confirmation happened is this invocation — it belongs to the person, it lands in the
transcript, and it spares the agent from deciding how far something said in conversation
reached. Background and the three trust levels: the core `CLAUDE.md`, *Trust*.

## Steps

1. Run the script with the **Bash tool, not PowerShell** (PowerShell mangles UTF-8 and
   Polish text in the file comes back unreadable). Both paths belong to the consuming
   repository, so **pass them explicitly**, the same split that governs the linter:

   ```bash
   python <core>/tools/verified/confirm.py "<path>" \
       --config <repo>/tools/context-lint/config.yaml \
       --schema <repo>/tools/tasks/schema.yaml
   ```

   `<core>` is `.` when this core *is* the repository, and `template` when it is vendored
   into a working repository.

   Scope comes from `verified_scope` in the linter's config, so this command and check
   #23 can never disagree about which files carry settled facts. Identity comes from the
   schema's `default_owner`, so the id matches the task registry's and nobody retypes it.
   Add `--owner <id>` only when somebody other than the default owner is confirming; an
   id outside the schema's `owners` is refused rather than invented.

2. **If the script refuses, relay the refusal and stop.** A refusal means the file is out
   of scope — material that is put down (`archive/`, `communication/`, `output/`,
   `inbox/`) records what happened and is not confirmed, and `tasks/` carries its own
   header contract. Do not look for a way around it: the scope is a decision written in
   the config, not an obstacle.

3. Report what was written — the file, the actor, the timestamp, and how many entries the
   file now carries. Then **run the linter over that file** so a malformed header is
   caught here rather than at session close:

   ```bash
   python <core>/tools/context-lint/lint.py "<path>" --config <repo>/tools/context-lint/config.yaml
   ```

4. The change is left **uncommitted**, like every other edit this session makes. It is
   committed with the rest of the scope's work.

## What an agent must never do with this command

- **Never invoke it on its own initiative.** Not for a file that looks important, not
  because the work that produced the file was just finished, not as a tidy-up at session
  close.
- **Never invoke it on the strength of something said in conversation.** "Yes", "agreed",
  "that's right", even "confirmed" are not invocations of this command. If the user says
  a file is right, carry on and mention that this command exists.
- **Never write, edit, or repair a `verified` entry by hand**, in any file, for any
  reason — including fixing one the linter reports as malformed in a `human:` entry.
  Report it and let the person decide.

The ground is worth keeping in view: a field the agent can award itself measures exactly
what its absence measures, while looking like evidence — and nothing downstream can tell
the difference afterwards, because an entry written by an agent is byte-identical to one
written after the person confirmed. The linter checks shape, never truth. This rule is
the whole defence.

## What this command does not do

- **It does not decide what needs confirming.** The agent never proposes a list of files
  to confirm; picking the file is the person's judgement and is the act being recorded.
- **It does not remove or amend entries.** The field is append-only — a confirmation is
  something that happened on a date, and a record of it is not improved by editing.
- **It does not mean the contents are true.** It records that somebody looked.
