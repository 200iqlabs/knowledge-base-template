#!/usr/bin/env python3
"""
Report generator — prints the task report for one person, on demand.

Six sections, in order:
  1. Overdue        — due < today, status != done
  2. Due today      — due == today, or a window (start..due) covering today
  3. In play        — start <= today with no due date
  4. Blocked        — 🔴 rows from every status.md, plus tasks with status: blocked
  5. From outside   — task files a session in another repository has written to
  6. Quiet          — entities with no commit for longer than the threshold

Sections 2 and 3 are what a task is "current" in: the window lives in the task's own
`start` and `due`, so there is no membership list to keep in step with anything. A task
with neither date is live and indexed but appears in none of the first three sections —
it comes back through the monthly pool review, not through this report, which is a plan
for the day and not a dump of the registry.

A task with `status: blocked` is excluded from sections 2 and 3: waiting on an outside
party is not work to do. It stays in section 1 when its due date has passed, though — a
missed deadline on something you are waiting for is the signal to escalate, and is
exactly what must not be swallowed.

Section 4 is what keeps the rest of them visible. It reads **two sources**: the
hand-written 🔴 rows, and the task files themselves. Without the second, a blocked task
whose entity has no matching 🔴 row appears in no section of the report at all — excluded
from 2 and 3 by the rule above, and not overdue yet. The two sources can describe the
same wait twice; that duplication is the known inconsistency between board rows and task
state, and showing it beats hiding a task nobody is tracking.

Section titles come from the schema (`report_labels`), not from this file: the
report is what a human reads every morning, so its language is configuration.

Sections 1–3 are filtered to one owner (plus the schema's `shared_owner`, which is
everyone's). Sections
4–6 are not: a blocker on the other cofounder is exactly what you want to see, an entity
gone quiet belongs to nobody in particular, and a task changed from outside needs to be
noticed by whoever is about to commit — not only by its owner. They are also the only
reason the report reads anything outside the registry: a task file never knows that we
are waiting on an outside party (status.md does), nor that it has uncommitted changes
(git does).

The report is ephemeral by design: it prints to stdout and is not committed. It reads the
working directory, so it shows what is on disk right now, pushed or not.

Usage:
    python <path-to>/regen_today.py [--owner <id>] [--date YYYY-MM-DD]
                                    [--silence-days N] [--write]
                                    [--root ROOT] [--schema SCHEMA]
"""

import argparse
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Imported as a module, not as `from regen import CONTEXT_DIR, ...`: those names are
# bound by regen.configure() at run time, and a from-import would freeze whatever
# they held at import time — which is nothing.
import regen  # noqa: E402
from regen import (  # noqa: E402  — functions read the module globals when called
    STATUS_ICON,
    collect_tasks,
    link_from,
    manual_part,
    rel,
    sort_key,
)

DEFAULT_SILENCE_DAYS = 30


def today_file() -> Path:
    return regen.REGISTRY_DIR / "_today.md"


def task_line(task: dict) -> str:
    L = regen.SCHEMA["report_labels"]
    href = link_from(today_file(), task["path"])
    due = L["task_due"].format(due=task["due"]) if task.get("due") else L["task_no_due"]
    # The start date is printed only when it is set: on a task that carries a due date it
    # answers "since when has this been open", which a plan for the day needs, and on one
    # without a due date it is the only thing saying the task was ever picked up.
    dates = due
    if task.get("start"):
        dates = f"{L['task_start'].format(start=task['start'])}, {due}"
    return (
        f"- {STATUS_ICON[task['status']]} `{task['id']}` **{task['entity']}** "
        f"· [{task['title']}]({href}) "
        f"— {regen.OWNER_LABEL.get(task['owner'], task['owner'])}, {task['priority']}, {dates}"
    )


