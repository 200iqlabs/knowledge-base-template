#!/usr/bin/env python3
"""context-lint — deterministic consistency checks over the context/ knowledge base.

Read-only. Never modifies files. Same repo state -> same output.
Facts about files only; semantic judgement belongs to the agent (close-session).

Usage:
    python <path-to>/lint.py [PATH] [--json] [--config CONFIG] [--root ROOT]

    PATH     Optional. Limit scan to a subtree (e.g. a single client folder).
             Default: whole repo (all scan_roots from config).
    --json   Emit findings as JSON instead of the tab-separated text format.
    --config Path to config.yaml. Default: the one next to this script.
    --root   Repository root that scan_roots are resolved against.
             Default: `git rev-parse --show-toplevel`, else the working directory.

Exit code: 0 when no ERROR findings (WARN allowed), 1 when >=1 ERROR.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import fnmatch
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, asdict

# --- dependency guard: fail readable, not with a traceback -------------------
try:
    import yaml
except ImportError:
    sys.stderr.write(
        "context-lint: missing dependency PyYAML.\n"
        "  Install with:  pip install pyyaml\n"
    )
    sys.exit(2)

# Resolved in main(). Deliberately NOT derived from this file's own location: the
# script lives in a template directory that is itself a subtree of the consuming
# repo, so counting parent directories would silently scan the template instead of
# the repo. The repo asks git which root it is in.
REPO_ROOT = ""
DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")

# How many entities the last run actually visited. Reported alongside the findings so a
# clean result can be told apart from a scan that reached nothing.
SCANNED = 0


def default_root() -> str:
    """Repo root per git, falling back to the working directory outside a repo."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        )
        return os.path.abspath(out.stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return os.path.abspath(os.getcwd())

DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
AUTO_START = "<!-- AUTO:START -->"
AUTO_END = "<!-- AUTO:END -->"
# checks #8, #13: markers that belong to the generated section only
TASK_ICONS = ("⚪", "🟡")
# check #16: the closed marker. Counted in the first cell of a hand-written table row —
# the same icon inside a row's text is prose, not a second closed item.
CLOSED_ICON = "🟢"
# check #11: a sprint entry is `- <ID> — <Title>`. Same shape as the generator's, kept
# here rather than imported — the linter is deliberately standalone, and the two tools
# already validate task headers side by side for the same reason.
SPRINT_ENTRY_RE = re.compile(r"^-\s+(?P<id>[A-Z][A-Z0-9]*-\d+)\s+—\s+(?P<title>.+)$")
SPRINT_HEAD_RE = re.compile(r"^-\s+(?P<id>[A-Z][A-Z0-9]*-\d+)\b")
# check #15: the prefix the template ships with. Not a value belonging to any repository
# using it — a repository still carrying it has not been through the setup command.
PLACEHOLDER_ID_PREFIX = "REPO"
# markdown link target: [text](target)
MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
# frontmatter 'updated:' value or body 'Last updated:' value
UPDATED_RE = re.compile(
    r"(?:^updated:\s*|Last updated:\**\s*)(\d{4}-\d{2}-\d{2})", re.IGNORECASE | re.MULTILINE
)
# entity-style token in an index table first cell (UPPER_CASE or ALL CAPS words)
ENTITY_TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_]{2,}(?: [A-Z0-9_]+)*$")
INDEX_STOPWORDS = {
    "Client", "Project", "Status", "Active", "Completed", "Inbox", "Recent",
    "Files", "Last", "TODO", "DONE", "OPEN", "BLOCKED", "N/A",
}


@dataclass
class Finding:
    level: str      # ERROR | WARN
    check: str      # short check id
    path: str       # repo-relative path
    message: str

    def as_line(self) -> str:
        return f"{self.level}\t{self.check}\t{self.path}\t{self.message}"


def rel(p: str) -> str:
    return os.path.relpath(p, REPO_ROOT).replace(os.sep, "/")


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
    # The task-registry contract (allowed fields and values) is owned by the registry
    # itself; config.yaml only points at it. Checks #10-#12 then validate against the
    # same file the generator enforces.
    reg = config.get("task_registry")
    if reg and reg.get("schema"):
        schema_path = os.path.join(REPO_ROOT, reg["schema"])
        try:
            with open(schema_path, "r", encoding="utf-8") as fh:
                config["task_registry"] = {**yaml.safe_load(fh), **reg}
        except OSError:
            sys.stderr.write(
                f"context-lint: task_registry.schema not readable ({reg['schema']}) — "
                "checks #10-#12 skipped\n"
            )
            config["task_registry"] = None
    return config


