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

**Nothing is lost on the way.** No step changes what a link says, or creates, moves or
deletes a file. A link nobody can resolve stays dead and is handed to the user — unless
its target never entered the repository: nobody ever can resolve that one, so its name
stays as plain text with a note saying why (*When the target is gone for good*).

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
- Not reported, by design: URLs, `/`-absolute site paths, git-ignored targets, sealed
  material (`archive/`, `communication/`, `output/`, `inbox/`), whose links are part of
  the record, and files `.gitattributes` marks `linguist-generated` — build output, whose
  links are checked in the source it is built from.

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
| links in build output nobody has marked yet (a release copy, an export — its README names the build) | you | one line in `.gitattributes`: `<pattern> linguist-generated=true`, with a comment naming the build; from then on its source is what gets checked |
| anything larger | a subagent on a mid-tier model | the brief below, one batch per set of disjoint files |
| no candidate at all, even after the subagent | the user | one list, grouped by the decision it needs — never invent a target, and drop a link only as *When the target is gone for good* says |

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
   name, a section, a document described in the sentence? The text may already say the
   target was removed on purpose — orders and prompts are often deleted once carried out.
   Then skip the search and go straight to *When the target is gone for good* below.
2. **Find candidates, cheapest first**, and stop at the first that settles it:
   1. the lead from the report — open the candidate, confirm it keeps the promise;
   2. an id in the link text (`<PREFIX>-<number>`):
      `git grep -l -E '^id: <ID>$' -- '*/tasks/*.md'` (archived tasks included);
   3. the target's history — find the commit that removed it, then read that whole commit,
      because a history narrowed to one path shows a bare deletion and hides both a rename
      destination and content that landed in another file of the same commit:
      `git log --format='%h %ad %s' --date=short --diff-filter=DR -- '<repo path of the target>'`,
      then `git show --stat -M <commit>`;
   4. the file name anywhere: `git ls-files '*<name>*'`;
   5. distinctive words from the link text: `git grep -l -F '<words>'`.
3. **Decide.** Exactly one candidate that keeps the promise → repair. Several, or none that
   keeps it → leave the link as it is and report it unresolved, with what you tried. A
   target the history shows deleted, or never shows at all, is decided by *When the target
   is gone for good* below.
   **Confirm identity by the target's own identifier** — the id, run id or date in its
   header — never by a word that merely occurs in it. Records get renumbered, and two
   records can share a short id: an id plus a matching keyword once pointed at a
   different, earlier record than the one the link meant. The converse holds as well: a
   matching id settles it even when the target's title no longer resembles the link text —
   titles drift under a stable id, so a title mismatch alone is no reason to keep looking.
   **Two documents naming different successors have usually split the deleted file's
   roles** — one took its text, the other a summary or a map of it. The link's promise
   picks one; a sentence of the old version (`git show <deleting commit>^:<path>`) found
   with `git grep -F` shows which file holds the text.
4. **Repair the target only** — the part inside `( )`. Prefer the tool, which rewrites only
   dead links and checks the new target exists:
   ```bash
   python tools/tasks/relink.py <file> --retarget '<old target>' '<new target>'
   ```
   The new target is written relative to the linking file, or as a repository path, which
   the tool converts, or as a file's address at a commit, which the tool checks against
   the local history. Edit by hand only what `--retarget` cannot express — the same old
   target meaning two different files inside one file, or a file in another repository.
   **Pass `--own <each file of your batch>` on every run**, and put every decision about
   one file into a single run (`--retarget` repeats). The tool never edits a file with
   uncommitted changes it was not told are yours — and after your first run, your own
   edits are exactly that.
   **Leave generated files alone.** A directory whose README says its files are built from
   somewhere else (a release copy, an export for another repository) is rebuilt over any
   repair, and its links may be meant for the destination. Report such a link unresolved
   with `options: generated — mark it linguist-generated, the source is checked instead`.
5. **Never**: create, move, rename or delete a file, edit sealed material, touch a file
   outside the batch, commit, or guess — and never change link text or prose or remove a
   link, except as the last row of the table below says.
6. **Check your batch** — `python tools/tasks/relink.py <your files>`: every link you
   report as repaired is gone from the output.

### When the target is gone for good

The history shows the target deleted, or never shows it at all. What takes its place
depends on why — read the deleting commit's message, or the decision it cites. A deletion
whose reason nobody recorded is unresolved.

| The target | What takes its place | How |
|---|---|---|
| lives in another repository | the file's page on that repository's code host, on its default branch — a relative path into a sibling checkout breaks wherever that directory is named differently | a direct edit of the target, once you have seen the file on that branch |
| was deleted once carried out (an order, a prompt), and nothing took its content over | its last version: the file's page on the code host at the commit before the deletion — the link text stays true, and the address never dies | `--retarget '<old>' '<address>'`, which checks that the commit holds the file. On GitHub the address is `https://github.com/<owner>/<repo>/blob/<commit>/<path>`, from `git remote get-url origin` and `git rev-parse <deleting commit>^` |
| was deleted because it was wrong, or was superseded | its live successor, here or in another repository — never its old version, which is exactly what the deletion withdrew | as any repair; no successor → unresolved |
| never entered the repository: `git log --all --diff-filter=A -- '*<name>*'` finds nothing | nothing can: keep the name as plain text, remove only the link, and add a short note in parentheses where it was — that there is no link, and why, when the history or the user says | a direct edit. Many such links in one file each get the short note, and the top of the file gets one sentence explaining it |

The link text stays as written in the first three rows. The last row is the only place a
link is removed — nothing is lost there, because that link could never lead anywhere.

## Report

Return exactly this, and no other prose. Evidence that quotes the target is copied
verbatim — the orchestrator searches for it, and a paraphrase that fails the search costs
it the time you saved:

```
REPAIRED
<path>:<line>  <old target> -> <new target | unlinked>  evidence: <lead | id | rename <commit> | name | words | deleted <commit>: <reason> | never added>
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
   are gone. `git diff --word-diff -U0 -- <batch files>`: every change sits inside `( )`,
   except where the report says `unlinked` — there the brackets go and a short note comes in.
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
