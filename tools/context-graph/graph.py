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

Exit code: 0, except 2 on a usage error or a broken installation — a config that exists and
will not parse, a `relink.py` that will not load. The state of the knowledge base — orphans,
dead links — is the content of the answer and never a failure of the run; not being able to
answer at all is, because the caller that silences stderr reads only the code, and 0 there
leaves a stale graph standing without a word.
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


class ConfigError(Exception):
    """A config that exists and cannot be used — a different thing from no config at all.

    No config means this repository has no graph, and the answer to that is silence. A
    config that will not parse means somebody configured one and it is broken, and
    answering that with silence would report a healthy repository: the pre-commit block
    would read exit 0, say nothing, and leave the graph stale without a word.
    """


def _yaml():
    """PyYAML, imported only when something actually has to be parsed.

    The status line is global — it runs in repositories that have never heard of this
    tool, and there the contract is empty output and exit 0. A dependency check at import
    time would print an installation hint in every one of them, which is exactly the
    noise the empty-output contract exists to prevent. So the dependency is owed by a
    repository that has a config to read, and by no other.
    """
    try:
        import yaml
    except ImportError as exc:
        raise ConfigError("missing dependency PyYAML — install with: "
                          "pip install pyyaml") from exc
    return yaml


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
# Publishing is a second, much shorter claim, and a separate one on purpose: a build that
# takes no build lock (an ordinary `map`, or the linter) still has to publish safely.
PUBLISH_LOCK_NAME = "publishing.lock"
# How long a publish waits for the one in front of it, and how long a held publish claim
# may stand before it is assumed dead. A publish is one JSON write over a file already in
# memory, so seconds are generous; the wait is bounded because the numbers still go out
# either way.
PUBLISH_WAIT = 2.0
PUBLISH_STALE = 30.0
# Where state goes when a config does not say. Needed on one path where the config is
# exactly what could not be read — the detached build still owes its lock back.
DEFAULT_STATE_DIR = "context/.graph"
# How old a published graph may be before the one-line form starts a rebuild behind it.
# Overridden by `thresholds.line_max_age_seconds`; a default is needed because the line
# also runs against a config that predates the key.
DEFAULT_LINE_MAX_AGE = 60
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


