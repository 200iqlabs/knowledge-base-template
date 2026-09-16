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

Scope is read from the linter's config and applied the way check #23 applies it — the
same scanned roots, the same `verified_scope` directories and exclusions, the same
`.md`-only rule — so the two cannot disagree about which files carry settled facts. A
header this command could write somewhere the check never looks would be a confirmation
nothing ever validates. Identity comes from the task schema's `default_owner`, so the id
is the same one the task registry uses and nobody retypes it per invocation.

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


def inside(path: str, base: str) -> bool:
    """True when `path` is `base` itself or sits beneath it, by path component.

    Compared on the real paths, so neither `..` in the argument nor a symlink pointing
    out of the checkout can reach a file the scope rules were never applied to.
    """
    p, b = os.path.normcase(path), os.path.normcase(base)
    return p == b or p.startswith(b.rstrip(os.sep) + os.sep)


def split_frontmatter(text: str) -> tuple[str, list[str], list[str], str]:
    """Return (shape, frontmatter lines, body lines, eol).

    `shape` is "none" when the file opens with no fence, "block" when a frontmatter
    block opens and closes, and "unterminated" when a fence opens and never closes.
    The third case gets its own answer rather than being folded into the first: a file
    that opens `---` and never closes it is either a broken header or a document opening
    on a thematic break, and writing a fresh header above it would leave two openings
    with nothing to say which was meant.

    Lines keep their endings, so a file written with CRLF stays CRLF and the diff shows
    the entry that was added rather than the whole file.
    """
    lines = text.splitlines(keepends=True)
    eol = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
    if not lines or lines[0].rstrip("\r\n") != "---":
        return "none", [], lines, eol
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            return "block", lines[1:i], lines[i + 1:], eol
    return "unterminated", [], lines, eol


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
    # Every element has to be a mapping before anything is appended: a malformed one
    # (`verified: [null]`, a bare string in the list) is exactly what check #23 reports,
    # and this command does not repair those — it stops and says so, because rewriting
    # somebody's malformed entry is a judgement that belongs to the person.
    for entry in entries:
        if not isinstance(entry, dict):
            fail(f"an existing `verified` entry is {entry!r} and not a mapping with `by` "
                 "and `at` — the file was left untouched; check #23 reports this shape "
                 "and repairing it is the person's call, not this script's")
    return entries, start, end - start


def emit_value(value) -> str:
    """Serialise one value the way YAML will read it back, on a single line.

    Formatting an existing entry back with an f-string looked harmless while every value
    was an id and a timestamp, but the contract allows extra keys, and `note: checked #1`
    written unquoted is truncated at the `#` the next time the file is read. Re-emitting
    an entry must not change what it says, so the emitter decides the quoting.

    A datetime is written as its own ISO form rather than through the emitter: it is
    always plain-safe, it reads back as the same instant, and it keeps the canonical
    `at: 2026-03-04T11:20:00+01:00` that the doctrine shows.
    """
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    for style in (None, '"'):
        try:
            dumped = yaml.safe_dump(value, default_flow_style=True, default_style=style,
                                    allow_unicode=True, sort_keys=False,
                                    width=10 ** 6).strip()
        except yaml.YAMLError as exc:
            fail(f"an existing `verified` entry holds a value this script cannot write "
                 f"back unchanged ({value!r}): {exc}")
        if dumped.endswith("\n..."):  # the document-end marker PyYAML adds to bare scalars
            dumped = dumped[: -len("\n...")].strip()
        if "\n" not in dumped:
            return dumped
    fail(f"an existing `verified` entry holds a value with no single-line YAML form "
         f"({value!r}) — the file was left untouched")


