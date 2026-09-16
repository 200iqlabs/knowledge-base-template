#!/usr/bin/env python3
"""Repair links broken by moving a file — above all, by archiving a task.

Archiving a task is a `git mv` into `tasks/_archive/`, one directory below where the task
lived. That breaks two kinds of link, and both are mechanical:

  inbound   every link that pointed at the task. The part in front of `tasks/` stays the
            same wherever the link was written from, because `_archive/` sits inside the
            very same `tasks/` directory: only `_archive/` has to go in before the file
            name. It is a find-and-replace after all — once the link is proved to point
            at the moved file, which is what resolving it against its own directory does.
  outbound  every relative link inside the moved task. The task now sits one directory
            deeper, so a target that resolved from the old directory needs one more `../`.

A task moved back out of the archive is the inbound case in reverse.

A repair is written only when the file system proves it: the link is dead as written, and
the corrected target exists. Nothing else is touched — no prose, no link text, no link
that still resolves; no file is created, moved or deleted. A second run is a no-op.

Every other dead link is reported, with a lead when one is cheap to compute, and left to
whoever repairs it: which file an author meant is judgement, and a script that guesses
turns a dead link into a wrong live one. Once somebody has decided, `--retarget OLD NEW`
carries the decision out with the same guarantees — only dead links whose target is OLD,
only where NEW exists. NEW may be a file's address at a commit (`…/blob/<commit>/<path>`),
the last version of something deleted on purpose; it exists when the local history shows
that commit holding that file. The procedure for deciding is the `relink` skill.

Not checked, because the repository cannot know the answer: URLs; targets starting with
`/`, which are absolute on whatever serves the page — a code host or a website, and a
knowledge base holds drafts for both; targets outside the repository; targets git is
told to ignore, which exist on the station that produced them and nowhere else;
placeholders (`<name>`, `{id}`); and files named `_template*`, whose links resolve only
once the template is copied to where it will live. Nor are files marked as build output
(`linguist-generated` in `.gitattributes`): a build rewrites them from their source, over
any repair, and writes their links for wherever they get published — so the source is
what gets checked. Code — fenced blocks and inline spans, a span wrapped onto the next
line included — is an example, not a link.

Several sessions may share one working tree, so a file with somebody's uncommitted
changes is never edited — it is listed as skipped. `--own PATH` claims a subtree as the
caller's own; a file being moved right now (a staged rename) always is.

Usage:
    python tools/tasks/relink.py                          # report, write nothing
    python tools/tasks/relink.py --apply                  # repair what is proved
    python tools/tasks/relink.py context/projects/EXAMPLE --json
    python tools/tasks/relink.py data/runs --retarget _index.md ../_index.md
Exit: 0 when no dead link is left, 1 when some are, 2 on a usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import unquote

# Material recording what happened as it happened — raw input, sent text, sealed
# artefacts. Its links are part of that record, so "repairing" one would rewrite the
# record. Everything else is a knowledge file whose links are pointers, and repairing a
# pointer changes no content. That is why `tasks/_archive/` and `status_archive.md` are
# not listed: they are unread by default, and a reading rule is about context volume,
# not about immutability.
SEALED_DIRS = frozenset({"archive", "communication", "output", "inbox"})
TASKS_DIR = "tasks"
ARCHIVE_DIR = "_archive"
REPAIRABLE = ("archived", "unarchived", "moved")

INLINE_RE = re.compile(r"\]\(\s*(<[^>\n]*>|[^)\s]+)(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
REFDEF_RE = re.compile(r"^ {0,3}\[(?!\^)[^\]]+\]:[ \t]*(<[^>\n]*>|\S+)")
SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
# a code span may wrap onto the next line of its paragraph, never past a blank line
CODE_SPAN_RE = re.compile(r"(`+)((?:(?!\n[ \t]*\n)[\s\S])+?)\1")


@dataclass
class Link:
    path: str                    # repo-relative file holding the link
    line: int                    # 1-based
    target: str                  # the target as written, without angle brackets
    kind: str                    # archived | unarchived | moved | retargeted | dead
    fix: str | None = None       # the corrected target, proved to resolve
    hint: str | None = None      # dead only: a lead for whoever repairs it by hand
    skipped: str | None = None   # why a repair was not written
    col: int = field(default=0, repr=False)   # span of the target inside the line
    end: int = field(default=0, repr=False)

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in
                ("path", "line", "target", "kind", "fix", "hint", "skipped")}


# --- the repository ------------------------------------------------------------

# Windows gives a console to any console application started by a process that has none,
# and this module is read by one that has none on purpose — the graph's background build.
# Every `git` call then opens a window, and on a host whose default terminal is Windows
# Terminal that is a tab per call, flashing over whatever the user was doing. This flag
# is what suppresses it. An empty mapping on POSIX rather than `creationflags=0`, because
# subprocess rejects the argument there outright. graph.py carries the same two lines and
# the reasoning behind them: either tool runs when the other is absent, so neither can
# import the constant from the other.
NO_WINDOW = {"creationflags": 0x08000000} if os.name == "nt" else {}


def repo_root(explicit: str | None) -> str:
    if explicit:
        return os.path.abspath(explicit)
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, **NO_WINDOW)
    return out.stdout.strip() if out.returncode == 0 else os.getcwd()


def git(root: str, *args: str, stdin: str | None = None) -> str:
    out = subprocess.run(["git", *args], cwd=root, input=stdin,
                         capture_output=True, text=True, **NO_WINDOW)
    # check-ignore answers 1 when nothing matched; its stdout is still the answer
    return out.stdout if out.returncode in (0, 1) else ""


def tracked_files(root: str) -> list[str]:
    """Files git tracks. Untracked ones may be somebody's work in progress."""
    listed = [p for p in git(root, "ls-files", "-z").split("\0") if p]
    if listed:
        return listed
    files = []                       # not a git checkout: walk, skipping hidden dirs
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        files += [os.path.relpath(os.path.join(dirpath, fn), root).replace(os.sep, "/")
                  for fn in filenames]
    return files