def read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return ""


# Unknown YAML tags in frontmatter (an id pasted from an external system can look like
# `thread: !abc:example.local`) would trip safe_load. We never need those values, so
# quote them into plain strings.
_UNKNOWN_TAG_RE = re.compile(r'(?m)^(\s*[A-Za-z0-9_]+:\s+)(![^\n"\']+?)\s*$')


def parse_frontmatter(text: str) -> dict | None:
    """Return the YAML frontmatter dict, {} if delimited but empty, None if absent.

    Uses safe_load (no arbitrary object construction). Unknown `!tag` scalars are
    quoted first so a stray external id cannot break the parse.
    """
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    block = _UNKNOWN_TAG_RE.sub(r'\1"\2"', text[3:end])
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def iter_files(root: str):
    """Yield absolute file paths under root, skipping .git, hidden dirs, hidden files."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git" and not d.startswith(".")]
        for fn in filenames:
            if fn.startswith("."):  # hidden files (.gitkeep, .kyero-sample.xml, …)
                continue
            yield os.path.join(dirpath, fn)


def path_parts(abspath: str, entity_root: str) -> list[str]:
    return rel_within(abspath, entity_root).split("/")


def rel_within(abspath: str, base: str) -> str:
    return os.path.relpath(abspath, base).replace(os.sep, "/")


def name_exempt(basename: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(basename, pat) for pat in patterns)


# --- entity discovery --------------------------------------------------------

def discover_entities(root_cfg: dict) -> list[str]:
    base = os.path.join(REPO_ROOT, root_cfg["path"])
    if not os.path.isdir(base):
        return []
    out = []
    for name in sorted(os.listdir(base)):
        full = os.path.join(base, name)
        if os.path.isdir(full) and not name.startswith("_") and not name.startswith("."):
            out.append(full)
    return out


# --- checks ------------------------------------------------------------------

def check_structure(entity: str, root_cfg: dict, findings: list[Finding]) -> None:
    """#9 required files present per template."""
    for req in root_cfg.get("required_files", []):
        if not os.path.isfile(os.path.join(entity, req)):
            findings.append(Finding("ERROR", "structure", rel(os.path.join(entity, req)),
                                    f"required file missing ({req}) per the entity template"))


def check_catalog(entity: str, cfg: dict, findings: list[Finding]) -> None:
    """#1 catalog <-> files (forward: file uncatalogued; reverse: link unresolved)."""
    catalog_path = os.path.join(entity, "catalog.md")
    if not os.path.isfile(catalog_path):
        return  # structure check already reports the missing catalog
    catalog_text = read_text(catalog_path)
    exclude = set(cfg["catalog_exclude_dirs"])
    structural = set(cfg["structural_files"])
    check_exts = tuple(cfg.get("catalog_check_extensions", [".md"]))

    # A subtree that maintains its own index catalogues itself. The entity's
    # catalog.md then references it at folder level — listing every file inside
    # would be a second, staler copy of that index. R&D directions in this repo
    # work exactly this way (data/<kierunek>/_index.md), and without this rule a
    # single research folder buries the whole run in thousands of findings.
    marker = cfg.get("self_index_marker")
    self_indexed: list[str] = []
    if marker:
        for dirpath, dirnames, filenames in os.walk(entity):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            if dirpath == entity:
                continue
            if marker in filenames:
                self_indexed.append(rel_within(dirpath, entity))
                dirnames[:] = []  # nested indexes belong to that subtree, not here
        for sub in sorted(self_indexed):
            if os.path.basename(sub) not in catalog_text:
                findings.append(Finding("ERROR", "catalog", rel(os.path.join(entity, sub)),
                                        f"directory with its own {marker} but no entry in catalog.md"))

    # forward: catalogued content files must appear (by basename) in catalog text.
    # Only knowledge docs are mapped per-file (default .md); binary assets and
    # machine dumps are referenced by folder, not individually.
    for abspath in iter_files(entity):
        relpath = rel_within(abspath, entity)
        parts = relpath.split("/")
        if any(part in exclude for part in parts[:-1]):
            continue
        if any(relpath.startswith(sub + "/") for sub in self_indexed):
            continue
        basename = parts[-1]
        if len(parts) == 1:  # entity-root files are structural, not catalogued
            continue
        if not basename.endswith(check_exts):
            continue
        if basename in structural or basename.startswith("_"):
            continue
        if basename not in catalog_text:
            findings.append(Finding("ERROR", "catalog", rel(abspath),
                                    "file with no entry in catalog.md"))

    # reverse: markdown-link targets that look like files must resolve
    for target in MD_LINK_RE.findall(catalog_text):
        t = target.strip().split("#", 1)[0]
        if not t or t.startswith(("http://", "https://", "mailto:")):
            continue
        if "." not in os.path.basename(t):  # only file-like targets
            continue
        resolved = os.path.normpath(os.path.join(entity, t))
        if not os.path.exists(resolved):
            findings.append(Finding("ERROR", "catalog", rel(catalog_path),
                                    f"catalog entry points at a missing file: {t}"))


