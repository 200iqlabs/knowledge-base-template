---
name: relink
description: "Repair dead links in the knowledge base in a fixed order — check with
  tools/tasks/relink.py, let the script repair what a move explains (an archived task above
  all), decide the rest by size (yourself for one pattern or a couple of links, a mid-tier
  subagent working from this skill for more), verify, commit per scope, and improve this
  skill from what the subagent reported. Use after archiving tasks, when lint check #19
  reports dead links, when a link leads nowhere, or when the user asks to fix broken links."
license: Apache-2.0
---

# relink — dead links, repaired in a fixed order

Two roles. The **orchestrating agent** runs the order, makes the decisions, verifies,
commits and improves this file. A **repair subagent** repairs one batch it was handed and
reports — it reads only `Repair procedure` and `Report`; everything else is for the
orchestrator.

Paths below are the template's (`tools/tasks/relink.py`). A repository keeping the
template in a subdirectory says in its own `CLAUDE.md` where the tool sits.

## Why this order

**The tool proves, an agent decides.** `relink.py` writes only what the file system
proves — the link is dead as written and the corrected target exists. Which file an author
meant is judgement, and a script that guesses turns a dead link into a wrong live one. So
judgement stays with an agent, and even then the tool carries the decision out and checks
it again (`--retarget`).

**Every link goes to the cheapest hand that can repair it correctly.** The script costs
nothing. A direct decision costs the orchestrator a few reads. A subagent costs a context
of its own — worth it only when the alternative is the orchestrator reading dozens of files
it will never need again.

**Nothing is lost on the way.** No step removes a link, changes its text, or creates, moves
or deletes a file. A link nobody can resolve stays dead and is handed to the user.

## Step 1 — Check

```bash
python tools/tasks/relink.py [PATH...]          # text report, writes nothing
python tools/tasks/relink.py [PATH...] --json   # every field: triage and briefs
```

- `REPAIRABLE` — `archived` (the task sits in `_archive/` next to where the link looks),
  `unarchived` (the reverse), `moved` (the file holding the link went one directory down).
  The script repairs these in Step 2.
- `DEAD` — needs a decision. Each carries a lead: `<target> resolves (one ../ more|fewer)`,
  `the only file named …`, `N files named …`, or `no tracked file named …`.
- Not reported, by design: URLs, `/`-absolute site paths, git-ignored targets, and sealed
  material (`archive/`, `communication/`, `output/`, `inbox/`), whose links are part of
  the record.

## Step 2 — Repair what a move explains

```bash
python tools/tasks/relink.py --apply [--own PATH]   # --own: uncommitted work that is yours
```

A `SKIPPED` line is a file with somebody else's uncommitted changes — the tool never edits
those. Leave it and mention it; it will be repairable once they commit. Then Step 5.

## Step 3 — Triage the dead links

Group first, then hand each group out. A group shares a target, a kind of lead, or a
directory — the JSON sorted by `target`, then by `hint`, shows them at once.

| Group | Who | How |
|---|---|---|
| a pattern whose meaning fits one sentence ("every prompt's `_index.md` means its experiment's index, one level up") | you | open 2–3 of the candidates and confirm they are what the link text promises, then one `--retarget OLD NEW` over the group's paths |
| one or two unrelated links | you | the same check, then `--retarget` on that file or one direct edit |
| anything larger | a subagent on a mid-tier model | the brief below, one batch per set of disjoint files |
| no candidate at all, even after the subagent | the user | one list, grouped by the decision it needs — never invent a target, never drop the link |

"One or two commands" is the limit for doing it yourself. Past it, your context pays for
reading files a subagent could have read instead. The smallest model tier is not relied on
for this: telling "the only file with that name" from "the file the author meant" is the
whole job.

### The brief

- Read `<absolute path to this file>` — the sections `Repair procedure` and `Report`, and
  nothing else of the conversation.
- The repository root, and your batch: the JSON records (`path`, `line`, `target`,
  `hint`) — about 40 links at most, in files no other batch touches.
