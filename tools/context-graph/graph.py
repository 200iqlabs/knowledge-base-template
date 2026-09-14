#!/usr/bin/env python3
"""context-graph — the explicit link graph of a Markdown knowledge base.

Answers the question no index answers: *what connects to this file*. The four indexes a
knowledge base keeps (`_index.md`, `catalog.md`, `status.md`, the task registry) all
describe containment — what is inside what. An edge written in prose describes a relation,
and nothing reads those edges back, so an edge dies silently the first time a file moves.

Read-only with respect to the knowledge base. Everything it writes goes to the state
directory named in the config, which the repository is expected to ignore.

What counts as a link, and where a link resolves, is NOT decided here — it is read from
`relink.py`, the tool that repairs what this one reports. One definition, three readers
(relink, lint check #19, this): a second implementation would drift, and the dead-link
count appears in two places at once (the session line and the linter), where two numbers
for one thing is the failure this knowledge base is organised against. What is added on
top is only what relink has no reason to have: traversal over configured scopes, a cache
keyed by content hash, and the query commands.

Usage:
    python <path-to>/graph.py links <file> [--config CONFIG]
    python <path-to>/graph.py orphans | bridges | map
    python <path-to>/graph.py stats [--line]
    python <path-to>/graph.py report

Exit code: 0 always, except 2 on a usage error. The state of the knowledge base — orphans,
dead links — is the content of the answer, never a failure of the run.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time

try:
    import yaml
except ImportError:
    sys.stderr.write(
        "context-graph: missing dependency PyYAML.\n"
        "  Install with:  pip install pyyaml\n"
    )
    sys.exit(2)

# Console encoding: a Windows console defaults to a legacy codepage. Replace rather than
# raise — a mangled character is cosmetic, a traceback in a status line is not.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
# relink.py owns the definition of a link. Loaded by path: the two tools sit in sibling
# directories of the template, not in a package — the same arrangement lint.py uses.
RELINK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir,
                           "tasks", "relink.py")
CACHE_VERSION = 2
# The only edge type there is. It is carried explicitly so that adding a second source of
# edges later (co-occurrence in a commit, say) is an addition rather than a rewrite of
# everything that reads the graph.
EDGE_LINK = "link"
LOCK_NAME = "building.lock"
# Where state goes when a config does not say. Needed on one path where the config is
# exactly what could not be read — the detached build still owes its lock back.
DEFAULT_STATE_DIR = "context/.graph"
ABSENT_LINE = "context: no graph yet — building in the background"


def write_json(path: str, payload, **dump) -> None:
    """Write via a temporary file in the same directory, then rename over the target.

    The build can be running in a process the caller is only waiting on, so a reader may
    look at exactly the wrong moment. A rename is atomic; a half-written cache read as
    complete would be a wrong answer that persists until something changes.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, **dump)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --- the repository ----------------------------------------------------------

def repo_root(explicit: str | None) -> str:
    if explicit:
        return os.path.abspath(explicit)
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else os.getcwd()


