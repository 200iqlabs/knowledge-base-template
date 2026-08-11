#!/usr/bin/env python3
"""
Task registry generator.

Scans every tasks/ directory under context/, parses task headers and rewrites:
  - the AUTO region of context/tasks/_index.md (all live tasks + group views +
    the ID → path table a session in another repository resolves identifiers with)
  - the AUTO region of status.md in each entity that owns tasks

Live task = status in (todo, open, blocked). Tasks with status: done stay in place
until the sprint is closed but drop out of the open lists; they are reported in a
separate "awaiting archiving" section so closing a sprint never requires a directory scan.

Validation errors go to stderr and exclude the offending task from the index.
Exit code is 0 unless --strict is passed, so the pre-commit hook never blocks a commit.

Usage:
    python <path-to>/regen.py [--strict] [--check] [--next-id]
                              [--root ROOT] [--schema SCHEMA]

    --root     Repository root the scan is anchored to.
               Default: `git rev-parse --show-toplevel`, else the working directory.
    --schema   Task contract to enforce. Default: the schema.yaml next to this script.
    --next-id  Print the next free identifier and exit, writing nothing.
"""

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

AUTO_START = "<!-- AUTO:START -->"
AUTO_END = "<!-- AUTO:END -->"

DEFAULT_SCHEMA = Path(__file__).resolve().parent / "schema.yaml"

STATUS_ICON = {"todo": "⚪", "open": "🟡", "blocked": "🔴", "done": "🟢"}

# Every label the renderers ask for. Listed so a missing one fails at configure()
# with a name, instead of a KeyError halfway through rewriting somebody's index.
LABEL_KEYS = (
    "index_heading", "index_table_header", "index_table_divider", "index_empty",
    "groups_heading", "group_line_due", "archive_heading", "archive_line_closed",
    "status_heading", "status_table_header", "status_table_divider", "status_empty",
    "id_map_heading", "id_map_table_header", "id_map_table_divider", "id_map_empty",
    "index_footer", "sprint_active", "sprint_none",
)

# A sprint entry is `- <ID> — <Title>`. The title is copied by hand for reading comfort
# and checked against the task's own `title` field, so the copy cannot quietly rot.
SPRINT_ENTRY_RE = re.compile(r"^-\s+(?P<id>[A-Z][A-Z0-9]*-\d+)\s+—\s+(?P<title>.+)$")
# Any bullet that opens with something id-shaped is meant to be an entry. Bullets that
# do not are prose — a sprint file may carry a note or a ritual checklist.
SPRINT_HEAD_RE = re.compile(r"^-\s+(?P<id>[A-Z][A-Z0-9]*-\d+)\b")

# Everything below is bound by configure(), never at import time. The repo root is
# not derived from this file's own location: the generator lives in a template
# directory that is itself a subtree of the consuming repo, so counting parent
# directories would anchor the scan inside the template instead of the repo.
REPO_ROOT: Path
CONTEXT_DIR: Path
REGISTRY_DIR: Path
INDEX_FILE: Path
SCHEMA: dict
REQUIRED_FIELDS: tuple
OWNERS: tuple
STATUSES: tuple
PRIORITIES: tuple
LIVE_STATUSES: tuple
FORBIDDEN_FIELDS: dict
PRIORITY_RANK: dict
OWNER_LABEL: dict
ALLOWED_TASK_DIRS: tuple
ENTITY_ROOTS: tuple
GROUP_FIELD: str
LABELS: dict
ID_PREFIX: str
ID_RE: re.Pattern
ARCHIVE_DIR: str

errors: list[str] = []


