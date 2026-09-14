# context-graph

The explicit link graph of a Markdown knowledge base. It answers the one question none of
the indexes answer.

`_index.md`, `catalog.md`, `status.md` and the task registry all describe **containment** —
what is inside what. A link written in prose describes a **relation**, and nothing reads
those relations back. So an edge dies silently the first time a file moves: on the machine
that wrote it, nothing reports it.

## Commands

Every command refreshes before it answers, so there is no separate build step.

| Command | Answers |
|---|---|
| `links <file>` | what points at this file, and what it points at |
| `orphans` | files nothing links to, minus the ones exempt by rule |
| `bridges` | edges crossing an entity boundary, grouped by entity pair |
| `map` | nodes, edges, the biggest hubs, entities with no outside link |
| `stats [--line]` | the state of the graph; `--line` is the one-sentence form |
| `report` | writes the Markdown report into the state directory |

```bash
python template/tools/context-graph/graph.py map --config tools/context-graph/config.yaml
python template/tools/context-graph/graph.py links context/projects/EXAMPLE/status.md --config tools/context-graph/config.yaml
```

`--config` and `--root` work on either side of the command name, because a hook writes
them after it without thinking about it. `--rebuild` ignores the cache; `--limit N` sets
how many rows a list prints.

**Exit code is always 0** (except 2 for a usage error). Orphans and dead links are the
content of the answer, never a failure of the run — a status line that goes red because
the knowledge base has a loose end would be red permanently.

## What counts as a link

**This tool does not decide that.** The definition of a link, and of where a link
resolves, is read from `../tasks/relink.py` — the tool that repairs what this one reports.
One definition, three readers: relink, the linter's dead-link check, and this.

That matters because the dead-link count is shown in two places at once — the session line
and the linter — and two implementations would eventually print two numbers for one fact.
It also means the graph inherits the parts that are easy to get wrong: fenced blocks and
inline code spans are masked (a `[x](y)` shown as an example is not an edge), `<...>`
targets and reference definitions are understood, `<name>`/`{id}` placeholders are not
paths, and `_template*` files are skipped because their links only resolve once copied.

A target is resolved **against the file's own directory first, then against the repository
root**; the first hit wins. A link carrying a `#section` anchor is an edge to the file —
headings are not separate nodes, because in a base of this size they were 12 links in
11 345. External `http`/`https` targets are not edges and are never reported.

Every edge carries a **type**, today always `link`. It is spelled out rather than assumed
so that adding a second source of edges later — co-occurrence in a commit, say — is an
addition instead of a rewrite of everything that reads the graph.

## Nodes and sources are two different sets

A **node** is a knowledge file: something the graph answers about, and something that can
be reported as unreachable. Nodes come from `scan_roots`.

A **source** is anything whose links count as a way of reaching a node, without being
knowledge itself — a rules file, a skill, a tooling document. Sources come from
`source_roots`, and a root there may name a single `.md` file rather than a directory. A
file reachable only from a skill is reachable; calling it an orphan would simply be false.

The file list is the **working tree**, not `git ls-files`. The index would make the answer
depend on what happens to be staged: a newly created file would appear the moment it was
staged, and a deletion not yet staged would keep a file in the list that is no longer on
disk. The linter's contract is that its result does not depend on the staging area, and a
walk is the only source of truth that honours both that and "the answer describes the
working tree". Git is still asked which files it ignores — but only about files it does
not already track, because a tracked file cannot be ignored, and asking about all of them
costs seconds on a command that runs every turn.

## The orphan exemption

A file nothing links to is **not** reported as an orphan when it sits in a subtree that
indexes itself — a directory holding the `self_index_marker` file — and the search upwards
**stops at the entity root**. A scope-level index does not exempt anything: it lists
entities, not their internals.

That boundary is the whole rule. Measured three ways on a live base of 8 219 files:

| Where the index has to be | Exempts | Left to report |
|---|---|---|
| in the file's own directory | 2% | ~6 300 — useless |
| in any ancestor at all | 100% | ~30 — useless the other way |
| **in an ancestor below the entity root** | **~6 300** | **~170** |

Directories the linter already excludes from per-file cataloguing are exempt here too, for
the same reason: they are referenced as a folder, not file by file.

The report prints the number exempted next to the number reported, so that somebody
dropping an `_index.md` halfway up an entity — and silently exempting hundreds of files —
shows up as a jump rather than as silence.

## Configuration

Passed at invocation; the copy next to the script is a neutral default.

| Key | Meaning |
|---|---|
| `scan_roots` | where nodes are looked for |
| `source_roots` | files whose links count as a way in, without being nodes |
| `state_dir` | where the graph, hash cache and report are written |
| `lint_config` | the linter config to borrow the scope and exemption rules from |
| `self_index_marker`, `exclude_dirs`, `entity_scopes` | inline fallbacks, used only when `lint_config` cannot be read |
| `thresholds.build_seconds` | how long a first build may be waited for |

The exemption rule and the scope boundaries are **borrowed from the linter's config** in a
repository that runs one, rather than restated here — a second copy would drift the first
time somebody adds a directory to one of them, and the linter's orphan check has to report
exactly what `orphans` reports. A value there is read as configured: an explicitly empty
exclusion list means "exclude nothing", not "unset, use the fallback".

## Cost, and why nothing is committed

Measured on a base of 8 219 nodes, 344 further link sources and 11 025 edges:

| | Time |
|---|---|
| cold build (`--rebuild`) | **~5.8 s** |
| warm call (nothing changed) | **~2.2 s** |
| one file changed | ~2.2 s, one file re-extracted |
| cold `stats --line` | the build, or `build_seconds` — whichever comes first |

Recomputation is keyed on a **content hash**, not a modification time: a checkout can
restore an mtime, and a graph that believes a stale cache reports links that no longer
exist. What is cached per file is what that file alone determines — where each target
points. Whether the target *exists* is resolved fresh every run, because it depends on
other files, which change without changing this one.

The cache also carries a fingerprint of `relink.py`. The targets in it were extracted by
relink's rules, so those rules are part of what the cache depends on: change how a code
span is masked or a target resolved, and every unchanged file would otherwise keep serving
its old answer — a graph quietly disagreeing with the check that shares its definition.
The fingerprint turns that into a single full rebuild.

`stats --line` never waits longer than the repository said it would. Finding no cache, it
starts the build in a **detached** process and waits for it — but only up to
`thresholds.build_seconds`. Under that, the call answers with real numbers. Over it, the
line says the graph is not there yet and returns; the build carries on, and the next call
answers from the cache. A session hook wired to this command therefore needs a timeout
**above** that budget, or it gets killed before it can print either answer.

Only one build runs at a time. The right to start one is claimed by creating a lock file
with `O_EXCL`, so the claim is atomic and a status line rendering every turn cannot
stampede a cold repository with one full build per render. A lock left behind by a build
that died is reclaimed once it goes stale. The cache and the graph are written to a
temporary file and renamed over the target, because the waiting call is reading a file
another process is writing: a rename is atomic, and a half-written cache read as complete
would be a wrong answer that sticks.

**The state directory is never committed.** The graph is fully reproducible from files
that *are* in the repository, so history carries the cause rather than the effect — and a
committed state file would be a conflict factory in a repository worked on from several
sessions at once. Add `state_dir` to `.gitignore`; the template ships with it already
there.

The consequence, accepted on purpose: a fresh checkout has no graph until something builds
one, and a base falling apart leaves no trace in `git diff`. What compensates is that the
signal arrives where it matters — in the session line and in the linter's checks, at the
moment somebody is about to act on it.