def check_index(root_cfg: dict, entities: list[str], findings: list[Finding]) -> None:
    """#2 index <-> folders (forward: folder without row; reverse: ghost row)."""
    base = os.path.join(REPO_ROOT, root_cfg["path"])
    index_path = os.path.join(base, root_cfg["index"])
    if not os.path.isfile(index_path):
        findings.append(Finding("ERROR", "index", rel(index_path),
                                f"index file missing ({root_cfg['index']})"))
        return
    index_text = read_text(index_path)
    names = [os.path.basename(e) for e in entities]

    # forward: every entity folder must be named in the index
    for name in names:
        if name not in index_text:
            findings.append(Finding("ERROR", "index", rel(os.path.join(base, name)),
                                    f"folder with no row in {root_cfg['index']}"))

    # reverse: entity-style tokens in table first cells must map to a folder
    known = set(names)
    for line in index_text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not cells:
            continue
        token = cells[0]
        token = re.sub(r"[*`\[\]]", "", token).strip()
        if not token or token in INDEX_STOPWORDS or token.startswith("-"):
            continue
        if ENTITY_TOKEN_RE.match(token) and token not in known:
            findings.append(Finding("ERROR", "index", rel(index_path),
                                    f"phantom index row with no folder: {token}"))


def check_index_files(root_cfg: dict, findings: list[Finding]) -> None:
    """#2, file variant — for scopes whose entities are single .md files, not folders.

    Such an entity is one markdown file, so the folder-shaped checks (#1, #3, #5, #6,
    #7, #8, #9) have nothing to bite on. Only the index correspondence carries value
    here — and it carries more of it when the scope is maintained mechanically (a sync
    from an external system, say): drift between the table and the directory means the
    sync broke, not that somebody forgot a row.
    """
    base = os.path.join(REPO_ROOT, root_cfg["path"])
    if not os.path.isdir(base):
        return
    index_name = root_cfg["index"]
    index_path = os.path.join(base, index_name)
    if not os.path.isfile(index_path):
        findings.append(Finding("ERROR", "index", rel(index_path),
                                f"index file missing ({index_name})"))
        return
    index_text = read_text(index_path)

    present = {
        fn for fn in os.listdir(base)
        if fn.endswith(".md") and not fn.startswith("_") and not fn.startswith(".")
        and os.path.isfile(os.path.join(base, fn))
    }

    # forward: every entity file must be named in the index
    for fn in sorted(present):
        if fn not in index_text:
            findings.append(Finding("ERROR", "index", rel(os.path.join(base, fn)),
                                    f"file with no row in {index_name}"))

    # reverse: every local .md link target in the index must resolve to a real file
    for target in MD_LINK_RE.findall(index_text):
        t = target.strip().split("#", 1)[0]
        if not t or t.startswith(("http://", "https://", "mailto:")):
            continue
        if not t.endswith(".md"):
            continue
        if not os.path.exists(os.path.normpath(os.path.join(base, t))):
            findings.append(Finding("ERROR", "index", rel(index_path),
                                    f"phantom index row with no file: {t}"))


def check_names(entity: str, cfg: dict, findings: list[Finding]) -> None:
    """#3 date-prefix convention in communication/ and archive/."""
    date_dirs = set(cfg["date_prefix_dirs"])
    exceptions = cfg["name_exceptions"]
    for abspath in iter_files(entity):
        parts = rel_within(abspath, entity).split("/")
        if not any(part in date_dirs for part in parts[:-1]):
            continue
        basename = parts[-1]
        if basename.startswith(".") or name_exempt(basename, exceptions):
            continue
        if not DATE_PREFIX_RE.match(basename):
            findings.append(Finding("WARN", "naming", rel(abspath),
                                    "missing YYYY-MM-DD date prefix"))