- Do not commit. Do not touch files outside the batch.

Batches with disjoint files run in parallel.

## Repair procedure (for the subagent)

For each link in the batch:

1. **Read the line and a few around it.** What does the link promise — a task id, a file
   name, a section, a document described in the sentence?
2. **Find candidates, cheapest first**, and stop at the first that settles it:
   1. the lead from the report — open the candidate, confirm it keeps the promise;
   2. an id in the link text (`<PREFIX>-<number>`):
      `git grep -l -E '^id: <ID>$' -- '*/tasks/*.md'` (archived tasks included);
   3. the target's history, which shows renames and deletions:
      `git log --format='%h %ad %s' --date=short --name-status -M -- '<repo path of the target>'`;
   4. the file name anywhere: `git ls-files '*<name>*'`;
   5. distinctive words from the link text: `git grep -l -F '<words>'`.
3. **Decide.** Exactly one candidate that keeps the promise → repair. Several, or none that
   keeps it → leave the link as it is and report it unresolved, with what you tried.
4. **Repair the target only** — the part inside `( )`. Prefer the tool, which rewrites only
   dead links and checks the new target exists:
   ```bash
   python tools/tasks/relink.py <file> --retarget '<old target>' '<new target>'
   ```
   The new target is written relative to the linking file, or as a repository path, which
   the tool converts. Edit by hand only what `--retarget` cannot express — the same old
   target meaning two different files inside one file.
   **Pass `--own <each file of your batch>` on every run**, and put every decision about
   one file into a single run (`--retarget` repeats). The tool never edits a file with
   uncommitted changes it was not told are yours — and after your first run, your own
   edits are exactly that.
   **Leave generated files alone.** A directory whose README says its files are built from
   somewhere else (a release copy, an export for another repository) is rebuilt over any
   repair, and its links may be meant for the destination. Report such a link unresolved
   with `options: generated — repair the source or the build`.
5. **Never**: change link text or prose, remove a link, create, move, rename or delete a
   file, edit sealed material, touch a file outside the batch, commit, or guess.
6. **Check your batch** — `python tools/tasks/relink.py <your files>`: every link you
   report as repaired is gone from the output.

## Report

Return exactly this, and no other prose:

```
REPAIRED
<path>:<line>  <old target> -> <new target>  evidence: <lead | id | rename <commit> | name | words>
UNRESOLVED
<path>:<line>  <target>  tried: <1-5>  options: <what a person could choose between>
PROBLEMS
<what got in the way: a tool error, an unclear step, a layout the procedure did not expect>
SKILL
<one or two concrete changes to this procedure that would have saved a step or a mistake —
generic, no real names or paths>
```

## Step 4 — Hand out, then Step 5 — verify, commit, learn

1. **Verify each batch** — `python tools/tasks/relink.py <batch files>`: the repaired links
   are gone. `git diff --word-diff -U0 -- <batch files>`: every change sits inside `( )`.
2. **Spot-check three repairs** per batch by opening the new target.
3. **Commit per scope**, naming the effect ("links from the research notes reach the
   recipe at its new place"), with explicit paths — never `git add -A`.
4. **Unresolved → the user**, one list, grouped by the decision it needs.
5. **Learn.** Read `PROBLEMS` and `SKILL` in every report. A point that would have prevented
   a wrong repair or a wasted step goes into `Lessons` below — generic, with no real names or
   paths, because this file is shared — and is committed on its own. The same point from
   two reports belongs in the procedure itself, not in `Lessons`.

## Lessons

Append-only. Each entry: the pattern, what to do, why.

## Out of scope

- **Moves between entities.** The tool proves archive moves only; a task moved to another
  entity shows up as `DEAD` with the lead "the only file named …" — a decision, Step 3.
  If such moves become common, teach the tool git's rename record instead.
- **Sealed material.** Links in `archive/`, `communication/`, `output/` and `inbox/` are
  part of the record and are never repaired.