def ignored(root: str, rels: set[str]) -> set[str]:
    """The paths git is told to ignore."""
    if not rels:
        return set()
    out = git(root, "check-ignore", "--stdin", "-z", stdin="\0".join(sorted(rels)))
    return {p for p in out.split("\0") if p}


def generated(root: str, rels: list[str]) -> set[str]:
    """The files `.gitattributes` marks `linguist-generated` — build output."""
    if not rels:
        return set()
    out = git(root, "check-attr", "--stdin", "-z", "linguist-generated",
              stdin="\0".join(rels)).split("\0")
    # answered in triples: path, attribute, value
    return {out[i] for i in range(0, len(out) - 2, 3) if out[i + 2] in ("set", "true")}


def uncommitted(root: str) -> set[str]:
    """Files with uncommitted changes, staged or not — except the destinations of staged
    renames: a file being moved right now belongs to whoever is moving it."""
    changed = {p for p in git(root, "diff", "--name-only", "-z").split("\0") if p}
    tokens = [t for t in git(root, "diff", "--cached", "--name-status", "-M", "-z")
              .split("\0") if t]
    moving, i = set(), 0
    while i < len(tokens):
        status = tokens[i]
        if status[:1] in ("R", "C"):
            changed.add(tokens[i + 1])
            (moving if status[0] == "R" else changed).add(tokens[i + 2])
            i += 3
        else:
            changed.add(tokens[i + 1])
            i += 2
    return changed - moving


def sealed(rel: str) -> bool:
    return any(part in SEALED_DIRS for part in rel.split("/")[:-1])


def in_task_archive(rel: str) -> bool:
    parts = rel.split("/")
    return len(parts) >= 3 and parts[-2] == ARCHIVE_DIR and parts[-3] == TASKS_DIR


def unread_by_default(rel: str) -> bool:
    """Closed history nobody loads by default: archived tasks, aged-out status rows."""
    return in_task_archive(rel) or rel.rsplit("/", 1)[-1] == "status_archive.md"


def within(rel: str, prefixes: list[str]) -> bool:
    return any(p in ("", ".") or rel == p or rel.startswith(p + "/") for p in prefixes)


def to_repo_rel(root: str, path: str) -> str:
    return os.path.relpath(os.path.abspath(path), root).replace(os.sep, "/")


def read(root: str, rel: str) -> str | None:
    try:
        with open(os.path.join(root, rel), encoding="utf-8", newline="") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


# --- links -----------------------------------------------------------------------