def check_comm_in_deliverables(entity: str, cfg: dict, findings: list[Finding]) -> None:
    """#6 sent-message files sitting in deliverables/."""
    patterns = cfg["communication_patterns"]
    exceptions = set(cfg["deliverables_comm_exceptions"])
    for abspath in iter_files(entity):
        parts = rel_within(abspath, entity).split("/")
        if "deliverables" not in parts[:-1]:
            continue
        basename = parts[-1]
        if basename in exceptions:
            continue
        if any(pat in basename for pat in patterns):
            findings.append(Finding("ERROR", "comm-place", rel(abspath),
                                    "a sent message in deliverables/ — move it to communication/"))


def manual_lines(text: str) -> list[str]:
    """Lines outside the AUTO:START/AUTO:END region — the hand-written part of a file.

    The generated task section is not the author's to shorten, so neither the size
    threshold (#8) nor the manual-task check (#13) may look at it.
    """
    out, inside = [], False
    for line in text.splitlines():
        if AUTO_START in line:
            inside = True
            continue
        if AUTO_END in line:
            inside = False
            continue
        if not inside:
            out.append(line)
    return out


def check_status_size(entity: str, cfg: dict, findings: list[Finding]) -> None:
    """#8 status.md length threshold, counted without the generated section."""
    limit = cfg["thresholds"]["status_max_lines"]
    for dirpath, dirnames, filenames in os.walk(entity):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        if "status.md" in filenames:
            p = os.path.join(dirpath, "status.md")
            n = len(manual_lines(read_text(p)))
            if n > limit:
                findings.append(Finding("WARN", "status-size", rel(p),
                                        f"status.md has {n} hand-written lines (>{limit}) — condense or archive"))


def check_status_closed_rows(entity: str, cfg: dict, findings: list[Finding]) -> None:
    """#16 closed (🟢) rows piling up in status.md instead of ageing into the archive.

    The size threshold (#8) is a backstop and reacts late: one row that grew into a
    paragraph trips it, while forty short closures do not. The agent reads status.md in
    full every time, so the row count is the thing worth bounding.

    `.get` with a default on purpose — a config written before this check existed must
    keep working rather than crash the whole run on a missing key.
    """
    limit = cfg["thresholds"].get("status_closed_rows_kept", 10)
    for dirpath, dirnames, filenames in os.walk(entity):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        if "status.md" not in filenames:
            continue
        p = os.path.join(dirpath, "status.md")
        n = 0
        for line in manual_lines(read_text(p)):
            stripped = line.strip()
            if not stripped.startswith("|"):
                continue
            first_cell = stripped.strip("|").split("|", 1)[0].strip()
            if first_cell == CLOSED_ICON:
                n += 1
        if n > limit:
            findings.append(Finding("WARN", "status-closed-rows", rel(p),
                                    f"{n} closed rows in status.md (>{limit}) — run "
                                    "/close-session to age them into status_archive.md"))


def check_manual_tasks(entity: str, findings: list[Finding]) -> None:
    """#13 hand-written ⚪/🟡 row outside the generated section."""
    for dirpath, dirnames, filenames in os.walk(entity):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        if "status.md" not in filenames:
            continue
        p = os.path.join(dirpath, "status.md")
        for line in manual_lines(read_text(p)):
            stripped = line.lstrip()
            # Only table rows count: the legend names both icons in prose on purpose.
            if not stripped.startswith("|"):
                continue
            if any(icon in stripped for icon in TASK_ICONS):
                findings.append(Finding("ERROR", "manual-task", rel(p),
                                        f"hand-written task row outside the AUTO section — create a file in tasks/: {stripped[:70]}"))


