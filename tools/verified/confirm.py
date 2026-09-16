#!/usr/bin/env python3
"""Append a human confirmation entry to a file's `verified` field.

    python template/tools/verified/confirm.py <path> \
        --config tools/context-lint/config.yaml \
        --schema tools/tasks/schema.yaml

The command behind this script is the ONLY way a `human:` entry is ever written. That
is the whole design: the proof a confirmation happened is the act of invoking it, which
belongs to the person, lands in the transcript, and needs no agent deciding how far
something said in conversation reached. An agent that writes the entry any other way has
not saved anybody a step — it has produced a field that measures exactly what its absence
measures, while looking like evidence. See the core CLAUDE.md, "Trust".

Scope comes from `verified_scope` in the linter's config, so the script and check #23
cannot disagree about which files carry settled facts. Identity comes from the task
schema's `default_owner`, so the id is the same one the task registry uses and nobody
retypes it per invocation.

The file is rewritten by editing the frontmatter block alone: every other byte, including
comments and key order, survives untouched. Only the `verified` block is re-emitted, and
only in one canonical shape.
"""
import argparse
import datetime as _dt
import io
import os
import subprocess
import sys

try:
    import yaml
except ImportError:
    sys.stderr.write("confirm: missing dependency PyYAML.\n  Install with:  pip install pyyaml\n")
    sys.exit(2)


def repo_root() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True)
        return os.path.abspath(out.stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        return os.path.abspath(os.getcwd())


def load_yaml(path: str) -> dict:
    with io.open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def fail(message: str) -> None:
    sys.stderr.write(f"confirm: {message}\n")
    sys.exit(1)


def split_frontmatter(text: str) -> tuple[list[str], list[str], str]:
    """Return (frontmatter lines, body lines, eol) — frontmatter empty when absent.

    Lines keep their endings, so a file written with CRLF stays CRLF and the diff shows
    the entry that was added rather than the whole file.
    """
    lines = text.splitlines(keepends=True)
    eol = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
    if not lines or lines[0].rstrip("\r\n") != "---":
        return [], lines, eol
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            return lines[1:i], lines[i + 1:], eol
    # An opening fence with no closing one is not frontmatter; treat the file as body.
    return [], lines, eol


def existing_entries(fm_lines: list[str]) -> tuple[list[dict], int, int]:
    """Parse the `verified` block. Returns (entries, first line index, line count).

    A single entry may be written as a bare mapping with no list dash — the spec allows
    it and reading it as a one-item list is the whole point, because the alternative is
    silently dropping somebody's confirmation when the second one is added.
    """
    start = None
    for i, line in enumerate(fm_lines):
        # Top-level key only: an indented `verified:` belongs to some other mapping, and
        # rewriting that one would hang a confirmation on the wrong thing.
        if line.rstrip("\r\n").startswith("verified:"):
            start = i
            break
    if start is None:
        return [], -1, 0
    end = start + 1
    while end < len(fm_lines):
        line = fm_lines[end]
        if line.strip() and not line.startswith((" ", "\t")):
            break
        end += 1
    block = "".join(fm_lines[start:end])
    try:
        parsed = (yaml.safe_load(block) or {}).get("verified")
    except yaml.YAMLError as exc:
        fail(f"the existing `verified` block is not valid YAML and was left alone: {exc}")
    if parsed is None:
        entries = []
    elif isinstance(parsed, dict):
        entries = [parsed]
    elif isinstance(parsed, list):
        entries = list(parsed)
    else:
        fail(f"`verified` holds {parsed!r}, which is neither an entry nor a list of them")
    return entries, start, end - start


def render(entries: list[dict], eol: str) -> list[str]:
    out = [f"verified:{eol}"]
    for entry in entries:
        keys = ["by", "at"] + [k for k in entry if k not in ("by", "at")]
        first = True
        for key in keys:
            if key not in entry:
                continue
            value = entry[key]
            if isinstance(value, (_dt.datetime, _dt.date)):
                value = value.isoformat()
            prefix = "  - " if first else "    "
            out.append(f"{prefix}{key}: {value}{eol}")
            first = False
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Record that a person confirmed the contents of a file.")
    ap.add_argument("path", help="file to confirm, relative to the repository root")
    ap.add_argument("--config", required=True,
                    help="linter config.yaml — the source of verified_scope")
    ap.add_argument("--schema", required=True,
                    help="task schema.yaml — the source of the operator's id")
    ap.add_argument("--owner", default=None,
                    help="owner id from the schema; defaults to its default_owner")
    args = ap.parse_args(argv)

    root = repo_root()
    config = load_yaml(args.config)
    schema = load_yaml(args.schema)

    vcfg = config.get("verified_scope") or {}
    dirs = list(vcfg.get("dirs") or [])
    if not dirs:
        fail(f"{args.config} declares no verified_scope.dirs — this repository does not "
             "record confirmations, and inventing a scope here is not this script's call")

    owner = args.owner or schema.get("default_owner")
    owners = schema.get("owners") or []
    if not owner:
        fail(f"{args.schema} has no default_owner and --owner was not given")
    if owners and owner not in owners:
        fail(f"`{owner}` is not one of the schema's owners {owners} — an id nobody else "
             "uses is an id that means nothing to the next reader")

    target = os.path.abspath(os.path.join(root, args.path)) if not os.path.isabs(args.path) \
        else os.path.abspath(args.path)
    if not os.path.isfile(target):
        fail(f"{args.path} does not exist")
    relpath = os.path.relpath(target, root).replace(os.sep, "/")

    excludes = [str(p).replace("\\", "/").strip("/")
                for p, reason in (vcfg.get("exclude") or {}).items()
                if str(reason or "").strip()]
    scope_help = (f"in scope: any {' or '.join(sorted(dirs))} directory of an entity"
                  + (f"; excluded: {', '.join(sorted(excludes))}" if excludes else ""))
    if any(relpath == e or relpath.startswith(e + "/") for e in excludes):
        fail(f"{relpath} is outside the confirmable scope — {scope_help}")
    if not any(seg in dirs for seg in relpath.split("/")[:-1]):
        fail(f"{relpath} is outside the confirmable scope — {scope_help}. Material that "
             "is put down (archive/, communication/, output/, inbox/) records what "
             "happened and is not confirmed; tasks/ carries its own header contract")

    with io.open(target, "r", encoding="utf-8", newline="") as fh:
        text = fh.read()
    fm_lines, body_lines, eol = split_frontmatter(text)
    entries, start, span = existing_entries(fm_lines)

    actor = f"human:{owner}"
    stamp = _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()
    entries.append({"by": actor, "at": stamp})
    block = render(entries, eol)

    if start == -1:
        fm_lines = fm_lines + block
    else:
        fm_lines = fm_lines[:start] + block + fm_lines[start + span:]

    if not text.startswith("---"):
        # No frontmatter at all: open one, and keep the body exactly where it was.
        new = [f"---{eol}"] + fm_lines + [f"---{eol}", eol] + body_lines
    else:
        new = [f"---{eol}"] + fm_lines + [f"---{eol}"] + body_lines

    with io.open(target, "w", encoding="utf-8", newline="") as fh:
        fh.writelines(new)

    print(f"confirmed {relpath}")
    print(f"  by: {actor}")
    print(f"  at: {stamp}")
    print(f"  entries on this file: {len(entries)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