def iter_targets(text: str):
    """Yield (line_no, start, end, target) for every link target outside code."""
    lines = text.split("\n")
    fenced, fence = set(), None
    for no, line in enumerate(lines):
        opened = FENCE_RE.match(line)
        if opened:
            marker = opened.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            fenced.add(no)
        elif fence is not None:
            fenced.add(no)
    # Mask code spans with blanks, keeping the newlines: `[x](y)` shown as an example is
    # not a link, a span may wrap onto the next line of its paragraph, and equal width
    # keeps every column valid in the original line.
    prose = "\n".join("" if no in fenced else line for no, line in enumerate(lines))
    masked = CODE_SPAN_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), prose).split("\n")
    for no, line in enumerate(lines):
        if no in fenced:
            continue
        for rx in (INLINE_RE, REFDEF_RE):
            for m in rx.finditer(masked[no]):
                start, end = m.span(1)
                if masked[no][start] == "<":
                    start, end = start + 1, end - 1
                yield no + 1, start, end, line[start:end]


def split_target(target: str) -> tuple[str, str]:
    """('path', '#anchor' / '?query') — the suffix is carried over untouched."""
    for i, ch in enumerate(target):
        if ch in "#?":
            return target[:i], target[i:]
    return target, ""


def local_path(target: str) -> str | None:
    """The path part of a target the repository can check, else None."""
    if not target or target.startswith(("#", "/")) or SCHEME_RE.match(target):
        return None
    # `<name>` and `{id}` are placeholders waiting to be filled in, not paths
    if any(ch in target for ch in "<>{}"):
        return None
    return split_target(target)[0] or None


def is_template(rel: str) -> bool:
    """A template's links resolve only once it is copied to where it will live."""
    return rel.rsplit("/", 1)[-1].lower().startswith("_template")


def norm(path: str) -> str:
    return os.path.normpath(path).replace(os.sep, "/")


def readings(src: str, path: str) -> list[str]:
    """Repo-relative readings of a link path: from the file's own directory first, then
    from the repository root — prompts written for another session often carry root
    paths. A reading that leaves the repository is dropped: it cannot be checked here."""
    p = unquote(path)
    raw = [os.path.join(os.path.dirname(src), p)]
    if not p.startswith("."):
        raw.append(p)
    return [n for n in map(norm, raw) if n != ".." and not n.startswith("../")]


def exists(root: str, rel: str) -> bool:
    return os.path.exists(os.path.join(root, rel))


def resolves(root: str, src: str, path: str) -> bool:
    return any(exists(root, r) for r in readings(src, path))


def into_archive(root: str, src: str, path: str) -> str | None:
    """`path` with `_archive/` before the file name, when that is where the task went."""
    head, sep, base = path.rpartition("/")
    for r in readings(src, path):
        parent = os.path.dirname(r)
        if os.path.basename(parent) == TASKS_DIR \
                and exists(root, f"{parent}/{ARCHIVE_DIR}/{os.path.basename(r)}"):
            fixed = f"{head}{sep}{ARCHIVE_DIR}/{base}"
            if resolves(root, src, fixed):
                return fixed
    return None


def repair(root: str, src: str, path: str) -> tuple[str, str] | None:
    """(kind, corrected path) when the file system proves one, else None."""
    # Outbound: the file holding the link was itself moved one directory down — and the
    # task it links to may have been archived after it, which needs both repairs at once.
    if in_task_archive(src):
        deeper = "../" + (path[2:] if path.startswith("./") else path)
        if resolves(root, src, deeper):
            return "moved", deeper
        both = into_archive(root, src, deeper)
        if both:
            return "moved", both
    fixed = into_archive(root, src, path)
    if fixed:
        return "archived", fixed
    head, sep, base = path.rpartition("/")
    for r in readings(src, path):
        parent = os.path.dirname(r)
        name = os.path.basename(r)
        if os.path.basename(parent) == ARCHIVE_DIR \
                and os.path.basename(os.path.dirname(parent)) == TASKS_DIR \
                and exists(root, f"{os.path.dirname(parent)}/{name}"):
            outer = head.rpartition("/")
            if outer[2] == ARCHIVE_DIR:
                fixed = f"{outer[0]}{outer[1]}{base}"
                if resolves(root, src, fixed):
                    return "unarchived", fixed
    return None