def validate_task_file(abspath: str, tcfg: dict, today: _dt.date, findings: list[Finding]) -> None:
    """#10 task header validity, #12 overdue task."""
    fm = parse_frontmatter(read_text(abspath))
    if not fm:
        findings.append(Finding("ERROR", "task-header", rel(abspath),
                                "task has no YAML header"))
        return
    for field in tcfg["required_fields"]:
        if not fm.get(field):
            findings.append(Finding("ERROR", "task-header", rel(abspath),
                                    f"required field `{field}` is missing"))
    task_id = fm.get("id")
    if task_id and not re.fullmatch(rf"{re.escape(str(tcfg['id_prefix']))}-\d+", str(task_id)):
        findings.append(Finding("ERROR", "task-header", rel(abspath),
                                f"`id: {task_id}` does not match `{tcfg['id_prefix']}-<number>`"))
    for field, allowed in (("owner", tcfg["owners"]), ("status", tcfg["statuses"]),
                           ("priority", tcfg["priorities"])):
        value = fm.get(field)
        if value and value not in allowed:
            findings.append(Finding("ERROR", "task-header", rel(abspath),
                                    f"`{field}: {value}` is not one of {allowed}"))
    if fm.get("status") == "blocked" and not fm.get("blocked_by"):
        findings.append(Finding("ERROR", "task-header", rel(abspath),
                                "`status: blocked` without `blocked_by` — name who we wait on"))
    if fm.get("status") == "done" and not fm.get("closed"):
        findings.append(Finding("ERROR", "task-header", rel(abspath),
                                "`status: done` without a date in `closed`"))
    for field, reason in (tcfg.get("forbidden_fields") or {}).items():
        if field in fm:
            findings.append(Finding("ERROR", "task-header", rel(abspath),
                                    f"field `{field}` does not belong in a task — {reason}"))
    for field in ("created", "due", "closed"):
        value = fm.get(field)
        if value and not ISO_DATE_RE.match(str(value)):
            findings.append(Finding("ERROR", "task-header", rel(abspath),
                                    f"`{field}: {value}` is not an ISO date (YYYY-MM-DD)"))
    due = fm.get("due")
    if due and ISO_DATE_RE.match(str(due)) and fm.get("status") != "done":
        if _dt.date.fromisoformat(str(due)) < today:
            findings.append(Finding("WARN", "task-overdue", rel(abspath),
                                    f"due {due} has passed, state `{fm.get('status')}`"))


def iter_task_files(tasks_dir: str, tcfg: dict):
    """Task files in a tasks/ directory: no archive, no sprints, no generated files."""
    if not os.path.isdir(tasks_dir):
        return
    for fn in sorted(os.listdir(tasks_dir)):
        p = os.path.join(tasks_dir, fn)
        if not os.path.isfile(p) or not fn.endswith(".md"):
            continue
        if fn.startswith("_") or fn.startswith("sprint-") or fn.lower() == "readme.md":
            continue
        yield p


def all_task_dirs(tcfg: dict) -> list[str]:
    """Every directory a task may live in: the registry plus one per entity."""
    dirs = [os.path.join(REPO_ROOT, tcfg["registry_path"])]
    for root in tcfg.get("entity_roots") or []:
        base = os.path.join(REPO_ROOT, root)
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            d = os.path.join(base, name, tcfg["entity_dir_name"])
            if os.path.isdir(d):
                dirs.append(d)
    return [d for d in dirs if os.path.isdir(d)]


def collect_ids(tcfg: dict) -> dict[str, list[tuple[str, str]]]:
    """id → [(path, title)] across the whole repository, `_archive/` included.

    The archive is scanned here and nowhere else on purpose: archiving does not return
    a number to the pool, so a new task reusing an archived id is a real collision. An
    identifier that has left the repository must keep pointing at one thing forever.
    """
    out: dict[str, list[tuple[str, str]]] = {}
    for tasks_dir in all_task_dirs(tcfg):
        paths = list(iter_task_files(tasks_dir, tcfg))
        archive = os.path.join(tasks_dir, tcfg.get("archive_dir") or "_archive")
        paths += list(iter_task_files(archive, tcfg))
        for p in paths:
            fm = parse_frontmatter(read_text(p)) or {}
            if fm.get("id"):
                out.setdefault(str(fm["id"]), []).append((p, str(fm.get("title") or "")))
    return out


def check_id_uniqueness(ids: dict[str, list[tuple[str, str]]], findings: list[Finding]) -> None:
    """#14 the same id claimed by more than one file."""
    for task_id, entries in sorted(ids.items()):
        if len(entries) > 1:
            for p, _ in entries:
                others = ", ".join(rel(q) for q, _ in entries if q != p)
                findings.append(Finding("ERROR", "task-id", rel(p),
                                        f"id `{task_id}` is also used by: {others}"))