def default_root() -> Path:
    """Repo root per git, falling back to the working directory outside a repo."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        )
        return Path(out.stdout.strip()).resolve()
    except (OSError, subprocess.CalledProcessError):
        return Path.cwd().resolve()


def configure(root=None, schema_path=None) -> None:
    """Bind the repo root and the task contract. Call once, before anything else."""
    global REPO_ROOT, CONTEXT_DIR, REGISTRY_DIR, INDEX_FILE, SCHEMA
    global REQUIRED_FIELDS, OWNERS, STATUSES, PRIORITIES, LIVE_STATUSES
    global FORBIDDEN_FIELDS, PRIORITY_RANK, OWNER_LABEL
    global ALLOWED_TASK_DIRS, ENTITY_ROOTS, GROUP_FIELD, LABELS, _task_dirs_cache
    global ID_PREFIX, ID_RE, ARCHIVE_DIR

    REPO_ROOT = Path(root).resolve() if root else default_root()
    SCHEMA = yaml.safe_load(Path(schema_path or DEFAULT_SCHEMA).read_text(encoding="utf-8"))

    CONTEXT_DIR = REPO_ROOT / "context"
    REGISTRY_DIR = REPO_ROOT / SCHEMA["registry_path"]
    INDEX_FILE = REGISTRY_DIR / "_index.md"

    REQUIRED_FIELDS = tuple(SCHEMA["required_fields"])
    OWNERS = tuple(SCHEMA["owners"])
    STATUSES = tuple(SCHEMA["statuses"])
    PRIORITIES = tuple(SCHEMA["priorities"])
    LIVE_STATUSES = tuple(SCHEMA["live_statuses"])
    FORBIDDEN_FIELDS = dict(SCHEMA["forbidden_fields"])
    PRIORITY_RANK = {p: i for i, p in enumerate(PRIORITIES)}
    OWNER_LABEL = dict(SCHEMA.get("owner_labels") or {})
    GROUP_FIELD = SCHEMA.get("group_field", "group")
    ID_PREFIX = SCHEMA["id_prefix"]
    ID_RE = re.compile(rf"^{re.escape(ID_PREFIX)}-\d+$")
    ARCHIVE_DIR = SCHEMA["archive_dir"]
    # Text written into repository files. Missing keys are a broken contract, not a
    # cosmetic gap: a silent default would write English into somebody's board.
    LABELS = dict(SCHEMA["labels"])
    missing = [k for k in LABEL_KEYS if k not in LABELS]
    if missing:
        raise SystemExit(f"schema: labels missing keys {missing}")

    entity_dir = SCHEMA["entity_dir_name"]
    entity_roots = tuple(SCHEMA.get("entity_roots") or ())
    ENTITY_ROOTS = tuple(REPO_ROOT / r for r in entity_roots)
    # Where a tasks/ directory is allowed to live, relative to the repo root.
    ALLOWED_TASK_DIRS = (SCHEMA["registry_path"],) + tuple(
        f"{r}/*/{entity_dir}" for r in entity_roots
    )

    _task_dirs_cache = None


def error(msg: str) -> None:
    errors.append(msg)
    print(f"  [!] {msg}", file=sys.stderr)


def rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def parse_frontmatter(filepath: Path) -> dict | None:
    content = filepath.read_text(encoding="utf-8")
    if not content.startswith("---"):
        error(f"{rel(filepath)}: no YAML header")
        return None
    parts = content.split("---", 2)
    if len(parts) < 3:
        error(f"{rel(filepath)}: unterminated YAML header")
        return None
    try:
        data = yaml.safe_load(parts[1])
    except yaml.YAMLError as exc:
        error(f"{rel(filepath)}: YAML error — {exc}")
        return None
    if not isinstance(data, dict):
        error(f"{rel(filepath)}: YAML header is not a mapping of fields")
        return None
    return data


def validate(filepath: Path, data: dict) -> bool:
    """Report every problem found, then answer whether the task may enter the index."""
    ok = True
    for field in REQUIRED_FIELDS:
        if not data.get(field):
            error(f"{rel(filepath)}: required field `{field}` is missing")
            ok = False
    if data.get("id") and not ID_RE.match(str(data["id"])):
        error(f"{rel(filepath)}: `id: {data['id']}` does not match `{ID_PREFIX}-<number>`")
        ok = False
    if data.get("owner") and data["owner"] not in OWNERS:
        error(f"{rel(filepath)}: `owner: {data['owner']}` is not one of {list(OWNERS)}")
        ok = False
    if data.get("status") and data["status"] not in STATUSES:
        error(f"{rel(filepath)}: `status: {data['status']}` is not one of {list(STATUSES)}")
        ok = False
    if data.get("priority") and data["priority"] not in PRIORITIES:
        error(f"{rel(filepath)}: `priority: {data['priority']}` is not one of {list(PRIORITIES)}")
        ok = False
    if data.get("status") == "blocked" and not data.get("blocked_by"):
        error(f"{rel(filepath)}: `status: blocked` without `blocked_by` — name who we wait on")
        ok = False
    if data.get("status") == "done" and not data.get("closed"):
        error(f"{rel(filepath)}: `status: done` without a date in `closed`")
        ok = False
    for field, reason in FORBIDDEN_FIELDS.items():
        if field in data:
            error(f"{rel(filepath)}: field `{field}` does not belong in a task — {reason}")
            ok = False
    for field in ("created", "due", "closed"):
        value = data.get(field)
        if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)):
            error(f"{rel(filepath)}: `{field}: {value}` is not an ISO date (YYYY-MM-DD)")
            ok = False
    return ok


_task_dirs_cache: list[Path] | None = None


def find_task_dirs() -> list[Path]:
    """Every tasks/ directory under context/, with misplaced ones reported.

    Cached: called from both the collector and the status.md pass, and a misplaced
    directory must be reported once, not once per caller.
    """
    global _task_dirs_cache
    if _task_dirs_cache is not None:
        return _task_dirs_cache
    allowed = {p.resolve() for pattern in ALLOWED_TASK_DIRS for p in REPO_ROOT.glob(pattern)}
    found = []
    for path in CONTEXT_DIR.rglob("tasks"):
        if not path.is_dir() or "_archive" in path.parts:
            continue
        if path.resolve() not in allowed:
            error(f"{rel(path)}: katalog tasks/ w niedozwolonym miejscu")
            continue
        found.append(path)
    _task_dirs_cache = sorted(found)
    return _task_dirs_cache


def entity_of(tasks_dir: Path) -> tuple[str, Path | None]:
    """(display name, status.md path). Company tasks have no entity and no status.md."""
    parent = tasks_dir.parent
    if parent.resolve() == CONTEXT_DIR.resolve():
        return "—", None
    return parent.name, parent / "status.md"


def is_task_file(f: Path) -> bool:
    """`_*` are generated or template files, `sprint-*` are lists of tasks, not tasks."""
    return not (
        f.name.startswith("_")
        or f.name.startswith("sprint-")
        or f.name.lower() == "readme.md"
    )


ID_HEADER_RE = re.compile(r"^id:\s*[\"']?([^\"'\s]+)", re.MULTILINE)


def scan_ids() -> dict[str, list[Path]]:
    """Every id in the repository → the files carrying it, archived tasks included.

    Reads the id line straight out of the header instead of going through
    parse_frontmatter(): an archived task is validated by nothing else, and a stale
    header in `_archive/` should not fill the console with complaints about work closed
    last quarter. What we need from it is one thing — that its number is spoken for.
    """
    found: dict[str, list[Path]] = {}
    for tasks_dir in find_task_dirs():
        candidates = list(tasks_dir.glob("*.md")) + list((tasks_dir / ARCHIVE_DIR).glob("*.md"))
        for f in sorted(candidates):
            if not is_task_file(f):
                continue
            content = f.read_text(encoding="utf-8")
            if not content.startswith("---"):
                continue
            header = content.split("---", 2)[1] if content.count("---") >= 2 else ""
            match = ID_HEADER_RE.search(header)
            if match:
                found.setdefault(match.group(1), []).append(f)
    return found


def check_id_uniqueness(ids: dict[str, list[Path]]) -> None:
    """An id is a promise that survives leaving the repository, so a second file
    claiming it is an error even when the first one is already archived: archiving
    does not return a number to the pool."""
    for task_id, paths in sorted(ids.items()):
        if len(paths) > 1:
            error(f"id `{task_id}` used by {len(paths)} files: " + ", ".join(rel(p) for p in paths))


def next_id(ids: dict[str, list[Path]] | None = None) -> str:
    """The next free identifier: the highest number ever handed out, plus one.

    Derived by scanning rather than stored in a counter file. A counter would be a
    merge-conflict magnet — several sessions work in this repository at once, and each
    new task would touch the same line.
    """
    if ids is None:
        ids = scan_ids()
    highest = 0
    for task_id in ids:
        match = ID_RE.match(task_id)
        if match:
            highest = max(highest, int(task_id.rsplit("-", 1)[1]))
    return f"{ID_PREFIX}-{highest + 1}"


def collect_tasks() -> list[dict]:
    tasks = []
    for tasks_dir in find_task_dirs():
        entity, status_file = entity_of(tasks_dir)
        for f in sorted(tasks_dir.glob("*.md")):
            if not is_task_file(f):
                continue
            data = parse_frontmatter(f)
            if data is None:
                continue
            if not validate(f, data):
                continue
            tasks.append(
                {
                    "path": f,
                    "entity": entity,
                    "status_file": status_file,
                    "tasks_dir": tasks_dir,
                    **data,
                }
            )
    return tasks


def sprint_entries(sprint_file: Path, by_id: dict[str, dict]) -> set[Path]:
    """Resolve `- <ID> — <Title>` entries to task paths, checking both halves.

    The title is redundant with the task's own `title` field, and that is deliberate:
    a sprint file listing bare identifiers is unreadable by a human. The redundancy is
    made safe by checking it — a copy nobody verifies is a copy that drifts.
    """
    linked: set[Path] = set()
    for line in sprint_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not SPRINT_HEAD_RE.match(stripped):
            continue
        match = SPRINT_ENTRY_RE.match(stripped)
        if not match:
            error(f"{rel(sprint_file)}: sprint entry is not `- <ID> — <Title>` → {stripped}")
            continue
        task_id, title = match.group("id"), match.group("title").strip()
        task = by_id.get(task_id)
        if task is None:
            error(f"{rel(sprint_file)}: `{task_id}` is not a task in the registry")
            continue
        if title != str(task["title"]).strip():
            error(
                f"{rel(sprint_file)}: `{task_id}` title out of date — "
                f"sprint says {title!r}, the task says {str(task['title']).strip()!r}"
            )
        linked.add(task["path"].resolve())
    return linked


def read_active_sprint(tasks: list[dict]) -> tuple[Path | None, set[Path]]:
    """Resolve the single active sprint file and the tasks its entries point at."""
    by_id: dict[str, dict] = {}
    for t in tasks:
        by_id.setdefault(str(t["id"]), t)
    active = []
    for f in sorted(REGISTRY_DIR.glob("sprint-*.md")):
        data = parse_frontmatter(f)
        if data and data.get("status") == "active":
            active.append(f)
    if not active:
        return None, set()
    # Every active sprint gets its entries checked — reporting only the first would hide
    # a stale entry in exactly the file the next commit is most likely to fix.
    per_file = {f: sprint_entries(f, by_id) for f in active}
    if len(active) > 1:
        error("more than one active sprint: " + ", ".join(rel(f) for f in active))
    sprint_file = active[0]
    return sprint_file, per_file[sprint_file]


def sort_key(task: dict) -> tuple:
    due = str(task.get("due") or "9999-99-99")
    return (due, PRIORITY_RANK.get(task.get("priority"), 99), task["entity"].lower())


def link_from(source: Path, task: Path) -> str:
    return os.path.relpath(task, source.parent).replace(os.sep, "/")


def link_text(title: str) -> str:
    """Escape brackets: a title like `[O1] Druga warstwa` would break the link syntax."""
    return str(title).replace("[", "\\[").replace("]", "\\]")


def render_index(tasks: list[dict], sprint_file: Path | None, linked: set[Path]) -> str:
    live = [t for t in tasks if t.get("status") in LIVE_STATUSES]
    done = [t for t in tasks if t.get("status") == "done"]
    sprint_name = sprint_file.stem.replace("sprint-", "") if sprint_file else None

    lines = [f"## {LABELS['index_heading']}", ""]
    if live:
        lines.append(LABELS["index_table_header"])
        lines.append(LABELS["index_table_divider"])
        for t in sorted(live, key=sort_key):
            href = link_from(INDEX_FILE, t["path"])
            in_sprint = "✓" if t["path"].resolve() in linked else "—"
            lines.append(
                f"| `{t['id']}` | {t['entity']} | [{link_text(t['title'])}]({href}) "
                f"| {OWNER_LABEL.get(t['owner'], t['owner'])} "
                f"| {STATUS_ICON[t['status']]} | {t['priority']} "
                f"| {t.get('due') or '—'} | {in_sprint} |"
            )
    else:
        lines.append(LABELS["index_empty"])
    lines.append("")

    groups: dict[str, list[dict]] = {}
    for t in live:
        if t.get(GROUP_FIELD):
            groups.setdefault(str(t[GROUP_FIELD]), []).append(t)
    if groups:
        lines.append(f"## {LABELS['groups_heading']}")
        lines.append("")
        for name in sorted(groups):
            lines.append(f"### {name}")
            lines.append("")
            for t in sorted(groups[name], key=sort_key):
                href = link_from(INDEX_FILE, t["path"])
                lines.append(
                    f"- {STATUS_ICON[t['status']]} `{t['id']}` "
                    f"[{link_text(t['title'])}]({href}) — {t['entity']}, "
                    f"{OWNER_LABEL.get(t['owner'], t['owner'])}, "
                    f"{LABELS['group_line_due']} {t.get('due') or '—'}"
                )
            lines.append("")

    if done:
        lines.append(f"## {LABELS['archive_heading']}")
        lines.append("")
        for t in sorted(done, key=lambda x: str(x.get("closed") or "")):
            href = link_from(INDEX_FILE, t["path"])
            lines.append(
                f"- `{t['id']}` [{link_text(t['title'])}]({href}) — {t['entity']}, "
                f"{LABELS['archive_line_closed']} {t.get('closed')}"
            )
        lines.append("")

    # The lookup table. Its reader is a session working in a different repository, which
    # was handed an identifier and nothing else: it cannot guess the entity, and must not
    # go hunting through tasks/ directories by content. Paths are relative to the repo
    # root with POSIX separators, because that reader is not necessarily on this OS.
    lines.append(f"## {LABELS['id_map_heading']}")
    lines.append("")
    if live:
        lines.append(LABELS["id_map_table_header"])
        lines.append(LABELS["id_map_table_divider"])
        for t in sorted(live, key=lambda x: int(str(x["id"]).rsplit("-", 1)[1])):
            lines.append(f"| `{t['id']}` | `{rel(t['path'])}` |")
    else:
        lines.append(LABELS["id_map_empty"])
    lines.append("")

    sprint_line = (
        LABELS["sprint_active"].format(name=sprint_name) if sprint_name else LABELS["sprint_none"]
    )
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines.append(LABELS["index_footer"].format(
        live=len(live), done=len(done), sprint=sprint_line, date=stamp,
    ))
    return "\n".join(lines)


def render_status_section(tasks: list[dict]) -> str:
    live = [t for t in tasks if t.get("status") in LIVE_STATUSES]
    if not live:
        return LABELS["status_empty"]
    lines = [LABELS["status_table_header"], LABELS["status_table_divider"]]
    for t in sorted(live, key=sort_key):
        href = link_from(t["status_file"], t["path"])
        lines.append(
            f"| `{t['id']}` | {STATUS_ICON[t['status']]} | [{link_text(t['title'])}]({href}) "
            f"| {OWNER_LABEL.get(t['owner'], t['owner'])} | {t.get('due') or '—'} |"
        )
    return "\n".join(lines)


def manual_part(text: str) -> list[str]:
    """Lines outside the AUTO region — the hand-written part of a file.

    Used by the daily report to read 🔴 rows from status.md: the generated section
    holds tasks, and a task is never the same thing as waiting on an outside party.
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