def config_fingerprint(config: dict, root: str, config_path: str | None) -> str:
    """A hash of the files that decide what the published counts mean.

    The cache is keyed by the content of each knowledge file, which answers "did this
    file change" and nothing else. Which files are looked at in the first place, where an
    entity begins, what exempts a subtree from being called an orphan — all of that comes
    from the config, and from the linter's config behind it. Change either and every
    number in the published graph can move without one knowledge file having changed, so
    a state written before the edit would keep being served as current.

    The bytes of the two files are hashed rather than the settings parsed out of them:
    this is read on the render path, where a YAML parse per draw is a cost nobody agreed
    to, and where the parse would also have to report a config it could not read. Hashing
    bytes is stricter than it needs to be — a reworded comment invalidates too — and that
    errs the right way: the line still prints instantly, it just rebuilds behind itself.
    """
    digest = hashlib.sha256()
    lint_config = config.get("lint_config")
    for path in (config_path,
                 os.path.join(root, lint_config) if lint_config else None):
        if path is None:
            digest.update(b"\0none")
            continue
        try:
            with open(path, "rb") as fh:
                digest.update(fh.read())
        except OSError:
            digest.update(b"\0unreadable")
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def load_config(path: str) -> dict | None:
    """The config, or None when there is none. `ConfigError` when there is one and it is broken.

    None is not an error: this tool ships inside a template that lands in repositories
    which have not configured it, and a status line is global. No config means no graph
    means say nothing.

    A file that exists and does not parse is the opposite case, and used to come back as
    the same `None` — so a typo in the YAML read as "this repository has no graph", the
    run ended 0, and the graph stayed as stale as it was, silently. Absence is answered
    by the path not existing; anything else is reported.
    """
    if not os.path.exists(path):
        return None
    yaml = _yaml()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            config = yaml.safe_load(fh)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        # `UnicodeError` alongside the other two: the file is opened as UTF-8, so a config
        # saved in a legacy codepage raises `UnicodeDecodeError`, which is neither an
        # `OSError` nor a YAML error. Uncaught it left a traceback where the documented
        # answer is one line naming the config — and in a status line, on a render path.
        raise ConfigError(f"{path} could not be read ({exc.__class__.__name__})") from exc
    if not isinstance(config, dict):
        raise ConfigError(f"{path} does not hold a mapping of settings")
    return config


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
    try:
        lint = load_config(os.path.join(root, lint_config))
    except ConfigError:
        lint = None
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
    """Every visible `.md` file under the given roots, as the working tree has it now.

    Visible means: nothing whose own name, or the name of a directory it sits in below a
    root, starts with a dot. That is not a stray optimisation — this tool's own state
    directory lives under a root by default, and a hidden directory under a knowledge
    root is where tooling keeps its workings, not where the knowledge is. Excluding the
    state directory by name instead would need the config down here and would still walk
    every other hidden tree. A root may itself be hidden and is walked: it was named on
    purpose, which is the difference.

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

    Material put down is scanned and carries no edges, which is one set doing two jobs
    rather than an inconsistency. A link inside an archived file still has to be *looked
    at*: that is the set the repair tool scans, and the two dead-link counts are printed
    side by side. But it must not make its target reachable, because a file nobody reads
    by default is not a way anybody gets anywhere. Left as an edge it would quietly
    answer "something points here" for a file whose only mention is in last quarter's raw
    export — precisely the orphan the check exists to surface.

    The line is `put_down`, not `sealed`: an archived task and an aged-out status row are
    unread by exactly the same rule as a sealed directory, and drawing the line at sealed
    alone let them go on making their targets reachable.
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
        reaches = not put_down(relink, rel)
        for line, target, path, readings in record["targets"]:
            hit = next((r for r in readings if exists(r)), None)
            if hit is None:
                dead_candidates.append((rel, line, target, path, readings))
                continue
            if reaches and hit.endswith(".md") and hit in node_set and hit != rel:
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
                     "reported": reportable(relink, rel, tracked),
                     "cause": silenced_because(relink, rel, tracked)})
    for edges in out.values():
        edges.sort()
    for edges in inbound.values():
        edges.sort()
    dead.sort(key=lambda d: (d["path"], d["line"], d["target"]))
    return {"out": out, "in": inbound, "dead": dead}


def put_down(relink, rel: str) -> bool:
    """Material that records what happened as it happened, and is not read by default.

    Sealed directories and closed history — archived tasks, aged-out status rows — are one
    category, not two: both are written once and then left, and the reading rules keep an
    agent out of both. One predicate, because the two places that ask are asking the same
    question. `resolve` asks it to decide whether a file's links are a way of reaching
    anything, and `reportable` to decide whether a dead link in it is worth telling anybody
    about. Splitting it is what let an archived task keep a `data/` file out of the orphan
    list while the same file's only other mention was in a sealed export.
    """
    return relink.sealed(rel) or relink.unread_by_default(rel)


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
    return not (put_down(relink, rel) or rel not in tracked)


def silenced_because(relink, rel: str, tracked: set) -> str | None:
    """Which of `reportable`'s two reasons kept this dead link quiet, or None if neither.

    The two are not interchangeable and must not be counted as one. "Material put down"
    is a statement about the knowledge base — a record of the day it was written, and
    nothing to repair. "Not tracked yet" is a statement about one file on one station:
    the link may well be a fault, it is simply invisible to the check whose count this
    one has to match, and it stops being invisible at `git add`. Reporting the sum under
    the first name would let a scratch file nobody has committed inflate a figure that
    reads as closed history.
    """
    if put_down(relink, rel):
        return "put-down"
    if rel not in tracked:
        return "untracked"
    return None


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
        # `isfile`, not `exists`: the linter looks the marker up among a directory's file
        # names, so a *directory* that happens to carry the marker's name would exempt a
        # whole subtree here and nothing there — one rule, two answers.
        if os.path.isfile(os.path.join(root, directory, marker)):
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

def lock_path(state_dir: str, name: str = LOCK_NAME) -> str:
    return os.path.join(state_dir, name)


def new_token() -> str:
    """The identity of one build, written into the lock file it holds."""
    return f"{os.getpid()}-{time.time_ns()}"


def take_lock(state_dir: str, stale_after: float, name: str = LOCK_NAME) -> str | None:
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
    path = lock_path(state_dir, name)
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
        return take_lock(state_dir, stale_after, name)
    except OSError:
        return None


def drop_lock(state_dir: str, token: str, name: str = LOCK_NAME) -> None:
    """Give the lock back, but only while it is still the one this build took.

    Reading the token and then unlinking are two operations, and a stale reclaim can land
    between them: the read says "mine", the reclaim replaces the file, and the unlink
    then removes a lock belonging to the build that took over — leaving the way open for
    a third. So the file is moved out of the way first, under a name carrying this
    build's own token. A rename is atomic and exactly one caller can move any one file,
    so from that point on nothing else can be removed by accident. Ours: it is gone,
    which is what releasing means. Not ours: the claim goes back with `O_EXCL`, which
    cannot overwrite whoever holds the lock by then.
    """
    path = lock_path(state_dir, name)
    claimed = f"{path}.{token}"
    try:
        os.rename(path, claimed)
    except OSError:
        return               # no lock to give back, or somebody moved it first
    try:
        with open(claimed, "r", encoding="ascii", errors="replace") as fh:
            held = fh.read().strip()
    except OSError:
        held = token         # unreadable: treat as ours and let it go
    try:
        os.unlink(claimed)
    except OSError:
        pass
    if held == token:
        return
    try:                     # reclaimed as stale and handed on while we were letting go
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, held.encode("ascii", errors="replace"))
        os.close(fd)
    except OSError:
        pass                 # a third build holds it now, which is claim enough


def publish(state_dir: str, started: float, payload: dict) -> None:
    """Write the graph, unless a build that started later has already written its own.

    Builds are deliberately not serialised: an ordinary `orphans` or `map` runs while a
    detached build is going, because making one wait for the other would trade three
    duplicated seconds for a stalled command. What must not follow from that is the
    slower of the two finishing last and laying its older view of the tree over the
    newer one — the file would then describe a tree that no longer exists, and go on
    doing so until something rebuilt it. Every published graph carries the moment its
    build started, and a publish yields to one that started later.

    Reading that stamp and writing over it are two operations, so the comparison alone
    settles nothing: both builds can read the same older file, the newer one can write,
    and the older one can then pass a test it has already invalidated and overwrite the
    newer — precisely the outcome the stamp exists to prevent, only in a narrower window.
    So the pair is taken under a claim of its own. It is not the build lock: a `map`, a
    `report` and the linter all build without ever claiming that one, and they publish
    too. It is short-lived by construction, one JSON write over a payload already in
    memory, which is why waiting for it is bounded in seconds rather than in builds.

    Failing to claim it means giving the publish up, not doing it unclaimed. The answer
    is unaffected either way — the numbers are already computed and already on their way
    to whoever asked; only the file is skipped. And the claim can only be unavailable
    because somebody else is mid-publish, so the file does get written, by them. Writing
    it here anyway would run the read-and-write pair with nothing ordering it against
    theirs, which is the stale overwrite this claim exists to prevent, reintroduced in
    the one case it was taken out for.

    The hash cache needs no such guard. It is keyed by content, so an entry written by
    either build is valid for exactly the file it came from; the worst an overwrite costs
    there is re-extracting a file whose entry went missing.
    """
    path = os.path.join(state_dir, "graph.json")
    deadline = time.time() + PUBLISH_WAIT
    token = take_lock(state_dir, PUBLISH_STALE, PUBLISH_LOCK_NAME)
    while token is None and time.time() < deadline:
        time.sleep(0.05)
        token = take_lock(state_dir, PUBLISH_STALE, PUBLISH_LOCK_NAME)
    if token is None:
        return               # somebody else holds it and is writing; theirs stands
    try:
        previous = read_graph(path)
        if previous is not None and previous.get("started_at", 0) > started:
            return
        write_json(path, payload, sort_keys=True, indent=1)
    finally:
        drop_lock(state_dir, token, PUBLISH_LOCK_NAME)


# --- assembling the answer ---------------------------------------------------

def build(config: dict, root: str, rebuild: bool = False,
          persist: bool = True, config_path: str | None = None) -> dict | None:
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
    # Everything whose links are read at all. The nodes are folded in because git's
    # spelling of a path and the walk's are not always the same byte for byte — measured
    # on a directory with a non-ASCII name — and a node missing here would take its own
    # links with it. Material put down is in this set and carries no edges: `resolve`
    # draws that line, because the two questions want different answers from one scan.
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
        "dead_put_down": sum(1 for d in graph["dead"] if d["cause"] == "put-down"),
        "dead_untracked": sum(1 for d in graph["dead"] if d["cause"] == "untracked"),
        "recomputed": state["recomputed"],
    }
    if not persist:
        return result
    publish(state_dir, started, {
        "version": CACHE_VERSION,
        "config": config_fingerprint(config, root, config_path),
        "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
        # Unrounded, unlike `seconds` below: this one is compared, not read. Rounded to
        # milliseconds it stopped being a total order — two builds a fraction of a
        # millisecond apart could land on the same stamp, and the older one would then
        # pass the yield test it had just failed and write over the newer result.
        "started_at": started,
        "seconds": round(elapsed, 3),
        "counts": {"nodes": len(nodes), "sources": len(sources), "edges": edges,
                   "orphans": len(reported), "waived": waived,
                   "dead": len(result["dead"])},
        "out": {k: [{"to": t, "line": ln, "type": ty} for t, ln, ty in v]
                for k, v in sorted(graph["out"].items())},
        "dead": result["dead"],
        "orphan_paths": reported,
    })
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


def line_from_counts(counts: dict, note: str) -> str:
    return (f"context: {counts['nodes']} nodes · {counts['edges']} edges · "
            f"{counts['orphans']} orphans · {counts['dead']} dead links · {note}")


def age_label(seconds: float) -> str:
    """How long ago these counts were published — the one thing the line can prove.

    `fresh` is a claim about the tree: it says the numbers were checked against the files
    and match. Only a command that walked the files may make it, and the one-line form
    deliberately walks nothing — it reads what the last build left behind. Labelling that
    `fresh` was wrong in exactly the way that is hardest to notice: the line kept saying
    the base was clean while a link added a minute ago was already dead, and looked no
    different from the line that had checked.

    An age says less and is always true. It also tells the reader the one thing they need
    in order to know whether to trust it, which `fresh` never did. Coarse on purpose —
    this is drawn beside a prompt, and a second of precision after the first minute is
    width spent on nothing.
    """
    if seconds < 60:
        return f"published {int(seconds)}s ago"
    if seconds < 3600:
        return f"published {int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"published {int(seconds // 3600)}h ago"
    return f"published {int(seconds // 86400)}d ago"


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
          f"{state['dead_put_down']}")
    print(f"  dead links in files git does not track yet (not reported): "
          f"{state['dead_untracked']}")
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
        # The two silenced counts are named apart here for the same reason `stats` names
        # them apart: subtracting the reported ones from the total lumps a dead link in a
        # file nobody has committed yet together with closed history, and reads it out
        # under the label that says there is nothing to repair.
        f"| dead links in material put down | {state['dead_put_down']} |",
        f"| dead links in files git does not track yet | {state['dead_untracked']} |",
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

    The child is handed the parent's token **and the directory that token was taken in**,
    and releases the lock on every way out of its own process, not only on the way out
    through a build that worked: ownership travels with the build, and a build that cannot
    even read the config still owes the lock back. The directory has to travel with it for
    exactly those ways out — the config is where `state_dir` is written, so on the path
    where the config is what could not be read, the child has nothing left to derive it
    from and used to fall back to the default. In a repository that configures a different
    one that released nothing, and left the real claim standing until it went stale.
    If the spawn fails the lock is dropped here and now — otherwise every later call would
    wait out the full budget while nothing at all was building.
    """
    # Absolute before it travels: the caller may have written a path relative to the
    # directory it was invoked from, and the child starts in the repository root. The
    # same text would then name a file that is not there, and the build would end
    # without a graph, having burned the lock for nothing.
    config_path = os.path.abspath(config_path)
    flags = {}
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP — no console, no signal inheritance
        flags["creationflags"] = 0x00000008 | 0x00000200
    else:
        flags["start_new_session"] = True
    try:
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "report",
             "--release-lock", token, "--lock-dir", os.path.abspath(state_dir),
             "--config", config_path, "--root", root],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, cwd=root, **flags)
    except OSError:
        drop_lock(state_dir, token)


