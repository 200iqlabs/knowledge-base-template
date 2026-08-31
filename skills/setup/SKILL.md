---
name: setup
description: One-time configuration of a knowledge base created from this template — the language of generated content, the task identifier prefix, task owners, scope roots, thresholds, the pre-commit hook, and removing the example entity. Use when the user runs /setup, says the repo is freshly created from the template, or asks how to configure owners, scopes, the id prefix, or the language of the generated boards.
---

# /setup — configure a fresh knowledge base

Everything this walks through is **configuration, not code**. You edit
`tools/tasks/schema.yaml` and `tools/context-lint/config.yaml`; you never edit a `.py`
file. If something seems to require a code change, stop and say so — that is a gap in
the template, not a step for the user to work around.

Run the steps in order and **confirm each one with the user before writing.** A
knowledge base is configured once and lived in for years; a guess made here is a guess
you keep paying for.

## Step 0 — Is this actually a fresh clone?

```bash
python tools/tasks/regen.py --check
python tools/context-lint/lint.py
```

If the registry already holds real tasks, or the linter reports entities other than
`EXAMPLE_PROJECT`, **this is not a fresh clone**. Say so and ask whether the user wants
to reconfigure an existing knowledge base — that is a legitimate request, but the
example-entity step at the end no longer applies.

## Step 1 — Language of generated content

The generator writes prose **into** your files: section headings, table headers, empty
states. Those strings live in the `labels:` and `report_labels:` blocks of
`tools/tasks/schema.yaml`, in English by default.

Ask which language the boards should be in. If it is not English, rewrite the values in
both blocks — **the keys stay as they are**, only the values change. Placeholders in
braces (`{live}`, `{done}`, `{date}`, `{name}`, `{who}`, `{age}`, `{start}`,
`{days}`, `{due}`, `{stamp}`, `{path}`) must survive verbatim; dropping one turns a
generated line into a lie or a crash.

Console output is deliberately **not** configurable — it leaves no trace in the repo, so
it stays English in the code.

After the change: `python tools/tasks/regen.py` and show the user the result.

## Step 2 — Task identifier prefix

In `tools/tasks/schema.yaml` → `id_prefix`. It ships as `REPO` and **must be changed.**

Every task carries an `id` of the form `<id_prefix>-<number>`, and the prefix is the part
that says *which registry*. That matters the moment work leaves this repository: a session
opened somewhere else is handed `ACME-142` and knows where to report back. If every
knowledge base created from this template kept `REPO`, two of them in the same
conversation would produce two `REPO-7`s and no way to tell them apart — which is the one
job the identifier exists to do.

Ask for a short uppercase token, usually an abbreviation of the organisation or the
knowledge base itself: `ACME`, `KB`, `OPS`. Letters and digits, no spaces, no hyphen —
the hyphen separates prefix from number.

Existing tasks are not renumbered by this step; on a fresh clone the only ones are the
example entity's, which go away at the end anyway.

**Leaving the default is a lint error** once the example entity is removed (check #15).
That ordering is deliberate: a fresh clone is green, a knowledge base in use must have a
prefix of its own.

## Step 3 — Task owners

In `tools/tasks/schema.yaml`:

- `owners` — one id per person who owns work. Lowercase, no spaces. Keep an
  everyone-task id (`shared` by default): it is what makes a task show up in every
  person's report.
- `shared_owner` — which of those ids is the everyone-task. Change it here too if you
  rename it in `owners`; the report reads this key, so a rename in one place only would
  silently drop shared tasks from every person's report.
- `owner_labels` — how each id is displayed. Optional; an id with no entry is printed as
  written.
- `default_owner` — whose report `/today` builds with no argument. Usually whoever's
  machine this repo lives on.

Ask for the real names. Do not invent ids from the user's git config without asking —
a wrong owner id silently fails validation on every task file that uses it.

## Step 4 — Scope roots

A **scope root** is a directory holding entity folders. The template ships with one,
`context/projects`. Ask what the user actually tracks — projects, clients, customers,
partnerships — and add a root per answer.

Two files must agree, and they are the same list seen from two angles:

- `tools/tasks/schema.yaml` → `entity_roots` — where a `tasks/` directory may live, and
  which entities the report checks for silence.
- `tools/context-lint/config.yaml` → `scan_roots` — each with its `required_files` (the
  anchor file name differs per scope: `project.md`, `client.md`, …) and its `index`.

Create the directory and an `_index.md` for each new root, and copy `_template.md` into
it, adjusted to that scope's anchor file.

**If they disagree, the tools disagree**: the linter will check an entity whose tasks
the generator refuses to index, or the other way round. Re-run both after editing.

## Step 5 — Thresholds

In `tools/context-lint/config.yaml` → `thresholds`:

- `freshness_days` (default 30) — how long before a `status.md` counts as stale.
- `status_max_lines` (default 80) — how long a `status.md` may get before it stops
  fitting on one screen.

The defaults are reasonable. Only change them if the user has a reason; say that rather
than prompting for a number they have no basis to pick.

Also worth a look while you are here: `catalog_exclude_dirs`. Any directory that fills
up mechanically — generated runs, bulk exports — belongs there, or the linter will ask
the user to catalogue a thousand files one by one.

## Step 6 — Install the pre-commit hook

```bash
bash tools/hooks/install.sh
```

This is what keeps the generated sections honest: edit a task file, and the boards that
summarise it are correct in the same commit. Without it, every `_index.md` is stale
until somebody remembers to run the generator.

Verify it took: the script prints `linked:` or `copied:` for `pre-commit`.

## Step 7 — Remove the example entity

Do this **last**, and only after the user confirms — up to this point the example is
what makes the tools return something visible.

```bash
git rm -r context/projects/EXAMPLE_PROJECT
python tools/tasks/regen.py
```

Then remove its row from `context/projects/_index.md`, and delete the
`template_example_entity` key from `tools/tasks/schema.yaml` — it exists only to say
"this knowledge base has not been set up yet", and that stops being true here.

Re-run the linter afterwards. It should report `0 findings` with **0 entities scanned**
— and that count is the point: it tells the user the knowledge base is empty rather than
misconfigured. As soon as they add a real entity, the number moves.

## Step 8 — Hand over

Tell the user, in a few lines:

- which scopes now exist and where to put the first entity,
- that `/lint` reports and never fixes, while `/close-session` fixes and commits,
- that `CLAUDE.md` is theirs to extend — the conventions are a starting point, and the
  reasoning is written down precisely so they can tell which rules they may bend.

## Boundaries

- Do **not** edit any `.py` file. Everything here is configuration.
- Do **not** commit on the user's behalf unless they ask.
- Do **not** delete the example entity before step 7, and never without confirmation.
- Do **not** invent owner ids, scope names, or thresholds. Ask.
