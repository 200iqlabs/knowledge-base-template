# Entity template — projects

Copy this folder shape when you start a new project. Three files are required by the
linter (`project.md`, `status.md`, `catalog.md`); the directories are created only when
you actually have something to put in them.

```
context/projects/<PROJECT>/
  project.md         # the anchor: what this is, who is involved, current phase
  status.md          # state of play — the only source of truth about state
  status_archive.md  # closed rows aged out of status.md — do NOT read unless asked
  catalog.md         # map of this entity's files
  data/              # processed, durable knowledge
  deliverables/      # work made FOR the recipient, in Markdown
  inbox/             # drop zone for raw incoming files
  output/            # sealed generated artefacts — do NOT read
  archive/           # processed raw material — do NOT read unless asked
  communication/     # text of messages you sent — do NOT read
  tasks/             # one file per task
```

## `project.md`

```markdown
# <PROJECT NAME>

> **Last updated:** YYYY-MM-DD
> **Review cycle:** monthly

| | |
|---|---|
| **Status** | Active / On Hold / Completed |
| **Started** | YYYY-MM-DD |
| **Owner** | <owner id from tools/tasks/schema.yaml> |

## What this is

One paragraph. What the project is for, and how you will know it worked.

## Who is involved

## Current phase
```

## `status.md`

```markdown
# <PROJECT NAME> — status

> **Last updated:** YYYY-MM-DD

| Stan | Item | Waiting on |
|:---:|---|---|
| 🟢 | What has been delivered | — |
| 🔴 | What we are waiting for | **Name the party** |

<!-- AUTO:START -->
<!-- AUTO:END -->
```

The `AUTO` region is written by `tools/tasks/regen.py` from the files in `tasks/`.
Never edit between the markers. Hand-written rows use only 🟢 and 🔴 — ⚪ and 🟡 belong
to the generated section. See `CLAUDE.md` → `Status Protocol`.

## `catalog.md`

```markdown
# <PROJECT NAME> — file catalog

> **Last updated:** YYYY-MM-DD

## data/
| File | What is in it |
|---|---|

## deliverables/
| File | What is in it |
|---|---|
```

A map of files, with **no status column**. Status lives in `status.md`, and only there.