def released_in(lock_dir: str | None, root_arg: str | None,
                config: dict | None = None, root: str | None = None) -> str:
    """Where a lock handed to this process is to be given back.

    The directory the claim was taken in, when the spawn passed it — that is a fact about
    a file that already exists, and it is right on every way out, including the two that
    are ways out of reading the config. Only a process nobody handed one falls back to
    working it out, and then a config that did load beats the default.
    """
    if lock_dir:
        return lock_dir
    base = root if root is not None else repo_root(root_arg)
    return os.path.join(base, (config or {}).get("state_dir", DEFAULT_STATE_DIR))


def state_file(config: dict, root: str, name: str) -> str:
    return os.path.join(root, config.get("state_dir", DEFAULT_STATE_DIR), name)


def is_cold(config: dict, root: str, config_path: str | None = None) -> bool:
    """Whether answering would mean a full build rather than a cheap refresh.

    Not merely "is there a cache file". A cache written by an older version, or by a
    different `relink.py`, is discarded on read — so it exists and is worth nothing, and
    the counts standing next to it describe a definition of a link that no longer holds.
    The one-line form uses this to decide whether what it found on disk is worth standing
    behind at all: a graph published a moment ago over a cache since invalidated is
    recent and wrong, and recency alone would let it through. A missing `graph.json`
    counts too: it is what the waiting call reads its numbers from.

    The config is the third way the same thing happens. Widen `scan_roots`, rename the
    self-index marker, exclude one directory more in the linter's config, and every count
    in the published graph can move while every knowledge file stands still — nothing in
    a content-keyed cache would notice. So the fingerprint of the configuration is
    compared as well, and a graph built under a different one is treated as cold.
    """
    published = read_graph(state_file(config, root, "graph.json"))
    if published is None:
        return True
    if published.get("config") != config_fingerprint(config, root, config_path):
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