def render(entries: list[dict], eol: str) -> list[str]:
    out = [f"verified:{eol}"]
    for entry in entries:
        keys = ["by", "at"] + [k for k in entry if k not in ("by", "at")]
        first = True
        for key in keys:
            if key not in entry:
                continue
            prefix = "  - " if first else "    "
            out.append(f"{prefix}{key}: {emit_value(entry[key])}{eol}")
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

    root = os.path.realpath(repo_root())
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

    # `join` keeps an absolute argument as given, and `realpath` resolves `..` and any
    # symlink before the scope rules are applied to the result — the rules read path
    # segments, so a path that escapes the checkout could otherwise satisfy them while
    # naming a file in another repository entirely.
    target = os.path.realpath(os.path.join(root, args.path))
    if not inside(target, root):
        fail(f"{args.path} resolves to {target}, outside this repository — the path is "
             "read relative to the repository root and a confirmation is a fact about a "
             "file in this checkout")
    if not os.path.isfile(target):
        fail(f"{args.path} does not exist")
    relpath = os.path.relpath(target, root).replace(os.sep, "/")

    # The same bases check #23 walks. Without this the command would happily write a
    # header into a tree the check never visits, and the confirmation would be validated
    # by nothing.
    bases = [str(r.get("path") or "").replace("\\", "/").strip("/")
             for r in (config.get("scan_roots") or []) + (config.get("file_scopes") or [])]
    bases = sorted({b for b in bases if b})
    excludes = [str(p).replace("\\", "/").strip("/")
                for p, reason in (vcfg.get("exclude") or {}).items()
                if str(reason or "").strip()]
    scope_help = (f"in scope: an `.md` file in any {' or '.join(sorted(dirs))} directory "
                  f"under {', '.join(bases) if bases else '(no scanned root configured)'}"
                  + (f"; excluded: {', '.join(sorted(excludes))}" if excludes else ""))
    if not any(relpath == b or relpath.startswith(b + "/") for b in bases):
        fail(f"{relpath} is outside the confirmable scope — {scope_help}. Check #23 walks "
             "the scanned roots only, so a header written outside them would never be "
             "validated by anything")
    if any(part.startswith(".") for part in relpath.split("/")):
        fail(f"{relpath} sits on a hidden path, which the linter's walk skips — "
             f"{scope_help}")
    if not relpath.endswith(".md"):
        fail(f"{relpath} is not a Markdown file — {scope_help}. Check #23 reads `.md` "
             "only, and a YAML header bolted onto anything else is both unchecked and "
             "quite possibly corrupting")
    if any(relpath == e or relpath.startswith(e + "/") for e in excludes):
        fail(f"{relpath} is outside the confirmable scope — {scope_help}")
    if not any(seg in dirs for seg in relpath.split("/")[:-1]):
        fail(f"{relpath} is outside the confirmable scope — {scope_help}. Material that "
             "is put down (archive/, communication/, output/, inbox/) records what "
             "happened and is not confirmed; tasks/ carries its own header contract")

    with io.open(target, "r", encoding="utf-8", newline="") as fh:
        text = fh.read()
    shape, fm_lines, body_lines, eol = split_frontmatter(text)
    if shape == "unterminated":
        fail(f"{relpath} opens a `---` fence it never closes, so it carries no header "
             "this command can extend — the file was left untouched")
    entries, start, span = existing_entries(fm_lines)

    actor = f"human:{owner}"
    stamp = _dt.datetime.now().astimezone().replace(microsecond=0)
    entries.append({"by": actor, "at": stamp})
    block = render(entries, eol)

    if start == -1:
        fm_lines = fm_lines + block
    else:
        fm_lines = fm_lines[:start] + block + fm_lines[start + span:]

    if shape == "none":
        # No frontmatter at all: open one, and keep the body exactly where it was.
        new = [f"---{eol}"] + fm_lines + [f"---{eol}", eol] + body_lines
    else:
        new = [f"---{eol}"] + fm_lines + [f"---{eol}"] + body_lines

    # Compare-and-swap: the field is append-only, and a second session confirming the
    # same file between the read above and the write below would otherwise have its entry
    # dropped by this write, silently and with no way to notice afterwards. Re-reading
    # turns that lost update into a refusal the person can act on.
    with io.open(target, "r", encoding="utf-8", newline="") as fh:
        if fh.read() != text:
            fail(f"{relpath} changed on disk while this confirmation was being prepared, "
                 "so nothing was written — read the file and run the command again")
    with io.open(target, "w", encoding="utf-8", newline="") as fh:
        fh.writelines(new)

    print(f"confirmed {relpath}")
    print(f"  by: {actor}")
    print(f"  at: {stamp.isoformat()}")
    print(f"  entries on this file: {len(entries)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