def check_id_prefix(tcfg: dict, findings: list[Finding]) -> None:
    """#15 the template's own prefix left in place in a repository that is in use.

    Gated on the example entity: while it is still there, nobody has run the setup
    command, and a fresh clone of the template must not greet its first user with an
    error about a value the template itself shipped.
    """
    example = tcfg.get("template_example_entity")
    if example and os.path.isdir(os.path.join(REPO_ROOT, example)):
        return
    if str(tcfg.get("id_prefix")) == PLACEHOLDER_ID_PREFIX:
        findings.append(Finding("ERROR", "task-id", tcfg.get("schema") or "task_registry.schema",
                                f"`id_prefix` is still the template default `{PLACEHOLDER_ID_PREFIX}` — "
                                "run the setup command's prefix step; identifiers stop telling "
                                "repositories apart while every one of them ships the same one"))


def check_entity_tasks(entity: str, cfg: dict, today: _dt.date, findings: list[Finding]) -> None:
    """#10, #12 for tasks owned by an entity."""
    tcfg = cfg.get("task_registry")
    if not tcfg:
        return
    for p in iter_task_files(os.path.join(entity, tcfg["entity_dir_name"]), tcfg):
        validate_task_file(p, tcfg, today, findings)


def check_task_registry(cfg: dict, today: _dt.date, findings: list[Finding]) -> None:
    """#10, #12 for company-level tasks, #11 sprint integrity, #14 id uniqueness,
    #15 placeholder prefix. Runs once per run."""
    tcfg = cfg.get("task_registry")
    if not tcfg:
        return
    check_id_prefix(tcfg, findings)
    ids = collect_ids(tcfg)
    check_id_uniqueness(ids, findings)
    titles = {task_id: entries[0][1] for task_id, entries in ids.items()}

    base = os.path.join(REPO_ROOT, tcfg["registry_path"])
    if not os.path.isdir(base):
        return
    for p in iter_task_files(base, tcfg):
        validate_task_file(p, tcfg, today, findings)

    active = []
    for fn in sorted(os.listdir(base)):
        if not fn.startswith("sprint-") or not fn.endswith(".md"):
            continue
        p = os.path.join(base, fn)
        fm = parse_frontmatter(read_text(p)) or {}
        if fm.get("status") == "active":
            active.append(p)
    if len(active) > 1:
        for p in active:
            findings.append(Finding("ERROR", "sprint", rel(p),
                                    f"more than one active sprint ({len(active)}) — exactly one may carry status: active"))
    for p in active:
        for line in read_text(p).splitlines():
            stripped = line.strip()
            if not SPRINT_HEAD_RE.match(stripped):
                continue
            m = SPRINT_ENTRY_RE.match(stripped)
            if not m:
                findings.append(Finding("ERROR", "sprint", rel(p),
                                        f"entry is not `- <ID> — <Title>`: {stripped[:70]}"))
                continue
            task_id, title = m.group("id"), m.group("title").strip()
            if task_id not in titles:
                findings.append(Finding("ERROR", "sprint", rel(p),
                                        f"`{task_id}` is not a task in the registry"))
                continue
            if title != titles[task_id].strip():
                findings.append(Finding("ERROR", "sprint", rel(p),
                                        f"`{task_id}` title out of date — sprint says {title!r}, "
                                        f"the task says {titles[task_id].strip()!r}"))


def check_freshness(entity: str, cfg: dict, today: _dt.date, findings: list[Finding]) -> None:
    """#4 updated marker older than freshness_days."""
    limit = cfg["thresholds"]["freshness_days"]
    tracked = set(cfg["freshness_files"])
    for dirpath, dirnames, filenames in os.walk(entity):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if fn not in tracked:
                continue
            p = os.path.join(dirpath, fn)
            m = UPDATED_RE.search(read_text(p))
            if not m:
                continue
            try:
                d = _dt.date.fromisoformat(m.group(1))
            except ValueError:
                continue
            age = (today - d).days
            if age > limit:
                findings.append(Finding("WARN", "freshness", rel(p),
                                        f"Last updated {m.group(1)} — {age} days ago (>{limit})"))


def check_inbox(entity: str, findings: list[Finding]) -> None:
    """#5 non-empty inbox/."""
    for dirpath, dirnames, filenames in os.walk(entity):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        if os.path.basename(dirpath) == "inbox":
            real = [f for f in filenames if not f.startswith(".") and f != ".gitkeep"]
            if real:
                findings.append(Finding("WARN", "inbox", rel(dirpath),
                                        f"inbox/ is not empty ({len(real)} file(s)) — run /ingest"))