def await_graph(path: str, budget: float, state_dir: str) -> dict | None:
    """Wait for a detached build to publish its counts, up to the budget.

    Waiting on `graph.json` rather than on the cache is what keeps the first call from
    doing the work twice: the counts are already in that file, so there is nothing left
    to compute once it appears. Only ever called when there was nothing to read — an
    artefact already on disk is never waited for, and never called fresh.

    There are two ways to stop early, and the second one matters as much as the first:
    the counts appear, or the lock is gone. The build gives its lock back on every way
    out of its process, so a lock that has vanished with nothing published says the build
    is over and failed — a `relink.py` that would not load, a spawn that never started.
    Sleeping out the rest of the budget for it would turn one failed build into a stall
    on every call until the stale threshold. The file is read before the lock is checked,
    because publishing happens before releasing: nothing can be missed that way round.
    """
    deadline = time.time() + budget
    while True:
        blob = read_graph(path)
        if blob is not None:
            return blob
        if not os.path.exists(lock_path(state_dir)):
            return None      # nobody is building any more, and nothing was published
        if time.time() >= deadline:
            return None
        time.sleep(0.2)


def published_age(path: str) -> float | None:
    """How long ago the graph on disk was published, or None when there is none."""
    try:
        return max(0.0, time.time() - os.path.getmtime(path))
    except OSError:
        return None


