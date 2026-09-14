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

# Console encoding: the session line carries a separator and the report carries Polish
# prose, and a Windows console defaults to cp1250. Replace rather than raise — a mangled
# character is a cosmetic problem, a traceback in a status line is not.
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
CACHE_VERSION = 1


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
    if lint.get("self_index_marker"):
        rules["self_index_marker"] = lint["self_index_marker"]
    if lint.get("catalog_exclude_dirs"):
        rules["exclude_dirs"] = list(lint["catalog_exclude_dirs"])
    scopes = [r["path"] for r in lint.get("scan_roots", []) if r.get("path")]
    if scopes:
        rules["entity_scopes"] = scopes
    return rules


# --- nodes -------------------------------------------------------------------

def list_nodes(relink, root: str, scan_roots: list[str]) -> list[str]:
    """Tracked Markdown inside the configured scopes.

    Tracked, not walked: an untracked file is somebody's work in progress, and the same
    choice relink makes keeps the two tools answering about the same universe of files.
    """
    return sorted(rel for rel in relink.tracked_files(root)
                  if rel.endswith(".md") and relink.within(rel, scan_roots))


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


def refresh(relink, root: str, nodes: list[str], state_dir: str, rebuild: bool,
            persist: bool = True) -> dict:
    """Re-extract only the files whose content changed since the last run.

    `persist=False` still reads the cache — it is just not allowed to update it.
    The linter asks in that mode: its whole value rests on being read-only, and a
    check that wrote something on the way to its answer would make a clean run mean
    two different things.
    """
    cache_path = os.path.join(state_dir, "cache.json")
    cached = {}
    if not rebuild:
        try:
            with open(cache_path, "r", encoding="utf-8") as fh:
                blob = json.load(fh)
            if blob.get("version") == CACHE_VERSION:
                cached = blob.get("files", {})
        except (OSError, ValueError):
            cached = {}
    files, recomputed = {}, 0
    for rel in nodes:
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
        write_json(cache_path, {"version": CACHE_VERSION, "files": files},
                   sort_keys=True, separators=(",", ":"))
    return {"files": files, "recomputed": recomputed}


# --- resolution --------------------------------------------------------------

def resolve(relink, root: str, nodes: list[str], files: dict) -> dict:
    """Turn cached targets into edges and dead links against the tree as it is now."""
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
    for rel in nodes:
        record = files.get(rel)
        if not record:
            continue
        for line, target, path, readings in record["targets"]:
            hit = next((r for r in readings if exists(r)), None)
            if hit is None:
                dead_candidates.append((rel, line, target, path, readings))
                continue
            if hit.endswith(".md") and hit in node_set and hit != rel:
                out.setdefault(rel, []).append((hit, line))
                inbound.setdefault(hit, []).append((rel, line))
    # A target git is told to ignore exists on the station that produced it and nowhere
    # else, so its absence here does not make the link dead. relink asks git the same
    # question; asking it the same way is what keeps the two counts equal.
    asked = {r + ("/" if c[3].endswith("/") else "")
             for c in dead_candidates for r in c[4]}
    skip = {p.rstrip("/") for p in relink.ignored(root, asked)}
    built = relink.generated(root, nodes)
    dead = []
    for rel, line, target, _path, readings in dead_candidates:
        if any(r in skip for r in readings):
            continue
        dead.append({"path": rel, "line": line, "target": target,
                     "reported": reportable(relink, rel, built)})
    for edges in out.values():
        edges.sort()
    for edges in inbound.values():
        edges.sort()
    dead.sort(key=lambda d: (d["path"], d["line"], d["target"]))
    return {"out": out, "in": inbound, "dead": dead}


def reportable(relink, rel: str, built: set) -> bool:
    """Whether a dead link in this file is worth telling anybody about.

    Material that records what happened as it happened — sealed directories, archived
    tasks, aged-out status rows — describes the state of the day it was put down, so a
    link that has since died is an accurate record, not a fault. Build output is written
    from a source, over any repair, so the source is what gets checked. relink draws
    these lines already; this reuses them rather than redrawing them.
    """
    return not (relink.sealed(rel) or relink.unread_by_default(rel)
                or relink.is_template(rel) or rel in built)


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