def uncommitted_paths() -> set[Path]:
    """Files git reports as changed in the working directory, resolved to absolute paths.

    This is how a change made from another repository becomes visible. That session
    writes and stops — it never commits, because several sessions work here at once and
    a commit from a process blind to the working directory would sweep up somebody
    else's staged work.
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=regen.REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    out = set()
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        # A rename is reported as `old -> new`; only the destination exists on disk.
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        out.add((regen.REPO_ROOT / path.strip().strip('"')).resolve())
    return out


def touched_from_outside(tasks: list[dict]) -> list[dict]:
    changed = uncommitted_paths()
    return [t for t in tasks if t["path"].resolve() in changed]


def blocked_tasks(tasks: list[dict]) -> list[str]:
    """Rendered lines for every task carrying `status: blocked`.

    Unfiltered by owner, like the rest of this section: a colleague's blocker is exactly
    what the person about to plan their day needs to see.
    """
    L = regen.SCHEMA["report_labels"]
    out = []
    for t in sorted(tasks, key=lambda x: (x["entity"].lower(), str(x["id"]))):
        if t.get("status") != "blocked":
            continue
        out.append(L["blocked_task_line"].format(
            entity=t["entity"],
            id=t["id"],
            title=t["title"],
            waiting_on=L["blocked_waiting_on"],
            who=str(t.get("blocked_by") or L["blocked_unnamed"]).strip(),
        ))
    return out


def blocked_rows() -> list[tuple[str, str]]:
    """(entity, row text) for every 🔴 row in the hand-written part of a status.md."""
    L = regen.SCHEMA["report_labels"]
    out = []
    for status_file in sorted(regen.CONTEXT_DIR.rglob("status.md")):
        entity = status_file.parent.name
        for line in manual_part(status_file.read_text(encoding="utf-8")):
            stripped = line.strip()
            if not stripped.startswith("|") or "🔴" not in stripped:
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if len(cells) < 2 or not cells[1]:
                continue
            who = cells[2] if len(cells) > 2 and cells[2] not in ("", "—") else L["blocked_unnamed"]
            out.append((entity, f"{cells[1]} — {L['blocked_waiting_on']}: {who}"))
    return out


def last_commit_date(path: Path) -> date | None:
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%cs", "--", str(path)],
            cwd=regen.REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    stamp = result.stdout.strip()
    try:
        return date.fromisoformat(stamp)
    except ValueError:
        return None


def silent_entities(today: date, threshold: int) -> list[tuple[str, int]]:
    out = []
    for root in regen.ENTITY_ROOTS:
        if not root.is_dir():
            continue
        for entity in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")):
            last = last_commit_date(entity)
            if last is None:
                continue
            age = (today - last).days
            if age > threshold:
                out.append((entity.name, age))
    return sorted(out, key=lambda x: -x[1])


def render(today: date, threshold: int, owner: str) -> str:
    # Collected once, unfiltered: sections 4-6 report on the whole repository, not on
    # the slice belonging to one person.
    all_tasks = collect_tasks()

    tasks = [t for t in all_tasks if t.get("status") in regen.LIVE_STATUSES]
    if owner != "all":
        # The shared owner belongs to everybody, so it shows up in every person's report.
        # Read from the contract, never hardcoded: the id differs per repository, and a
        # literal here would quietly drop the shared tasks in all the others.
        shared = regen.SCHEMA.get("shared_owner")
        keep = {owner} | ({shared} if shared else set())
        tasks = [t for t in tasks if t.get("owner") in keep]

    iso = today.isoformat()
    overdue, due_today, in_play = [], [], []
    for t in sorted(tasks, key=sort_key):
        due = str(t.get("due") or "")
        start = str(t.get("start") or "")
        if due and due < iso:
            overdue.append(t)
            continue
        # Everything below is "work for today", which a task waiting on somebody else is
        # not. The overdue section above deliberately sits before this line.
        if t.get("status") == "blocked":
            continue
        if due:
            # Inside the window, or due exactly today. A due date still in the future with
            # no start means the task has not been picked up yet, so it waits.
            if due == iso or (start and start <= iso):
                due_today.append(t)
        elif start and start <= iso:
            in_play.append(t)

    L = regen.SCHEMA["report_labels"]
    who = L["everyone"] if owner == "all" else regen.OWNER_LABEL.get(owner, owner)
    lines = [
        f"# {L['title'].format(who=who)}",
        "",
        L["subtitle"].format(date=today.isoformat()),
        "",
    ]

    def section(title: str, items: list[str], empty: str) -> None:
        lines.append(f"## {title}")
        lines.append("")
        lines.extend(items if items else [empty])
        lines.append("")

    section(L["overdue_heading"], [task_line(t) for t in overdue], L["overdue_empty"])
    section(L["today_heading"], [task_line(t) for t in due_today], L["today_empty"])

    section(
        L["inplay_heading"],
        [task_line(t) for t in in_play],
        L["inplay_empty"],
    )

    section(
        L["blocked_heading"],
        [f"- **{entity}** · {row}" for entity, row in blocked_rows()]
        + blocked_tasks(all_tasks),
        L["blocked_empty"],
    )

    section(
        L["external_heading"],
        [
            L["external_line"].format(
                id=t["id"],
                title=t["title"],
                status=f"{STATUS_ICON[t['status']]} {t['status']}",
            )
            for t in touched_from_outside(all_tasks)
        ],
        L["external_empty"],
    )

    section(
        L["silence_heading"].format(days=threshold),
        [L["silence_line"].format(name=name, age=age)
         for name, age in silent_entities(today, threshold)],
        L["silence_empty"],
    )

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(L["footer"].format(stamp=stamp))
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the task report for one person.")
    # No `choices=` and no literal default: the owner list is the contract's, not the
    # generator's. Validated against it below, once the contract is loaded.
    parser.add_argument(
        "--owner",
        default=None,
        help="whose report to build, or `all`; shared tasks always come along "
             "(default: `default_owner` from the schema, else `all`)",
    )
    parser.add_argument("--date", help="report date, ISO YYYY-MM-DD (default: today)")
    parser.add_argument("--silence-days", type=int, default=DEFAULT_SILENCE_DAYS)
    parser.add_argument("--write", action="store_true",
                        help="also write _today.md next to the registry (gitignored, for diffing)")
    parser.add_argument("--root", default=None,
                        help="repository root (default: git toplevel, else cwd)")
    parser.add_argument("--schema", default=None,
                        help="path to schema.yaml (default: the one next to this script)")
    args = parser.parse_args()

    regen.configure(args.root, args.schema)

    owner = args.owner or regen.SCHEMA.get("default_owner") or "all"
    allowed = list(regen.OWNERS) + ["all"]
    if owner not in allowed:
        parser.error(f"--owner {owner!r} is not one of {allowed}")

    # Windows consoles default to a legacy codepage that cannot encode ⚪/🟡 — the report
    # would die on its own status icons. Same guard as tools/context-lint/lint.py.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    today = date.fromisoformat(args.date) if args.date else date.today()
    content = render(today, args.silence_days, owner)

    print(content)
    if args.write:
        today_file().write_text(content, encoding="utf-8")
        print(regen.SCHEMA["report_labels"]["written_to"].format(path=rel(today_file())),
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