def line_answer(config: dict, root: str, config_path: str) -> None:
    """Print the one-line answer — without ever building in the process that prints it.

    This is the one form on a render path. A status line is drawn every turn, and the
    refresh behind the other commands walks and hashes the whole base whether or not
    anything changed: measured at around three seconds on eight thousand files, which is
    three seconds of every render, and unbounded besides — the budget used to apply only
    when there was no cache at all, so exactly the case the documentation promised was
    covered was the one that was not. So the one-line form reads what the last build
    published and hands any rebuilding to a detached process. Every other command still
    refreshes before it answers, because none of them is drawn on a timer.

    `line_max_age_seconds` is the whole of the trade. Inside that window the published
    counts are the answer and nothing is started; outside it the counts still go out at
    once, labelled as being rebuilt, and the build runs for whoever asks next. So the
    line is never older than one window plus one build, and never costs more than a file
    read.

    The budget is what the repository declares it will tolerate when there is nothing to
    print at all: under it, a first call gets real numbers; over it, the line says the
    graph is not there yet. Either way nobody waits longer than the number in the config.

    Cold does not always mean empty. A cache discarded for its version, its `relink.py`
    fingerprint or the configuration it was built under leaves the last build's
    `graph.json` behind, and those counts are worth printing — they are simply known to
    be out of date, which is why a rebuild was just launched. They go out at once,
    labelled as being rebuilt, and the next call reads the new ones.

    Nothing on this path is ever labelled `fresh`, including the branch inside the age
    window. That word says the numbers were checked against the files, and this form
    checks nothing by design: a link written seconds after a build is already missing
    from counts that are seconds old. What the line can prove is when they were
    published, so that is what it says — see `age_label`.
    """
    state_dir = os.path.join(root, config.get("state_dir", DEFAULT_STATE_DIR))
    thresholds = config.get("thresholds") or {}
    budget = float(thresholds.get("build_seconds") or 0)
    # `is None`, not truthiness: `line_max_age_seconds: 0` is a repository saying "never
    # serve a published build without starting a rebuild behind it", and reading that as
    # "unset" would answer it with the default minute — the opposite instruction.
    declared = thresholds.get("line_max_age_seconds")
    max_age = float(DEFAULT_LINE_MAX_AGE if declared is None else declared)
    path = state_file(config, root, "graph.json")
    published = read_graph(path)          # read before the rebuild can replace it
    age = published_age(path)
    if (published is not None and age is not None and age <= max_age
            and not is_cold(config, root, config_path)):
        print(line_from_counts(published["counts"], age_label(age)))
        return
    token = take_lock(state_dir, stale_after=max(budget * 3, 60))
    if token:
        start_background_build(config_path, root, state_dir, token)
    if published is not None:
        print(line_from_counts(published["counts"], "rebuilding"))
        return
    blob = await_graph(path, budget, state_dir)
    if blob is None:
        print(ABSENT_LINE)
        return
    print(line_from_counts(blob["counts"], age_label(published_age(path) or 0.0)))


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
    common.add_argument("--lock-dir", metavar="DIR", default=argparse.SUPPRESS,
                        help=argparse.SUPPRESS)   # internal: where that token was taken
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
    # The directory the token was taken in, sent along with it. Not derived from the
    # config here, because two of the three ways out are ways out of reading the config.
    lock_dir = getattr(args, "lock_dir", None)
    try:
        config = load_config(config_path)
    except ConfigError as exc:
        # A config that exists and is broken is not the same as no config, and must not
        # be answered with the silence owed to a repository that never configured one:
        # the pre-commit block reads the exit code, and 0 here would leave a stale graph
        # behind without a word.
        sys.stderr.write(f"context-graph: {exc}\n")
        if token:
            drop_lock(released_in(lock_dir, getattr(args, "root", None)), token)
        return 2
    if config is None:
        if token:
            drop_lock(released_in(lock_dir, getattr(args, "root", None)), token)
        return 0            # no config: this repository has no graph, so say nothing
    root = repo_root(getattr(args, "root", None))
    try:
        # Before building, because the point is not to build here at all: only the
        # one-line form is on a render path, so only it refuses to wait — cold or warm,
        # since a warm refresh costs the same walk over every file and used to run
        # synchronously past the very budget that exists to stop it.
        if (args.command == "stats" and getattr(args, "line", False)
                and not getattr(args, "rebuild", False)):
            line_answer(config, root, config_path)
            return 0
        state = build(config, root, rebuild=getattr(args, "rebuild", False),
                      config_path=config_path)
        if state is None:
            # relink.py would not load, so there is no definition of a link to build
            # against. Not the state of the knowledge base — a broken installation, and
            # the same case as a config that will not parse: the pre-commit block reads
            # the exit code and silences stderr, so 0 here left a stale graph behind
            # without a word, which is precisely what the graph is meant to prevent.
            return 2
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
            # `lock_dir` first even here, where the config did load: it says where the
            # claim was actually taken, while the config only says where a claim taken
            # now would go. Those differ if the config was edited after the spawn, and
            # then the lock that exists is the one this process does not release.
            drop_lock(released_in(lock_dir, getattr(args, "root", None), config, root),
                      token)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