# --- assembling the answer ---------------------------------------------------

def build(config: dict, root: str, rebuild: bool = False,
          persist: bool = True) -> dict | None:
    relink = load_relink()
    if relink is None:
        sys.stderr.write(f"context-graph: relink.py could not be loaded ({RELINK_PATH})\n")
        return None
    rules = resolve_rules(config, root)
    state_dir = os.path.join(root, config.get("state_dir", "context/.graph"))
    started = time.time()
    nodes = list_nodes(relink, root, config.get("scan_roots", []))
    state = refresh(relink, root, nodes, state_dir, rebuild, persist)
    graph = resolve(relink, root, nodes, state["files"])
    elapsed = time.time() - started
    reported, waived = orphans(root, nodes, graph, rules)
    edges = sum(len(v) for v in graph["out"].values())
    budget = (config.get("thresholds") or {}).get("build_seconds")
    if budget and elapsed > budget:
        # Load-bearing, not decorative: crossing it means the base outgrew the number in
        # the config, and the session line is about to become something that is waited on.
        sys.stderr.write(
            f"context-graph: budowa zajela {elapsed:.1f}s przy progu {budget}s — "
            "podnies thresholds.build_seconds albo zawez scan_roots\n")
    result = {
        "root": root, "state_dir": state_dir, "rules": rules,
        "nodes": nodes, "graph": graph,
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
            "counts": {"nodes": len(nodes), "edges": edges,
                       "orphans": len(reported), "waived": waived,
                       "dead": len(result["dead"])},
            "out": {k: v for k, v in sorted(graph["out"].items())},
            "dead": result["dead"],
            "orphan_paths": reported,
        }, sort_keys=True, indent=1)
    return result


# --- commands ----------------------------------------------------------------

def cmd_links(state: dict, target: str) -> int:
    root = state["root"]
    rel = os.path.relpath(os.path.abspath(target), root).replace(os.sep, "/")
    if rel not in set(state["nodes"]):
        print(f"`{rel}` nie nalezy do grafu — graf obejmuje pliki .md w skonfigurowanych "
              f"zakresach ({', '.join(state['rules']['entity_scopes']) or 'brak'}).")
        return 0
    inbound = state["graph"]["in"].get(rel, [])
    outbound = state["graph"]["out"].get(rel, [])
    print(f"{rel}")
    print(f"  wchodzace: {len(inbound)}")
    for src, line in inbound:
        print(f"    <- {src}:{line}")
    print(f"  wychodzace: {len(outbound)}")
    for dst, line in outbound:
        print(f"    -> {dst} (linia {line})")
    return 0


def cmd_orphans(state: dict) -> int:
    reported = state["orphans"]
    print(f"sieroty: {len(reported)}   zwolnione regula: {state['waived']}")
    for rel in reported:
        print(f"  {rel}")
    return 0


def bridge_pairs(state: dict) -> list:
    scopes = state["rules"]["entity_scopes"]
    pairs: dict[tuple[str, str], int] = {}
    for src, edges in state["graph"]["out"].items():
        a = entity_of(src, scopes)
        if not a:
            continue
        for dst, _line in edges:
            b = entity_of(dst, scopes)
            if not b or b[0] == a[0]:
                continue
            key = tuple(sorted((a[0], b[0])))
            pairs[key] = pairs.get(key, 0) + 1
    return sorted(pairs.items(), key=lambda kv: (-kv[1], kv[0]))


def cmd_bridges(state: dict, limit: int) -> int:
    pairs = bridge_pairs(state)
    total = sum(n for _, n in pairs)
    print(f"krawedzie miedzy encjami: {total} w {len(pairs)} parach")
    for (a, b), count in pairs[:limit]:
        print(f"  {count:5d}  {a} <-> {b}")
    if len(pairs) > limit:
        print(f"  ... i {len(pairs) - limit} dalszych par")
    return 0