def load_relink():
    try:
        spec = importlib.util.spec_from_file_location("context_graph_relink", RELINK_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module          # dataclasses look themselves up here
        spec.loader.exec_module(module)
        return module
    except (OSError, ImportError, AttributeError):
        return None


def relink_fingerprint() -> str:
    """A hash of relink.py, stored alongside the cache.

    The cache holds link targets extracted by relink's rules, keyed by the hash of the
    file they came from. That is sound only while the rules themselves hold still: change
    how relink masks code spans or resolves a target, and every unchanged file keeps
    serving its old answer — a graph quietly disagreeing with the check that shares its
    definition, until something happens to touch every file in the base. The fingerprint
    turns that into a one-off full rebuild, which is what it should have been.
    """
    try:
        with open(RELINK_PATH, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()[:16]
    except OSError:
        return "unknown"


def load_config(path: str) -> dict | None:
    """The config, or None when there is none.

    None is not an error: this tool ships inside a template that lands in repositories
    which have not configured it, and a status line is global. No config means no graph
    means say nothing.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            config = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return None
    return config if isinstance(config, dict) else None


def resolve_rules(config: dict, root: str) -> dict:
    """Scope boundaries and the orphan exemption rule.

    Both are already declared by the linter's config in a repository that has one, so
    this reads them from there rather than asking for a second copy that would drift —
    the same borrowing the linter itself does for the task-registry contract. A
    standalone config may declare them inline instead.
    """
    rules = {
        "self_index_marker": config.get("self_index_marker", "_index.md"),
        "exclude_dirs": list(config.get("exclude_dirs", [])),
        "entity_scopes": list(config.get("entity_scopes", [])),
    }
    lint_config = config.get("lint_config")
    if not lint_config:
        return rules
    lint = load_config(os.path.join(root, lint_config))
    if lint is None:
        sys.stderr.write(
            f"context-graph: lint_config not readable ({lint_config}) — "
            "falling back to the rules declared in this config\n"
        )
        return rules
    # `is not None`, not truthiness: a config that deliberately excludes nothing declares
    # an empty list, and reading that as "unset" would silently restore the fallback —
    # the linter and this tool would then disagree about the very rule they share.
    if lint.get("self_index_marker") is not None:
        rules["self_index_marker"] = lint["self_index_marker"]
    if lint.get("catalog_exclude_dirs") is not None:
        rules["exclude_dirs"] = list(lint["catalog_exclude_dirs"])
    if lint.get("scan_roots") is not None:
        rules["entity_scopes"] = [r["path"] for r in lint["scan_roots"] if r.get("path")]
    return rules


# --- the file set ------------------------------------------------------------

def walk_markdown(root: str, roots: list[str]) -> list[str]:
    """Every `.md` file under the given roots, as the working tree has it right now.

    Deliberately not `git ls-files`: that reads the **index**, so staging a newly created
    file would change what the graph — and therefore the linter's orphan check — reports,
    for content that did not change, and a deletion not yet staged would keep a file in
    the list that is no longer on disk. The linter's contract is that its result does not
    depend on what happens to be staged, and a walk is the only source of truth that can
    honour both that and "the answer describes the working tree".
    """
    found = []
    for base in roots:
        start = os.path.join(root, base)
        # A root may name one file. The rule file at the repository root is the clearest
        # case: it points into the knowledge base constantly, and nothing else would
        # bring it in.
        if os.path.isfile(start) and base.endswith(".md"):
            found.append(base.replace(os.sep, "/"))
            continue
        if not os.path.isdir(start):
            continue
        for dirpath, dirnames, filenames in os.walk(start):
            dirnames[:] = sorted(d for d in dirnames
                                 if d != ".git" and not d.startswith("."))
            for name in sorted(filenames):
                if name.endswith(".md") and not name.startswith("."):
                    rel = os.path.relpath(os.path.join(dirpath, name), root)
                    found.append(rel.replace(os.sep, "/"))
    return sorted(set(found))


def skipped(relink, rel: str, built: set) -> bool:
    """Files neither tool treats as a document with links of its own.

    A template's links resolve only once it is copied to where it will live; build output
    is rewritten from its source over any repair, so the source is what gets checked.
    relink draws both lines already, and drawing them differently here is what would make
    the two disagree.
    """
    return relink.is_template(rel) or rel in built


def untracked_markdown(root: str) -> set[str]:
    """New `.md` files git can already see: on disk, not ignored, not yet added.

    `git ls-files` reads the **index**, so a file written and not yet added is missing
    from it and appears the instant it is staged. Every set derived from it inherits
    that: reachability would answer differently for a working tree that did not change,
    which is the one thing the linter's determinism contract rules out. Asking git for
    what it calls "others" closes the gap — before `git add` the file is here, after it
    is in the tracked set, and the union is the same either way. One extra call, and it
    honours `.gitignore`, so a vendored directory full of Markdown never enters.
    """
    out = subprocess.run(["git", "-C", root, "ls-files", "--others",
                          "--exclude-standard", "-z", "--", "*.md"],
                         capture_output=True, encoding="utf-8",
                         errors="surrogateescape")
    if out.returncode != 0:
        return set()                  # not a git checkout: the walk is all there is
    return {p for p in out.stdout.split("\0") if p}


def list_nodes(relink, root: str, roots: list[str], tracked: set,
               built: set) -> list[str]:
    """The knowledge files: Markdown under `roots`, as the working tree has it.

    The predicate for ignoring is "git tracks it, or git is not told to ignore it" — a
    tracked file cannot be ignored, so only the remainder is worth asking about. That is
    not merely an optimisation: asking about all 8 000 walked files costs over two seconds
    and answers "none ignored" every time, on a command a status line runs every turn. The
    tracked set is used to skip the question, never to decide what is a node, so what
    happens to be staged still cannot change the answer.

    Membership in a git listing is deliberately not the test, and the reason is a
    measurement: 28 files under a directory with a non-ASCII name came back from
    `ls-files` spelled in a different encoding than the walk produced, so a node set
    defined as "what git listed" silently lost them. Existence is a fact about the disk;
    git is asked only the question the disk cannot answer, which is what it ignores.
    """
    found = walk_markdown(root, roots)
    if not found:
        return []
    unknown = {rel for rel in found if rel not in tracked}
    ignored = relink.ignored(root, unknown) if unknown else set()
    return [rel for rel in found
            if rel not in ignored and not skipped(relink, rel, built)]


def list_sources(relink, visible: set, built: set) -> list[str]:
    """Every file whose links can reach a node — the set relink scans, read git's way.

    Not a configured list of directories. That was tried and it is the wrong shape: any
    such list is a guess at where links live, and on a real base a carefully written one
    still missed 189 files relink reads — generated analyses, editor configuration,
    presentation sources. Every one of those is a place a dead link can hide from the
    graph while the linter reports it, which is precisely the disagreement the shared
    definition exists to rule out. Asking "what does relink scan" has no such gap by
    construction.

    Read from everything git can see rather than from the index alone, for the reason
    `untracked_markdown` gives: a new skill pointing into the base makes its target
    reachable the moment it is written, and `git add` must not be what decides whether
    the orphan check agrees. Dead links are the other way round and stay keyed on the
    index — see `reportable`, which has to match a count the linter prints beside this
    one.
    """
    return sorted(rel for rel in visible
                  if rel.endswith(".md") and not relink.sealed(rel)
                  and not skipped(relink, rel, built))


def file_hash(root: str, rel: str) -> str | None:
    try:
        with open(os.path.join(root, rel), "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


# --- extraction (cached per file, keyed by content) --------------------------

def extract(relink, root: str, rel: str) -> list:
    """Every checkable link target in one file, as written.

    Deliberately stops short of asking whether the target exists: existence depends on
    *other* files, which change without changing this one, so caching a resolved answer
    against this file's hash would go stale without any signal. What is cached is what
    the file itself determines — where each target points and how it may be read.
    """
    text = relink.read(root, rel)
    if text is None:
        return []
    targets = []
    for line, _start, _end, target in relink.iter_targets(text):
        path = relink.local_path(target)
        if path is None:                  # a URL, a bare anchor, a placeholder
            continue
        readings = relink.readings(rel, path)
        if not readings:                  # leaves the repository: not checkable here
            continue
        targets.append([line, target, path, readings])
    return targets


def refresh(relink, root: str, sources: list[str], state_dir: str, rebuild: bool,
            persist: bool = True) -> dict:
    """Re-extract only the files whose content changed since the last run.

    `persist=False` still reads the cache — it is just not allowed to update it.
    The linter asks in that mode: its whole value rests on being read-only, and a
    check that wrote something on the way to its answer would make a clean run mean
    two different things.
    """
    cache_path = os.path.join(state_dir, "cache.json")
    fingerprint = relink_fingerprint()
    cached = {}
    if not rebuild:
        try:
            with open(cache_path, "r", encoding="utf-8") as fh:
                blob = json.load(fh)
            if (blob.get("version") == CACHE_VERSION
                    and blob.get("relink") == fingerprint):
                cached = blob.get("files", {})
        except (OSError, ValueError):
            cached = {}
    files, recomputed = {}, 0
    for rel in sources:
        digest = file_hash(root, rel)
        if digest is None:
            continue
        hit = cached.get(rel)
        if hit and hit.get("hash") == digest:
            files[rel] = hit
            continue
        files[rel] = {"hash": digest, "targets": extract(relink, root, rel)}
        recomputed += 1
    if persist:
        write_json(cache_path,
                   {"version": CACHE_VERSION, "relink": fingerprint, "files": files},
                   sort_keys=True, separators=(",", ":"))
    return {"files": files, "recomputed": recomputed}


# --- resolution --------------------------------------------------------------

def resolve(relink, root: str, nodes: list[str], sources: list[str],
            files: dict, tracked: set) -> dict:
    """Turn cached targets into edges and dead links against the tree as it is now.

    Sources and nodes are two different sets on purpose. A node is a knowledge file — the
    thing that can be reported as unreachable. A source is anything whose links count as
    a way of reaching one, which includes files that are not knowledge themselves: a
    skill or a tool document pointing into the base makes its target reachable, and
    calling that target an orphan would simply be false.
    """
    node_set = set(nodes)
    seen: dict[str, bool] = {}

    def exists(rel: str) -> bool:
        hit = seen.get(rel)
        if hit is None:
            hit = seen[rel] = os.path.exists(os.path.join(root, rel))
        return hit

    out: dict[str, list] = {}
    inbound: dict[str, list] = {}
    dead_candidates = []
    for rel in sources:
        record = files.get(rel)
        if not record:
            continue
        for line, target, path, readings in record["targets"]:
            hit = next((r for r in readings if exists(r)), None)
            if hit is None:
                dead_candidates.append((rel, line, target, path, readings))
                continue
            if hit.endswith(".md") and hit in node_set and hit != rel:
                out.setdefault(rel, []).append((hit, line, EDGE_LINK))
                inbound.setdefault(hit, []).append((rel, line, EDGE_LINK))
    # A target git is told to ignore exists on the station that produced it and nowhere
    # else, so its absence here does not make the link dead. relink asks git the same
    # question; asking it the same way is what keeps the two counts equal.
    asked = {r + ("/" if c[3].endswith("/") else "")
             for c in dead_candidates for r in c[4]}
    skip = {p.rstrip("/") for p in relink.ignored(root, asked)}
    dead = []
    for rel, line, target, _path, readings in dead_candidates:
        if any(r in skip for r in readings):
            continue
        dead.append({"path": rel, "line": line, "target": target,
                     "reported": reportable(relink, rel, tracked)})
    for edges in out.values():
        edges.sort()
    for edges in inbound.values():
        edges.sort()
    dead.sort(key=lambda d: (d["path"], d["line"], d["target"]))
    return {"out": out, "in": inbound, "dead": dead}


def reportable(relink, rel: str, tracked: set) -> bool:
    """Whether a dead link in this file is worth telling anybody about.

    Material that records what happened as it happened — sealed directories, archived
    tasks, aged-out status rows — describes the state of the day it was put down, so a
    link that has since died is an accurate record, not a fault. relink draws that line
    already; this reuses it rather than redrawing it.

    Untracked files are excluded for a narrower reason: relink scans what git tracks, so
    a dead link in a file nobody has committed is invisible to check #19. Reporting it
    here would make the two counts differ — and they are shown side by side, one in the
    session line and one in the linter. The node set still includes untracked files,
    because reachability is this tool's own question and does not have to match.
    """
    return not (relink.sealed(rel) or relink.unread_by_default(rel)
                or rel not in tracked)


# --- entities and the orphan exemption ---------------------------------------

def entity_of(rel: str, scopes: list[str]) -> tuple[str, str] | None:
    """(entity name, entity root) for a file inside a folder-shaped entity, else None."""
    for scope in scopes:
        prefix = scope.rstrip("/") + "/"
        if not rel.startswith(prefix):
            continue
        rest = rel[len(prefix):].split("/")
        if len(rest) >= 2:                      # a file directly in the scope is not one
            return rest[0], prefix + rest[0]
    return None


def exempt(root: str, rel: str, rules: dict) -> bool:
    """Whether a file with no inbound link is exempt from being called an orphan.

    A subtree that indexes itself answers "how do I reach this file" on its own, and the
    machine-written research directories that make up the bulk of this base do exactly
    that. The ancestor walk stops at the entity root on purpose: a scope-level index
    lists entities, not their internals, so letting it exempt anything would exempt
    everything — measured at 100% of candidates, which is a check that never fires.
    """
    if any(part in rules["exclude_dirs"] for part in rel.split("/")[:-1]):
        return True
    found = entity_of(rel, rules["entity_scopes"])
    if not found:
        return False
    _name, entity_root = found
    marker = rules["self_index_marker"]
    directory = os.path.dirname(rel)
    while directory and directory != entity_root and directory.startswith(entity_root + "/"):
        if os.path.exists(os.path.join(root, directory, marker)):
            return True
        directory = os.path.dirname(directory)
    return False


def orphans(root: str, nodes: list[str], graph: dict, rules: dict) -> tuple[list, int]:
    reported, waived = [], 0
    for rel in nodes:
        if graph["in"].get(rel):
            continue
        if exempt(root, rel, rules):
            waived += 1
        else:
            reported.append(rel)
    return reported, waived


# --- one build at a time -----------------------------------------------------

def lock_path(state_dir: str) -> str:
    return os.path.join(state_dir, LOCK_NAME)


def new_token() -> str:
    """The identity of one build, written into the lock file it holds."""
    return f"{os.getpid()}-{time.time_ns()}"


def take_lock(state_dir: str, stale_after: float) -> str | None:
    """Claim the right to build: the token of the claim, or None if somebody holds it.

    Without this a cold start is a stampede: the status line renders every turn, finds no
    cache every time because the first build has not finished yet, and launches another
    full build on each render. The lock is a file created with O_EXCL, so the claim is
    atomic; a stale one is reclaimed, because a build that died holding it must not keep
    the graph from ever being built.

    The token is what makes reclaiming safe. Age cannot tell a dead build from a slow
    one, so a build that outruns the stale threshold has its lock taken from under it —
    and if releasing were unconditional, that build would then delete the lock of the
    one that replaced it, and the next render would start a third against the same
    directory. A release that checks the token first does nothing at all in that case.
    """
    path = lock_path(state_dir)
    token = new_token()
    try:
        os.makedirs(state_dir, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, token.encode("ascii"))
        os.close(fd)
        return token
    except FileExistsError:
        try:
            age = time.time() - os.path.getmtime(path)
        except OSError:
            return None
        if age <= stale_after:
            return None
        try:
            os.unlink(path)
        except OSError:
            return None
        return take_lock(state_dir, stale_after)
    except OSError:
        return None


def drop_lock(state_dir: str, token: str) -> None:
    """Give the lock back, but only while it is still the one this build took."""
    try:
        with open(lock_path(state_dir), "r", encoding="ascii",
                  errors="replace") as fh:
            held = fh.read().strip()
    except OSError:
        return               # no lock to give back
    if held != token:
        return               # reclaimed as stale and handed on: not ours to remove
    try:
        os.unlink(lock_path(state_dir))
    except OSError:
        pass


# --- assembling the answer ---------------------------------------------------

def build(config: dict, root: str, rebuild: bool = False,
          persist: bool = True) -> dict | None:
    relink = load_relink()
    if relink is None:
        sys.stderr.write(f"context-graph: relink.py could not be loaded ({RELINK_PATH})\n")
        return None
    rules = resolve_rules(config, root)
    state_dir = os.path.join(root, config.get("state_dir", DEFAULT_STATE_DIR))
    started = time.time()
    tracked = set(relink.tracked_files(root))
    # Everything git can see, index or working tree. Which of the two a new file is in
    # depends on whether somebody has run `git add`, and no answer here may.
    visible = tracked | untracked_markdown(root)
    walked = walk_markdown(root, config.get("scan_roots", []))
    # One question to git about build output, over everything either set can contain.
    built = relink.generated(root, sorted(set(walked) | {r for r in visible
                                                         if r.endswith(".md")}))
    nodes = list_nodes(relink, root, config.get("scan_roots", []), tracked, built)
    # Anything whose links make a node reachable, without being knowledge itself.
    sources = sorted(set(nodes) | set(list_sources(relink, visible, built)))
    state = refresh(relink, root, sources, state_dir, rebuild, persist)
    graph = resolve(relink, root, nodes, sources, state["files"], tracked)
    elapsed = time.time() - started
    reported, waived = orphans(root, nodes, graph, rules)
    edges = sum(len(v) for v in graph["out"].values())
    budget = (config.get("thresholds") or {}).get("build_seconds")
    if budget and elapsed > budget:
        # Load-bearing, not decorative: crossing it means the base outgrew the number in
        # the config, and the session line is about to become something that is waited on.
        sys.stderr.write(
            f"context-graph: build took {elapsed:.1f}s against a {budget}s budget — "
            "raise thresholds.build_seconds or narrow scan_roots\n")
    result = {
        "root": root, "state_dir": state_dir, "rules": rules,
        "nodes": nodes, "sources": sources, "graph": graph,
        "orphans": reported, "waived": waived,
        "edges": edges, "dead": [d for d in graph["dead"] if d["reported"]],
        "dead_all": graph["dead"], "seconds": elapsed,
        "recomputed": state["recomputed"],
    }
    if not persist:
        return result
    write_json(os.path.join(state_dir, "graph.json"), {
        "version": CACHE_VERSION,
        "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "seconds": round(elapsed, 3),
        "counts": {"nodes": len(nodes), "sources": len(sources), "edges": edges,
                   "orphans": len(reported), "waived": waived,
                   "dead": len(result["dead"])},
        "out": {k: [{"to": t, "line": ln, "type": ty} for t, ln, ty in v]
                for k, v in sorted(graph["out"].items())},
        "dead": result["dead"],
        "orphan_paths": reported,
    }, sort_keys=True, indent=1)
    return result


# --- commands ----------------------------------------------------------------

def cmd_links(state: dict, target: str) -> int:
    root = state["root"]
    rel = os.path.relpath(os.path.abspath(target), root).replace(os.sep, "/")
    if rel not in set(state["nodes"]):
        scopes = ", ".join(state["rules"]["entity_scopes"]) or "none configured"
        print(f"`{rel}` is not in the graph — it covers .md files in the configured "
              f"scopes ({scopes}).")
        return 0
    inbound = state["graph"]["in"].get(rel, [])
    outbound = state["graph"]["out"].get(rel, [])
    print(f"{rel}")
    print(f"  inbound: {len(inbound)}")
    for src, line, kind in inbound:
        print(f"    <- {src}:{line} ({kind})")
    print(f"  outbound: {len(outbound)}")
    for dst, line, kind in outbound:
        print(f"    -> {dst} (line {line}, {kind})")
    return 0


def cmd_orphans(state: dict, limit: int) -> int:
    reported = state["orphans"]
    print(f"orphans: {len(reported)}   exempt by rule: {state['waived']}")
    for rel in reported[:limit]:
        print(f"  {rel}")
    if len(reported) > limit:
        print(f"  ... and {len(reported) - limit} more — raise --limit to see them all")
    return 0


def entity_labels(state: dict) -> dict:
    """Entity root -> the shortest name that still identifies it.

    The root is the identity, never the folder name: the configuration carries several
    scopes at once, so two entities may share a name. Keyed by name, their edges would be
    merged into one pair and — worse — an edge between them would be read as internal and
    dropped. The name is kept as the label only while it is unique.
    """
    scopes = state["rules"]["entity_scopes"]
    roots: dict[str, str] = {}
    for rel in state["nodes"]:
        found = entity_of(rel, scopes)
        if found:
            roots[found[1]] = found[0]
    seen: dict[str, int] = {}
    for name in roots.values():
        seen[name] = seen.get(name, 0) + 1
    return {root: (name if seen[name] == 1 else root) for root, name in roots.items()}


def bridge_pairs(state: dict) -> list:
    scopes = state["rules"]["entity_scopes"]
    labels = entity_labels(state)
    pairs: dict[tuple[str, str], int] = {}
    for src, edges in state["graph"]["out"].items():
        a = entity_of(src, scopes)
        if not a:
            continue
        for dst, _line, _kind in edges:
            b = entity_of(dst, scopes)
            if not b or b[1] == a[1]:
                continue
            key = tuple(sorted((labels.get(a[1], a[1]), labels.get(b[1], b[1]))))
            pairs[key] = pairs.get(key, 0) + 1
    return sorted(pairs.items(), key=lambda kv: (-kv[1], kv[0]))


def cmd_bridges(state: dict, limit: int) -> int:
    pairs = bridge_pairs(state)
    total = sum(n for _, n in pairs)
    print(f"edges between entities: {total} across {len(pairs)} pair(s)")
    for (a, b), count in pairs[:limit]:
        print(f"  {count:5d}  {a} <-> {b}")
    if len(pairs) > limit:
        print(f"  ... and {len(pairs) - limit} more pair(s)")
    return 0


def cmd_map(state: dict, limit: int) -> int:
    scopes = state["rules"]["entity_scopes"]
    print(f"nodes: {len(state['nodes'])}   edges: {state['edges']}   "
          f"orphans: {len(state['orphans'])} (exempt: {state['waived']})   "
          f"dead links: {len(state['dead'])}")
    hubs = sorted(((len(v), k) for k, v in state["graph"]["in"].items()), reverse=True)
    print("\nbiggest hubs (inbound):")
    for count, rel in hubs[:limit]:
        print(f"  {count:5d}  {rel}")
    linked = set()
    for (a, b), _ in bridge_pairs(state):
        linked.update((a, b))
    entities = sorted(entity_labels(state).values())
    alone = [e for e in entities if e not in linked]
    print(f"\nentities with no outside link: {len(alone)} of {len(entities)}")
    for name in alone:
        print(f"  {name}")
    return 0


def line_from_counts(counts: dict, fresh: str) -> str:
    return (f"context: {counts['nodes']} nodes · {counts['edges']} edges · "
            f"{counts['orphans']} orphans · {counts['dead']} dead links · {fresh}")


def stats_line(state: dict) -> str:
    fresh = "fresh" if state["recomputed"] == 0 else f"{state['recomputed']} recomputed"
    return line_from_counts(
        {"nodes": len(state["nodes"]), "edges": state["edges"],
         "orphans": len(state["orphans"]), "dead": len(state["dead"])}, fresh)


def cmd_stats(state: dict, one_line: bool) -> int:
    if one_line:
        print(stats_line(state))
        return 0
    print(stats_line(state))
    print(f"  link sources outside the node scopes: "
          f"{len(state['sources']) - len(state['nodes'])}")
    print(f"  exempt by the self-indexing subtree rule: {state['waived']}")
    print(f"  dead links in material put down (not reported): "
          f"{len(state['dead_all']) - len(state['dead'])}")
    print(f"  build: {state['seconds']:.2f}s, files re-extracted: {state['recomputed']}")
    return 0


def cmd_report(state: dict, limit: int) -> int:
    path = os.path.join(state["state_dir"], "report.md")
    outside = len(state["sources"]) - len(state["nodes"])
    lines = [
        "# Knowledge base link graph",
        "",
        "Generated and strictly local — it is not committed. The content is a function of "
        "the repository alone: two runs over an unchanged tree write the same bytes, so "
        "two reports can be diffed against each other. When it was built, and how long "
        "that took, are in `graph.json` and in `stats`, deliberately not here.",
        "",
        "| Measure | Value |",
        "|---|---|",
        f"| nodes (.md files in the scopes) | {len(state['nodes'])} |",
        f"| link sources outside those scopes | {outside} |",
        f"| edges (.md -> .md links) | {state['edges']} |",
        f"| dead links (files read by default) | {len(state['dead'])} |",
        f"| dead links in material put down | {len(state['dead_all']) - len(state['dead'])} |",
        f"| orphans | {len(state['orphans'])} |",
        f"| exempt by the self-indexing subtree rule | {state['waived']} |",
        "",
        "## Dead links",
        "",
    ]
    if state["dead"]:
        for item in state["dead"]:
            lines.append(f"- `{item['path']}:{item['line']}` -> `{item['target']}`")
    else:
        lines.append("None.")
    lines += ["", "## Biggest hubs", ""]
    hubs = sorted(((len(v), k) for k, v in state["graph"]["in"].items()), reverse=True)
    for count, rel in hubs[:limit]:
        lines.append(f"- {count} inbound — `{rel}`")
    lines += ["", "## Bridges between entities", ""]
    for (a, b), count in bridge_pairs(state)[:limit]:
        lines.append(f"- {count} — {a} <-> {b}")
    lines += ["", "## Orphans", ""]
    if state["orphans"]:
        for rel in state["orphans"][:limit]:
            lines.append(f"- `{rel}`")
        if len(state["orphans"]) > limit:
            lines.append(f"- ... and {len(state['orphans']) - limit} more")
    else:
        lines.append("None.")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"report: {os.path.relpath(path, state['root']).replace(os.sep, '/')}")
    return 0


# --- the cold path -----------------------------------------------------------

def start_background_build(config_path: str, root: str, state_dir: str,
                           token: str) -> None:
    """Build the graph in a process nobody waits for.

    The child is handed the parent's token and releases the lock on every way out of
    its own process, not only on the way out through a build that worked: ownership
    travels with the build, and a build that cannot even read the config still owes the
    lock back. If the spawn fails the lock is dropped here and now — otherwise every
    later call would wait out the full budget while nothing at all was building.
    """
    flags = {}
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP — no console, no signal inheritance
        flags["creationflags"] = 0x00000008 | 0x00000200
    else:
        flags["start_new_session"] = True
    try:
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "report",
             "--release-lock", token, "--config", config_path, "--root", root],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, cwd=root, **flags)
    except OSError:
        drop_lock(state_dir, token)


def state_file(config: dict, root: str, name: str) -> str:
    return os.path.join(root, config.get("state_dir", DEFAULT_STATE_DIR), name)


def is_cold(config: dict, root: str) -> bool:
    """Whether answering would mean a full build rather than a cheap refresh.

    Not merely "is there a cache file". A cache written by an older version, or by a
    different `relink.py`, is discarded on read — so it exists and is worth nothing, and
    the synchronous path would then do the whole build while a session hook waits on it,
    straight past the budget that exists to stop exactly that. A missing `graph.json`
    counts too: it is what the waiting call reads its numbers from.
    """
    if not os.path.exists(state_file(config, root, "graph.json")):
        return True
    try:
        with open(state_file(config, root, "cache.json"), "r", encoding="utf-8") as fh:
            blob = json.load(fh)
    except (OSError, ValueError):
        return True
    return not (blob.get("version") == CACHE_VERSION
                and blob.get("relink") == relink_fingerprint())


def read_graph(path: str) -> dict | None:
    """The counts a build published, or None when there is nothing usable to read."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            blob = json.load(fh)
    except (OSError, ValueError):
        return None
    return blob if blob.get("version") == CACHE_VERSION and "counts" in blob else None


def await_graph(path: str, budget: float) -> dict | None:
    """Wait for a detached build to publish its counts, up to the budget.

    Waiting on `graph.json` rather than on the cache is what keeps the first call from
    doing the work twice: the counts are already in that file, so there is nothing left
    to compute once it appears. Only ever called when there was nothing to read — an
    artefact already on disk is never waited for, and never called fresh.
    """
    deadline = time.time() + budget
    while True:
        blob = read_graph(path)
        if blob is not None:
            return blob
        if time.time() >= deadline:
            return None
        time.sleep(0.2)


def cold_line(config: dict, root: str, config_path: str) -> None:
    """Print the one-line answer when answering otherwise means a full build.

    The budget is what the repository declares it will tolerate on a first build, and it
    is the whole answer to "build it or say it is missing": under it, the caller gets real
    numbers on this very call; over it, the line says the graph is not there yet and the
    build carries on for the next one. Either way nobody waits longer than the number in
    the config.

    Cold does not always mean empty. A cache discarded for its version or its `relink.py`
    fingerprint leaves the last build's `graph.json` behind, and those counts are worth
    printing — they are simply not fresh, and printing them under the word `fresh` is the
    one thing this line must never do, since a rebuild was just launched precisely
    because they are out of date. They go out at once, labelled as being rebuilt, and
    the next call reads the new ones.
    """
    state_dir = os.path.join(root, config.get("state_dir", DEFAULT_STATE_DIR))
    budget = float((config.get("thresholds") or {}).get("build_seconds") or 0)
    path = state_file(config, root, "graph.json")
    published = read_graph(path)          # read before the rebuild can replace it
    token = take_lock(state_dir, stale_after=max(budget * 3, 60))
    if token:
        start_background_build(config_path, root, state_dir, token)
    if published is not None:
        print(line_from_counts(published["counts"], "rebuilding"))
        return
    blob = await_graph(path, budget)
    print(ABSENT_LINE if blob is None else line_from_counts(blob["counts"], "fresh"))


# --- entry point -------------------------------------------------------------

def main(argv: list[str]) -> int:
    # The shared flags are attached to the top level AND to every subcommand, so that
    # `graph.py stats --line --config X` works as readily as `graph.py --config X stats`.
    # A hook writes the first form without thinking about it, and argparse would
    # otherwise reject it. SUPPRESS keeps the two copies from fighting: an option absent
    # from the command line sets no attribute at all, so whichever side supplied it wins
    # instead of the subcommand's default silently overwriting the top level's value.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS,
                        help="path to config.yaml (default: next to this script)")
    common.add_argument("--root", default=argparse.SUPPRESS,
                        help="repository root (default: git rev-parse)")
    common.add_argument("--rebuild", action="store_true", default=argparse.SUPPRESS,
                        help="recompute everything, ignoring the cache")
    common.add_argument("--release-lock", metavar="TOKEN", default=argparse.SUPPRESS,
                        help=argparse.SUPPRESS)   # internal: the token this build holds
    common.add_argument("--limit", type=int, default=argparse.SUPPRESS,
                        help="how many rows a list prints (default 15)")
    parser = argparse.ArgumentParser(
        prog="context-graph", parents=[common],
        description="The explicit link graph between knowledge base files.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_links = sub.add_parser("links", parents=[common],
                             help="inbound and outbound links of one file")
    p_links.add_argument("file")
    sub.add_parser("orphans", parents=[common],
                   help="files with no inbound link at all")
    sub.add_parser("bridges", parents=[common],
                   help="edges crossing an entity boundary, by entity pair")
    sub.add_parser("map", parents=[common],
                   help="size distribution: nodes, edges, hubs, entities")
    p_stats = sub.add_parser("stats", parents=[common], help="state of the graph")
    p_stats.add_argument("--line", action="store_true",
                         help="one sentence, for a status line or session hook")
    sub.add_parser("report", parents=[common],
                   help="write the Markdown report into the state directory")
    args = parser.parse_args(argv)
    config_path = getattr(args, "config", DEFAULT_CONFIG)
    limit = getattr(args, "limit", 15)

    # Set only on the detached build the cold path spawned. This process holds the lock
    # and owes it back on EVERY way out, not only on the way out through a build that
    # worked: a config that cannot be read, a relink that will not load, an exception
    # halfway through — each used to end the process before the release and leave the
    # lock standing until it went stale, with every status line in between waiting out
    # the full budget for a build that was no longer running.
    token = getattr(args, "release_lock", None)
    config = load_config(config_path)
    if config is None:
        if token:
            # The one path with no config to read `state_dir` from, so the default is
            # the best that can be done; a wrong guess unlinks nothing.
            drop_lock(os.path.join(repo_root(getattr(args, "root", None)),
                                   DEFAULT_STATE_DIR), token)
        return 0            # no config: this repository has no graph, so say nothing
    root = repo_root(getattr(args, "root", None))
    try:
        # Checked before building, because the point is not to build now: only the
        # one-line form is on a render path, so only it refuses to wait.
        if (args.command == "stats" and getattr(args, "line", False)
                and not getattr(args, "rebuild", False) and is_cold(config, root)):
            cold_line(config, root, config_path)
            return 0
        state = build(config, root, rebuild=getattr(args, "rebuild", False))
        if state is None:
            return 0
        if args.command == "links":
            return cmd_links(state, args.file)
        if args.command == "orphans":
            return cmd_orphans(state, limit)
        if args.command == "bridges":
            return cmd_bridges(state, limit)
        if args.command == "map":
            return cmd_map(state, limit)
        if args.command == "stats":
            return cmd_stats(state, getattr(args, "line", False))
        if args.command == "report":
            return cmd_report(state, limit)
        return 2
    finally:
        if token:
            drop_lock(os.path.join(root, config.get("state_dir", DEFAULT_STATE_DIR)),
                      token)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