def check_extraction(entity: str, cfg: dict, findings: list[Finding]) -> None:
    """#7 communication/ files with extracted:false or no frontmatter."""
    comm = cfg["communication_dir"]
    for abspath in iter_files(entity):
        parts = rel_within(abspath, entity).split("/")
        if comm not in parts[:-1]:
            continue
        basename = parts[-1]
        if basename.startswith(".") or not basename.endswith(".md"):
            continue
        fm = parse_frontmatter(read_text(abspath))
        if fm is None:
            findings.append(Finding("WARN", "extraction", rel(abspath),
                                    "message with no frontmatter (no extracted flag)"))
        elif fm.get("extracted") is not True:
            findings.append(Finding("WARN", "extraction", rel(abspath),
                                    "extracted != true — the decisions in it were never pulled out"))


# --- orchestration -----------------------------------------------------------

def run(config: dict, scope: str | None, today: _dt.date) -> list[Finding]:
    findings: list[Finding] = []
    scope_abs = os.path.abspath(scope) if scope else None
    global SCANNED
    SCANNED = 0

    for root_cfg in config["scan_roots"]:
        entities = discover_entities(root_cfg)
        # index check runs at root level (needs the full entity list)
        if scope_abs is None or _within(os.path.join(REPO_ROOT, root_cfg["path"]), scope_abs):
            check_index(root_cfg, entities, findings)
        for entity in entities:
            if scope_abs is not None and not _within(entity, scope_abs):
                continue
            SCANNED += 1
            check_structure(entity, root_cfg, findings)
            check_catalog(entity, config, findings)
            check_names(entity, config, findings)
            check_comm_in_deliverables(entity, config, findings)
            check_status_size(entity, config, findings)
            check_status_closed_rows(entity, config, findings)
            check_freshness(entity, config, today, findings)
            check_inbox(entity, findings)
            check_extraction(entity, config, findings)
            check_manual_tasks(entity, findings)
            check_entity_tasks(entity, config, today, findings)

    for root_cfg in config.get("file_scopes", []):
        if scope_abs is None or _within(os.path.join(REPO_ROOT, root_cfg["path"]), scope_abs):
            check_index_files(root_cfg, findings)

    tcfg = config.get("task_registry")
    if tcfg and (scope_abs is None or _within(os.path.join(REPO_ROOT, tcfg["registry_path"]), scope_abs)):
        check_task_registry(config, today, findings)

    findings.sort(key=lambda f: (f.level != "ERROR", f.check, f.path))
    return findings


def _within(entity: str, scope_abs: str) -> bool:
    """True when entity is inside scope, or scope is inside entity (root selected)."""
    e = os.path.abspath(entity)
    return e.startswith(scope_abs) or scope_abs.startswith(e)


def emit(findings: list[Finding], as_json: bool) -> None:
    errors = sum(1 for f in findings if f.level == "ERROR")
    warns = len(findings) - errors
    if as_json:
        print(json.dumps({
            "summary": {"errors": errors, "warnings": warns, "total": len(findings)},
            "findings": [asdict(f) for f in findings],
        }, ensure_ascii=False, indent=2))
        return
    for f in findings:
        print(f.as_line())
    # The entity count is the point of this line, not decoration. A clean repo prints no
    # findings, which on its own is indistinguishable from a config that matched nothing
    # and scanned zero entities — the failure mode this tool is least able to notice.
    print(f"\n{len(findings)} findings: {errors} ERROR, {warns} WARN "
          f"({SCANNED} entities scanned)", file=sys.stderr)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Deterministic context/ consistency lint.")
    ap.add_argument("path", nargs="?", default=None, help="limit scan to a subtree")
    ap.add_argument("--json", action="store_true", help="emit findings as JSON")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="path to config.yaml")
    ap.add_argument("--root", default=None,
                    help="repository root (default: git toplevel, else cwd)")
    args = ap.parse_args(argv)

    global REPO_ROOT
    REPO_ROOT = os.path.abspath(args.root) if args.root else default_root()

    # Windows consoles default to a legacy codepage that mangles Polish output;
    # force UTF-8 so findings stay readable and parsable downstream.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    config = load_config(args.config)
    today = _dt.date.today()
    findings = run(config, args.path, today)
    emit(findings, args.json)
    return 1 if any(f.level == "ERROR" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