def hint(root: str, src: str, path: str, names: dict[str, list[str]]) -> str:
    """A cheap lead for a dead link — never applied, only shown."""
    deeper = "../" + (path[2:] if path.startswith("./") else path)
    shallower = path[3:] if path.startswith("../") else None
    found = [(p, how) for p, how in ((deeper, "one `../` more"),
                                     (shallower, "one `../` fewer"))
             if p and resolves(root, src, p)]
    if len(found) == 1:
        return f"`{found[0][0]}` resolves ({found[0][1]})"
    base = os.path.basename(unquote(path.rstrip("/")))
    same = names.get(base, [])
    if len(same) == 1:
        return f"the only file named `{base}`: " \
               f"`{os.path.relpath(same[0], os.path.dirname(src) or '.')}`"
    if same:
        return f"{len(same)} files named `{base}` — pick by content, not by name"
    return f"no tracked file named `{base}` — check git history for a rename or deletion"


def scan(root: str, paths: list[str] | None = None) -> list[Link]:
    """Every dead link in tracked Markdown outside sealed material and build output,
    classified."""
    files = tracked_files(root)
    names: dict[str, list[str]] = defaultdict(list)
    for f in files:
        names[f.rsplit("/", 1)[-1]].append(f)
    docs = [rel for rel in files if rel.endswith(".md") and not sealed(rel)
            and not is_template(rel) and not (paths and not within(rel, paths))]
    built = generated(root, docs)
    candidates = []
    for rel in docs:
        if rel in built:
            continue                         # build output: its source is checked
        text = read(root, rel)
        if text is None:
            continue
        for no, start, end, target in iter_targets(text):
            path = local_path(target)
            if path is None:
                continue
            reads = readings(rel, path)
            if not reads or any(exists(root, r) for r in reads):
                continue                     # outside the repository, or alive
            candidates.append((rel, no, start, end, target, path, reads,
                               repair(root, rel, path)))
    # A target git is told to ignore is kept out of version control on purpose — present
    # on the station that produced it, absent everywhere else — so its absence here does
    # not make the link dead. A directory is asked about with its trailing slash, or a
    # directory-only pattern (`**/runs/`) cannot match one that is absent from this checkout.
    asked = {r + ("/" if c[5].endswith("/") else "") for c in candidates if not c[7]
             for r in c[6]}
    skip = {p.rstrip("/") for p in ignored(root, asked)}
    links = []
    for rel, no, start, end, target, path, reads, found in candidates:
        suffix = split_target(target)[1]
        if found:
            link = Link(rel, no, target, found[0], fix=found[1] + suffix)
        elif any(r in skip for r in reads):
            continue
        else:
            link = Link(rel, no, target, "dead", hint=hint(root, rel, path, names))
        link.col, link.end = start, end
        links.append(link)
    return links


# A file's address at one commit, the way a code host shows history:
# `https://<host>/<owner>/<repo>/blob/<commit>/<path>`.
HISTORY_URL_RE = re.compile(r"^https?://\S+?/blob/([0-9a-f]{7,40})/([^#?\s]+)$")


def retarget(root: str, links: list[Link], pairs: list[tuple[str, str]]) -> None:
    """Point dead links whose target is OLD at NEW — a decision made by whoever runs it,
    carried out and checked here. NEW is read the way a link is (from the linking file,
    then from the repository root) and written relative to the linking file. NEW may
    also be a file's address at a commit — the last version of something deleted — and
    is written as given once the local history shows that commit holding that file."""
    wanted = dict(pairs)
    for link in links:
        path, suffix = split_target(link.target)
        if link.kind != "dead" or path not in wanted:
            continue
        new, here = wanted[path], os.path.dirname(link.path)
        local = norm(os.path.join(here, unquote(new)))
        at = HISTORY_URL_RE.match(new)
        if at:
            if git(root, "cat-file", "-t", f"{at[1]}:{unquote(at[2])}").strip() != "blob":
                link.skipped = f"--retarget: commit {at[1]} holds no `{unquote(at[2])}`"
                continue
            written = new
        elif exists(root, local) and not local.startswith("../"):
            written = new
        elif exists(root, norm(unquote(new))) and not norm(new).startswith("../"):
            written = os.path.relpath(norm(unquote(new)), here or ".").replace(os.sep, "/")
            written = written.replace(" ", "%20")
        else:
            link.skipped = (f"--retarget: `{new}` exists neither next to this file "
                            "nor from the repository root")
            continue
        link.kind, link.fix = "retargeted", written + suffix