def write_auto_region(target: Path, body: str, heading: str | None = None) -> bool:
    """Replace the AUTO region, appending the section when the file has no markers."""
    if not target.exists():
        error(f"{rel(target)}: file does not exist — AUTO section not written")
        return False
    content = target.read_text(encoding="utf-8")
    block = f"{AUTO_START}\n{body}\n{AUTO_END}"
    pattern = re.compile(re.escape(AUTO_START) + r".*?" + re.escape(AUTO_END), re.DOTALL)
    if pattern.search(content):
        new_content = pattern.sub(lambda _: block, content)
    elif heading:
        new_content = content.rstrip("\n") + f"\n\n{heading}\n\n{block}\n"
    else:
        error(f"{rel(target)}: no AUTO:START/AUTO:END markers")
        return False
    if new_content == content:
        return False
    target.write_text(new_content, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Regenerate the task registry index.")
    parser.add_argument("--strict", action="store_true", help="exit 1 when validation reports an error")
    parser.add_argument("--check", action="store_true", help="report only, write nothing")
    parser.add_argument(
        "--porcelain",
        action="store_true",
        help="print only the paths of rewritten files, one per line (for the pre-commit hook)",
    )
    parser.add_argument(
        "--next-id",
        action="store_true",
        help="print the next free task identifier and exit (for the skill that creates tasks)",
    )
    parser.add_argument("--root", default=None,
                        help="repository root (default: git toplevel, else cwd)")
    parser.add_argument("--schema", default=None,
                        help="path to schema.yaml (default: the one next to this script)")
    args = parser.parse_args()

    configure(args.root, args.schema)

    if args.next_id:
        print(next_id())
        return 0

    tasks = collect_tasks()
    check_id_uniqueness(scan_ids())
    sprint_file, linked = read_active_sprint(tasks)

    if args.check:
        live = sum(1 for t in tasks if t.get("status") in LIVE_STATUSES)
        print(f"[=] {live} live tasks, {len(errors)} errors (check — nothing written)")
        return 1 if errors and args.strict else 0

    changed = []
    if write_auto_region(INDEX_FILE, render_index(tasks, sprint_file, linked)):
        changed.append(rel(INDEX_FILE))

    by_status_file: dict[Path, list[dict]] = {}
    for t in tasks:
        if t["status_file"] is not None:
            by_status_file.setdefault(t["status_file"], []).append(t)
    # Entities that own a tasks/ directory get a section even when it is empty, and every
    # status.md that already carries an AUTO region gets rewritten even when its tasks/
    # directory is gone. Deriving the set from the tasks alone would leave the last
    # deleted task frozen in the file forever.
    for tasks_dir in find_task_dirs():
        _, status_file = entity_of(tasks_dir)
        if status_file is not None:
            by_status_file.setdefault(status_file, [])
    for status_file in CONTEXT_DIR.rglob("status.md"):
        if AUTO_START in status_file.read_text(encoding="utf-8"):
            by_status_file.setdefault(status_file, [])
    for status_file, entity_tasks in sorted(by_status_file.items()):
        if write_auto_region(status_file, render_status_section(entity_tasks),
                             heading=LABELS["status_heading"]):
            changed.append(rel(status_file))

    live = sum(1 for t in tasks if t.get("status") in LIVE_STATUSES)
    if args.porcelain:
        for path in changed:
            print(path)
    elif changed:
        print(f"[+] Task registry: {live} live, {len(changed)} file(s) updated")
        for path in changed:
            print(f"    {path}")
    else:
        print(f"[=] Task registry unchanged ({live} live)")

    if errors:
        print(f"[!] {len(errors)} validation error(s) — see stderr", file=sys.stderr)
        return 1 if args.strict else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
