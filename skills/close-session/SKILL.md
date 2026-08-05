---
name: close-session
description: "Session-closing ritual — detect the scope from git, extract decisions from
  communication/ and from the conversation into status.md/data/, update the three index
  files (status/catalog/_index), run the linter and fix ERRORs inside the scope, review
  stale data with the user's approval, summarise, and commit once per scope using the
  scope table in CLAUDE.md. Triggers on '/close-session', 'close the session', 'wrap up',
  'close this entity', 'commit per scope'. Sends no messages, creates no deliverables,
  touches no files outside the detected scope."
license: Apache-2.0
---

# /close-session — the session-closing ritual

A deterministic order of steps — **the order matters**:
**scope → extraction → the three files → lint → sweep → summary → commit.**

Lint runs **after** the three files are updated, because it catches what the agent
missed. The sweep runs **after** `status.md`, because only then is it visible which
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
  them, treat them as one scope and skip steps 2–5 for them apart from the lint.
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

For each confirmed scope, run steps 2–5. The commits are split in step 7.

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

## Step 3 — Update the three index files

Three roles, **never three copies of state**:

- **`status.md`** — the only source of truth. Flip rows according to what happened in
  the session: what shipped, what was sent, where the ball went. Legend: 🟢 DONE ·
  🟡 OPEN · 🔴 BLOCKED · ⚪ TODO. A 🟡/🔴 row **must name the party** being waited on.
- **`catalog.md`** — add new and moved files. It is a map of files, **not** status. A
  directory with its own `_index.md` is entered at folder level, not file by file.
- The scope's **`_index.md`** — one row per entity plus the date of last activity. No
  state spelled out; that is what `status.md` is for.

Do not guess 🟡 vs 🔴 — when the material does not say whose side the ball is on, ask.
If `status.md` has passed the line threshold from `tools/context-lint/config.yaml`,
propose condensing the 🟢 items.

---

## Step 4 — Lint and fix the ERRORs

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

## Step 5 — Sweep stale data (with the user's approval)

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

## Step 6 — Conditional: the scope has its own closing skill

Some scopes carry domain knowledge this skill deliberately does not have — a CRM, a
billing system, an external register. When such a scope defines its own closing skill:

- **Propose** running it and wait for the decision.
- **Do not perform its consistency checks yourself.** That skill knows the semantics of
  its external system; duplicating its checks here would push domain knowledge into a
  generic skill and produce two registers that drift apart.

Which scopes have one is recorded in `CLAUDE.md` → `Session Hygiene`. No such scope in
play → skip this step **silently**.

---

## Step 7 — Summary and one commit per scope

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
- A problem in a folder outside the scope → report it, do not fix it.

## References

- Lint: `tools/context-lint/` (`README.md` = the check catalogue, `config.yaml` =
  thresholds and exceptions).
- Conventions: `CLAUDE.md` → `Status Protocol`, `Communication Files`,
  `Commit Convention`, `Session Hygiene`.