def cmd_map(state: dict, limit: int) -> int:
    scopes = state["rules"]["entity_scopes"]
    print(f"wezly: {len(state['nodes'])}   krawedzie: {state['edges']}   "
          f"sieroty: {len(state['orphans'])} (zwolnione: {state['waived']})   "
          f"martwe odnosniki: {len(state['dead'])}")
    hubs = sorted(((len(v), k) for k, v in state["graph"]["in"].items()), reverse=True)
    print(f"\nnajwieksze huby (wchodzace):")
    for count, rel in hubs[:limit]:
        print(f"  {count:5d}  {rel}")
    linked = set()
    for (a, b), _ in bridge_pairs(state):
        linked.update((a, b))
    entities = sorted({e[0] for rel in state["nodes"] if (e := entity_of(rel, scopes))})
    alone = [e for e in entities if e not in linked]
    print(f"\nencje bez powiazan na zewnatrz: {len(alone)} z {len(entities)}")
    for name in alone:
        print(f"  {name}")
    return 0


ABSENT_LINE = "context: grafu jeszcze nie ma — buduje sie w tle"


def start_background_build(config_path: str, root: str) -> None:
    """Build the graph in a process nobody waits for.

    A status line renders on every turn, so a first build over thousands of files must
    never be something a prompt waits behind. Detached, the cost is paid once and the
    next render answers from the cache — which is why this says "not yet" rather than
    "none": the next call will have it.
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
             "--config", config_path, "--root", root],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, cwd=root, **flags)
    except OSError:
        pass            # a status line that cannot spawn simply stays quiet next turn


def cache_file(config: dict, root: str) -> str:
    return os.path.join(root, config.get("state_dir", "context/.graph"), "cache.json")


def is_cold(config: dict, root: str) -> bool:
    return not os.path.exists(cache_file(config, root))


def await_build(config: dict, root: str, budget: float) -> bool:
    """Wait for a detached build to land, up to the budget. True when it did.

    The budget is what the repository declares it will tolerate on a first build, and it
    is the whole answer to "build it or say it is missing": under it, the caller gets real
    numbers on this very call; over it, the line says the graph is not there yet and the
    build keeps going for the next one. Either way nobody waits longer than the number in
    the config.
    """
    path = cache_file(config, root)
    deadline = time.time() + budget
    while time.time() < deadline:
        if os.path.exists(path):
            return True
        time.sleep(0.2)
    return os.path.exists(path)


def stats_line(state: dict) -> str:
    fresh = "swiezy" if state["recomputed"] == 0 else f"przeliczono {state['recomputed']}"
    return (f"context: {len(state['nodes'])} wezly · {state['edges']} krawedzi · "
            f"{len(state['orphans'])} sierot · {len(state['dead'])} martwych linkow "
            f"· {fresh}")


def cmd_stats(state: dict, one_line: bool) -> int:
    if one_line:
        print(stats_line(state))
        return 0
    print(stats_line(state))
    print(f"  zwolnione regula poddrzewa: {state['waived']}")
    print(f"  martwe odnosniki w materiale odlozonym (nieraportowane): "
          f"{len(state['dead_all']) - len(state['dead'])}")
    print(f"  budowa: {state['seconds']:.2f}s, przeliczonych plikow: {state['recomputed']}")
    return 0


def cmd_report(state: dict, limit: int) -> int:
    path = os.path.join(state["state_dir"], "report.md")
    lines = [
        "# Graf powiazan bazy wiedzy",
        "",
        f"Zbudowany {time.strftime('%Y-%m-%d %H:%M')} w {state['seconds']:.2f}s. "
        "Plik jest generowany i nieusuwalnie lokalny — nie commituje sie go.",
        "",
        "| Miara | Wartosc |",
        "|---|---|",
        f"| wezly (pliki .md w zakresach) | {len(state['nodes'])} |",
        f"| krawedzie (odnosniki .md -> .md) | {state['edges']} |",
        f"| martwe odnosniki (pliki czytane domyslnie) | {len(state['dead'])} |",
        f"| martwe odnosniki w materiale odlozonym | {len(state['dead_all']) - len(state['dead'])} |",
        f"| sieroty | {len(state['orphans'])} |",
        f"| zwolnione regula poddrzewa | {state['waived']} |",
        "",
        "## Martwe odnosniki",
        "",
    ]
    if state["dead"]:
        for item in state["dead"]:
            lines.append(f"- `{item['path']}:{item['line']}` -> `{item['target']}`")
    else:
        lines.append("Brak.")
    lines += ["", "## Najwieksze huby", ""]
    hubs = sorted(((len(v), k) for k, v in state["graph"]["in"].items()), reverse=True)
    for count, rel in hubs[:limit]:
        lines.append(f"- {count} wejsc — `{rel}`")
    lines += ["", "## Mosty miedzy encjami", ""]
    for (a, b), count in bridge_pairs(state)[:limit]:
        lines.append(f"- {count} — {a} <-> {b}")
    lines += ["", "## Sieroty", ""]
    if state["orphans"]:
        for rel in state["orphans"][:limit]:
            lines.append(f"- `{rel}`")
        if len(state["orphans"]) > limit:
            lines.append(f"- ... i {len(state['orphans']) - limit} dalszych")
    else:
        lines.append("Brak.")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"raport: {os.path.relpath(path, state['root']).replace(os.sep, '/')}")
    return 0


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
                        help="sciezka do config.yaml (domyslnie: obok skryptu)")
    common.add_argument("--root", default=argparse.SUPPRESS,
                        help="korzen repozytorium (domyslnie: git rev-parse)")
    common.add_argument("--rebuild", action="store_true", default=argparse.SUPPRESS,
                        help="policz wszystko od nowa, z pominieciem cache'u")
    common.add_argument("--limit", type=int, default=argparse.SUPPRESS,
                        help="ile pozycji wypisac na liste (domyslnie 15)")
    parser = argparse.ArgumentParser(
        prog="context-graph", parents=[common],
        description="Graf jawnych odnosnikow miedzy plikami bazy wiedzy.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_links = sub.add_parser("links", parents=[common],
                             help="odnosniki wchodzace i wychodzace pliku")
    p_links.add_argument("file")
    sub.add_parser("orphans", parents=[common],
                   help="pliki bez ani jednego odnosnika wchodzacego")
    sub.add_parser("bridges", parents=[common],
                   help="krawedzie miedzy encjami, po parach encji")
    sub.add_parser("map", parents=[common],
                   help="rozklad wielkosci: wezly, krawedzie, huby, encje")
    p_stats = sub.add_parser("stats", parents=[common], help="stan grafu")
    p_stats.add_argument("--line", action="store_true",
                         help="jedno zdanie, do linii statusu i hooka sesji")
    sub.add_parser("report", parents=[common],
                   help="zapisz raport markdown w katalogu stanu")
    args = parser.parse_args(argv)
    config_path = getattr(args, "config", DEFAULT_CONFIG)
    limit = getattr(args, "limit", 15)

    config = load_config(config_path)
    if config is None:
        return 0            # no config: this repository has no graph, so say nothing
    root = repo_root(getattr(args, "root", None))
    # Checked before building, because the point is not to build now: only the one-line
    # form is on a render path, so only it refuses to wait.
    if (args.command == "stats" and getattr(args, "line", False)
            and not getattr(args, "rebuild", False) and is_cold(config, root)):
        start_background_build(config_path, root)
        budget = (config.get("thresholds") or {}).get("build_seconds") or 0
        if not await_build(config, root, float(budget)):
            print(ABSENT_LINE)
            return 0          # still building; the next call answers from the cache
    state = build(config, root, rebuild=getattr(args, "rebuild", False))
    if state is None:
        return 0
    if args.command == "links":
        return cmd_links(state, args.file)
    if args.command == "orphans":
        return cmd_orphans(state)
    if args.command == "bridges":
        return cmd_bridges(state, limit)
    if args.command == "map":
        return cmd_map(state, limit)
    if args.command == "stats":
        return cmd_stats(state, args.line)
    if args.command == "report":
        return cmd_report(state, limit)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