def apply(root: str, links: list[Link], own: list[str],
          kinds: tuple[str, ...] = REPAIRABLE) -> None:
    """Write the repairs of the given kinds, file by file; mark the ones it may not."""
    dirty = uncommitted(root)
    by_file: dict[str, list[Link]] = defaultdict(list)
    for link in links:
        if link.kind in kinds and not link.skipped:
            by_file[link.path].append(link)
    for rel, items in by_file.items():
        if rel in dirty and not (own and within(rel, own)):
            for link in items:
                link.skipped = ("uncommitted changes in the file — claim it with --own "
                                "if they are yours, or re-run once they are committed")
            continue
        text = read(root, rel)
        if text is None:
            continue
        lines = text.split("\n")
        # Right to left inside a line, so an earlier column stays valid.
        for link in sorted(items, key=lambda x: (x.line, -x.col)):
            line = lines[link.line - 1]
            if line[link.col:link.end] != link.target:
                link.skipped = "the line changed since the scan"
                continue
            lines[link.line - 1] = line[:link.col] + link.fix + line[link.end:]
        new = "\n".join(lines)
        if new != text:
            with open(os.path.join(root, rel), "w", encoding="utf-8", newline="") as fh:
                fh.write(new)


# --- report ----------------------------------------------------------------------

def summary(links: list[Link], written: tuple[str, ...]) -> dict:
    out = {kind: sum(1 for l in links if l.kind == kind)
           for kind in REPAIRABLE + ("retargeted", "dead")}
    if written:
        mine = [l for l in links if l.kind in written or l.skipped]
        out["skipped"] = sum(1 for l in mine if l.skipped)
        out["written"] = len(mine) - out["skipped"]
    return out


def report(links: list[Link], written: tuple[str, ...], limit: int) -> None:
    for l in links:
        if l.kind in REPAIRABLE or l.kind == "retargeted":
            if l.skipped:
                tag, extra = "SKIPPED", l.skipped
            else:
                tag = "FIXED" if l.kind in written else "REPAIRABLE"
                extra = "-> " + l.fix
            print(f"{tag}\t{l.kind}\t{l.path}:{l.line}\t{l.target}\t{extra}")
    dead = [l for l in links if l.kind == "dead"]
    shown = dead if limit <= 0 else dead[:limit]
    for l in shown:
        print(f"DEAD\t-\t{l.path}:{l.line}\t{l.target}\t{l.skipped or l.hint}")
    if len(shown) < len(dead):
        print(f"... {len(dead) - len(shown)} more dead links "
              "(--limit 0 lists them all, --json gives every field)")
    s = summary(links, written)
    moved = f"archived {s['archived']}, unarchived {s['unarchived']}, moved {s['moved']}"
    if written:
        line = (f"relink: wrote {s['written']} ({moved}, retargeted {s['retargeted']}), "
                f"skipped {s['skipped']}, {s['dead']} dead link(s) need a decision")
    else:
        line = (f"relink: {s['archived'] + s['unarchived'] + s['moved']} repairable by "
                f"--apply ({moved}), {s['dead']} dead link(s) need a decision")
    print(line, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Repair links broken by moving files; report every other dead link.")
    ap.add_argument("paths", nargs="*", help="limit to these files or directories")
    ap.add_argument("--apply", action="store_true",
                    help="write the repairs the file system proves")
    ap.add_argument("--retarget", nargs=2, action="append", default=[],
                    metavar=("OLD", "NEW"),
                    help="point dead links whose target is OLD at NEW (repeatable)")
    ap.add_argument("--own", action="append", default=[], metavar="PATH",
                    help="uncommitted changes under PATH are yours: the tool may edit there")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--limit", type=int, default=30,
                    help="dead links listed in the text report (0 = all)")
    ap.add_argument("--root", default=None,
                    help="repository root (default: git toplevel, else cwd)")
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    root = repo_root(args.root)
    links = scan(root, [to_repo_rel(root, p) for p in args.paths] or None)
    written = (REPAIRABLE if args.apply else ()) + (("retargeted",) if args.retarget else ())
    if args.retarget:
        retarget(root, links, [tuple(p) for p in args.retarget])
    if written:
        apply(root, links, [to_repo_rel(root, p) for p in args.own], written)
    if args.json:
        print(json.dumps({"summary": summary(links, written),
                          "links": [l.as_dict() for l in links]},
                         ensure_ascii=False, indent=1))
    else:
        report(links, written, args.limit)
    left = [l for l in links if l.skipped or l.kind == "dead"
            or (l.kind in REPAIRABLE and not args.apply)]
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
