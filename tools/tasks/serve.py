#!/usr/bin/env python3
"""
Task registry view — a local, read-only HTTP surface over the task files.

Answers the one question the two existing surfaces do not: "what have I actually got,
and where is it". The generated index is a flat table of every live task; the report is
ephemeral and exists only while somebody is asking for it. This is a page you can leave
open, and an address every task is reachable at by its identifier.

Three properties are load-bearing and each is enforced in one place:

  read-only    Only GET and HEAD are answered; every other method is refused with 405.
               Nothing here opens a file for writing, and the process leaves no state
               file, cache or config behind when it stops.
  live         The registry is rescanned on every request, so a task written a minute
               ago and never committed is already in the view. There is no regeneration
               step for the reader to remember.
  loopback     The socket binds 127.0.0.1 and nothing else. The registry carries client
               names, amounts and the substance of commercial commitments; that binding
               is what makes it safe to ship no authentication at all.

The registry is read through regen.py, never re-implemented: collect_tasks() for the
list (live, validated, entity known) and scan_ids() for a single task (everything the
repository holds, archived tasks included — find_task_dirs() drops `_archive` from the
walk, so collect_tasks() alone could never resolve an archived identifier).

Chrome text is English, in this file, deliberately. The schema carries the text the
generators write INTO repository files, because that lands in the repo and its language
is the repository's business. Nothing here is written anywhere, so — like the console
messages in both generators — it has nothing to drift against.

Usage:
    python <path-to>/serve.py [--root ROOT] [--schema SCHEMA] [--port PORT]

    --root     Repository root the scan is anchored to.
               Default: `git rev-parse --show-toplevel`, else the working directory.
    --schema   Task contract to enforce. Default: the schema.yaml next to this script.
    --port     Port to listen on, on the loopback interface only. Default: 8765.

Starting a second time while one is already listening does not start a second process.
The bind settles whether the port is taken; a health endpoint settles by whom. A port
held by this view is reported as already serving, and a port held by anything else is
named as such — neither arrives as a traceback.
"""

import argparse
import html
import json
import re
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Imported as a module, not as `from regen import ...`: those names are bound by
# regen.configure() at run time, and a from-import would freeze whatever they held at
# import time — which is nothing. Same reason regen_today.py states in its own header.
import regen  # noqa: E402

# Rendering the body is what makes the address worth opening: a page showing the same
# source as the editor, only uneditable, gives nobody a reason to click. Task files use
# the whole of markdown - 641 table rows, 762 links and 6673 code spans across the
# registry - so this is a parser's job, not a few substitutions. Absent, the page falls
# back to the source and says why, rather than letting the reader conclude that raw
# markdown is the intended look.
try:
    import markdown as _markdown
    from markdown.treeprocessors import Treeprocessor as _Treeprocessor

    MARKDOWN_ERROR: str | None = None
except ImportError as _exc:  # pragma: no cover - depends on the machine, not the code
    _markdown = None
    _Treeprocessor = object
    MARKDOWN_ERROR = str(_exc)

# Schemes a link in a task file may carry. Everything else is defused, because
# Python-Markdown copies a destination into href verbatim - `javascript:` and `data:`
# included - and a task body is not always something we wrote: reports arrive from
# sessions in other repositories, and prose gets ingested from mail and transcripts.
SAFE_URL_SCHEMES = frozenset({"http", "https", "mailto"})

# A scheme is a name followed by a colon. No match means a relative path or a fragment,
# which is what task files actually use and which carries no scheme to abuse.
URL_SCHEME_RE = re.compile("^[A-Za-z][A-Za-z0-9+.-]*:")


class DefuseLinks(_Treeprocessor):
    """Blank out link and image destinations carrying a scheme we do not allow."""

    def run(self, root):
        for element in root.iter():
            for attribute in ("href", "src"):
                value = element.get(attribute)
                if value is None:
                    continue
                match = URL_SCHEME_RE.match(value.strip())
                if match and match.group(0)[:-1].lower() not in SAFE_URL_SCHEMES:
                    # Left visible on purpose: a link that silently stops working reads
                    # as a broken page, and the reader needs to know it was disarmed.
                    element.set(attribute, "#")
                    element.set("title", f"blocked link scheme: {match.group(0)}")

DEFAULT_PORT = 8765
HEALTH_PATH = "/healthz"

# Marker the pre-start probe recognises its own kind by. A port answering on the health
# path is not enough — something else may well answer there.
SERVICE_ID = "task-registry-view"

# regen binds module globals, so two requests scanning at once would interleave their
# error lists and their directory cache. The scan is milliseconds; serialising it costs
# nothing and removes the whole class of problem.
_scan_lock = threading.Lock()

# Bound by main(), read by the request handler.
ROOT: str | None = None
SCHEMA: str | None = None
PORT: int = DEFAULT_PORT


def configure_registry() -> None:
    """Rebind the registry for this request and drop the previous scan's complaints.

    configure() resets regen's tasks/ directory cache, which is what makes a
    long-running process see a task created after it started. Clearing `errors` matters
    just as much: the list is module-level and never trimmed, so by the third refresh a
    single broken file would be reported three times.
    """
    regen.configure(ROOT, SCHEMA)
    regen.errors.clear()


def load_live_tasks() -> tuple[list[dict], list[str]]:
    """Live tasks in regen's own order, plus whatever the scan complained about.

    A task that violates the contract is dropped by collect_tasks() and named in
    `errors`. Both halves are returned so the view can show the survivors and still say
    that something was skipped — a broken header is a thing to fix, not to hide.
    """
    with _scan_lock:
        configure_registry()
        tasks = regen.collect_tasks()
        problems = list(regen.errors)
    live = [t for t in tasks if t.get("status") in regen.LIVE_STATUSES]
    return sorted(live, key=regen.sort_key), problems


def resolve_id(task_id: str) -> list[Path]:
    """Every file claiming this identifier, archived ones included.

    scan_ids() returns a list per id rather than a single path, and the list is passed
    through unflattened on purpose: two files sharing an identifier is something the
    doctrine expects when several sessions work here at once, and a view that silently
    picked the first would be the reason nobody ever noticed.
    """
    with _scan_lock:
        configure_registry()
        ids = regen.scan_ids()
    wanted = task_id.strip().upper()
    for known, paths in ids.items():
        if known.upper() == wanted:
            return paths
    return []


def week_end(today: date) -> date:
    """The coming Sunday, or today when today is Sunday.

    "This week" ends where the week ends, not seven days from whenever you looked —
    otherwise the section quietly means something different every day of the week.
    """
    return today + timedelta(days=6 - today.weekday())


def partition(
    tasks: list[dict], today: date
) -> tuple[list[dict], list[dict], list[tuple[str, list[dict]]]]:
    """Split into overdue, due this week, and everything else grouped by entity.

    The axis is the due date. Priority is shown on the row but does not cut the list:
    a third of the registry carries `high`, so a field that broad separates nothing.
    """
    iso, horizon = today.isoformat(), week_end(today).isoformat()
    overdue, this_week, rest = [], [], []
    for t in tasks:
        due = str(t.get("due") or "")
        if due and due < iso:
            overdue.append(t)
        elif due and due <= horizon:
            this_week.append(t)
        else:
            rest.append(t)

    by_entity: dict[str, list[dict]] = {}
    for t in rest:
        by_entity.setdefault(t["entity"], []).append(t)
    # Biggest piles first: where work has collected is the thing worth seeing without
    # opening anything.
    groups = sorted(by_entity.items(), key=lambda kv: (-len(kv[1]), kv[0].lower()))
    return overdue, this_week, groups


# --------------------------------------------------------------------------- rendering

STYLE = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin: 0; padding: 2rem 1.25rem 4rem; font: 15px/1.55 ui-sans-serif, system-ui, sans-serif; }
main { max-width: 62rem; margin: 0 auto; }
h1 { font-size: 1.35rem; margin: 0 0 .25rem; }
h2 { font-size: 1rem; margin: 2rem 0 .5rem; text-transform: uppercase; letter-spacing: .06em; opacity: .7; }
.meta { opacity: .6; font-size: .85rem; margin: 0 0 1.5rem; }
a { color: inherit; }
input[type=search] { width: 100%; padding: .6rem .75rem; font: inherit; border-radius: .4rem;
  border: 1px solid rgba(128,128,128,.45); background: transparent; color: inherit; }
ul.tasks { list-style: none; margin: 0; padding: 0; }
li.task { padding: .4rem 0; border-bottom: 1px solid rgba(128,128,128,.18); }
li.task .id { font-family: ui-monospace, monospace; font-size: .85rem; opacity: .75; }
li.task .entity { font-weight: 600; }
li.task .tail { opacity: .6; font-size: .85rem; }
.empty { opacity: .55; font-style: italic; padding: .4rem 0; }
details.group { border-bottom: 1px solid rgba(128,128,128,.18); }
details.group > summary { cursor: pointer; padding: .45rem 0; font-weight: 600; }
details.group > summary .count { font-weight: 400; opacity: .6; font-size: .85rem; }
details.group > ul { padding-left: 1rem; }
.problems { border-left: 3px solid #c9702a; padding: .5rem .75rem; margin: 1.5rem 0; font-size: .85rem; }
.problems ul { margin: .35rem 0 0; padding-left: 1.1rem; }
table.fields { border-collapse: collapse; margin: 1rem 0; font-size: .9rem; }
table.fields th { text-align: left; padding: .2rem 1.25rem .2rem 0; opacity: .6; font-weight: 400; vertical-align: top; }
table.fields td { padding: .2rem 0; }
pre.body { white-space: pre-wrap; word-wrap: break-word; padding: 1rem; border-radius: .4rem;
  background: rgba(128,128,128,.12); font-size: .85rem; }
code.path { font-family: ui-monospace, monospace; font-size: .85rem; }
.prose { margin: 1.25rem 0; }
.prose > :first-child { margin-top: 0; }
.prose h1, .prose h2, .prose h3, .prose h4 { line-height: 1.3; margin: 1.6rem 0 .5rem;
  text-transform: none; letter-spacing: 0; opacity: 1; }
.prose h1 { font-size: 1.2rem; }
.prose h2 { font-size: 1.05rem; }
.prose h3, .prose h4 { font-size: .95rem; }
.prose p, .prose li { overflow-wrap: anywhere; }
.prose ul, .prose ol { padding-left: 1.35rem; }
.prose li { margin: .2rem 0; }
.prose a { text-decoration: underline; text-underline-offset: .15em; }
.prose code { font-family: ui-monospace, monospace; font-size: .85em;
  background: rgba(128,128,128,.15); padding: .1em .35em; border-radius: .25rem; }
.prose pre { padding: .85rem 1rem; border-radius: .4rem; background: rgba(128,128,128,.12);
  font-size: .85rem; overflow-x: auto; }
.prose pre code { background: none; padding: 0; }
.prose blockquote { margin: 1rem 0; padding: .1rem 0 .1rem 1rem;
  border-left: 3px solid rgba(128,128,128,.4); opacity: .85; }
/* A wide table scrolls inside its own box so the page itself never scrolls sideways. */
.prose table { display: block; overflow-x: auto; max-width: 100%; border-collapse: collapse;
  margin: 1rem 0; font-size: .9rem; }
.prose th, .prose td { border: 1px solid rgba(128,128,128,.3); padding: .35rem .6rem;
  text-align: left; vertical-align: top; }
.prose th { background: rgba(128,128,128,.1); }
.prose hr { border: 0; border-top: 1px solid rgba(128,128,128,.3); margin: 1.5rem 0; }
"""

SEARCH_JS = """
const box = document.getElementById('q');
if (box) {
  const rows = Array.from(document.querySelectorAll('[data-search]'));
  const groups = Array.from(document.querySelectorAll('details.group'));
  box.addEventListener('input', () => {
    const q = box.value.trim().toLowerCase();
    rows.forEach(r => { r.hidden = q !== '' && !r.dataset.search.includes(q); });
    groups.forEach(g => {
      const hits = Array.from(g.querySelectorAll('[data-search]')).filter(r => !r.hidden).length;
      // Filtering reaches into collapsed groups: a match nobody can see is not a match.
      g.hidden = q !== '' && hits === 0;
      g.open = q !== '' && hits > 0;
    });
  });
}
"""


def page(title: str, body: str) -> bytes:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{STYLE}</style></head>"
        f"<body><main>{body}</main><script>{SEARCH_JS}</script></body></html>"
    ).encode("utf-8")


def task_row(task: dict) -> str:
    """One task, with everything the search box has to be able to match on.

    The bare number goes into the haystack beside the full identifier: what a reader
    copies off the screen is `244`, not the prefix as well.
    """
    task_id = str(task.get("id", ""))
    number = task_id.rsplit("-", 1)[-1]
    title = str(task.get("title", ""))
    haystack = f"{task_id} {number} {title}".lower()
    icon = regen.STATUS_ICON.get(task.get("status"), "")
    owner = regen.OWNER_LABEL.get(task.get("owner"), task.get("owner"))
    due = f"due {task['due']}" if task.get("due") else "no due date"
    start = f"start {task['start']}, " if task.get("start") else ""
    return (
        f'<li class="task" data-search="{html.escape(haystack, quote=True)}">'
        f'{icon} <a class="id" href="/{html.escape(task_id)}">{html.escape(task_id)}</a> '
        f'<span class="entity">{html.escape(str(task["entity"]))}</span> · '
        f"{html.escape(title)} "
        f'<span class="tail">— {html.escape(str(owner))}, '
        f'{html.escape(str(task.get("priority", "")))}'
        f", {html.escape(start + due)}</span></li>"
    )


def section(title: str, tasks: list[dict], empty: str) -> str:
    """A section is present even when it holds nothing — an absent section reads as a
    rendering fault, an empty one reads as good news."""
    if not tasks:
        return f'<h2>{html.escape(title)}</h2><p class="empty">{html.escape(empty)}</p>'
    rows = "".join(task_row(t) for t in tasks)
    return f'<h2>{html.escape(title)}</h2><ul class="tasks">{rows}</ul>'


def render_index(today: date) -> bytes:
    tasks, problems = load_live_tasks()
    overdue, this_week, groups = partition(tasks, today)

    parts = [
        "<h1>Task registry</h1>",
        f'<p class="meta">{len(tasks)} live · {today.isoformat()} · '
        f"{html.escape(str(regen.REPO_ROOT))}</p>",
        '<input type="search" id="q" placeholder="Filter by identifier or title" autofocus>',
    ]
    if problems:
        items = "".join(f"<li>{html.escape(p)}</li>" for p in problems)
        parts.append(
            f'<div class="problems"><strong>{len(problems)} file(s) skipped — '
            f"they do not meet the task contract:</strong><ul>{items}</ul></div>"
        )
    parts.append(section("Overdue", overdue, "Nothing is past its due date."))
    parts.append(section("Due this week", this_week, "Nothing falls due before Sunday."))

    parts.append("<h2>Everything else, by entity</h2>")
    if not groups:
        parts.append('<p class="empty">No other live tasks.</p>')
    for entity, items in groups:
        rows = "".join(task_row(t) for t in items)
        parts.append(
            f'<details class="group"><summary>{html.escape(entity)} '
            f'<span class="count">{len(items)}</span></summary>'
            f'<ul class="tasks">{rows}</ul></details>'
        )
    return page("Task registry", "".join(parts))


def strip_frontmatter(content: str) -> str:
    """The file without its YAML header, split exactly as regen.parse_frontmatter splits it.

    Same rule, so what the page renders is the precise complement of the fields table
    above it. The header is dropped because that table already lists it, and because a
    renderer turns it into a horizontal rule followed by one glued paragraph - noise in
    the very place this page is meant to remove noise.
    """
    if not content.startswith("---"):
        return content
    parts = content.split("---", 2)
    return parts[2].lstrip() if len(parts) == 3 else content


def render_body(content: str) -> str:
    body = strip_frontmatter(content)
    if _markdown is None:
        return (
            '<p class="empty">Body shown as source: the markdown renderer is missing '
            f"({html.escape(MARKDOWN_ERROR or 'import failed')}). "
            "Install it with <code>pip install markdown</code>.</p>"
            f'<pre class="body">{html.escape(body)}</pre>'
        )
    # Python-Markdown copies raw HTML through untouched by default - no extension
    # setting changes that - so a `<script>` in a task file would run in the operator's
    # browser on this origin. Both handlers are deregistered, which makes such a tag
    # render as visible text rather than vanish: seeing `<script>` in a task is the
    # signal that something wrote it there.
    #
    # A fresh instance per call because Markdown carries per-conversion state and this
    # is a threading server; the cost is nothing against reading the file.
    renderer = _markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists"])
    renderer.preprocessors.deregister("html_block")
    renderer.inlinePatterns.deregister("html")
    renderer.treeprocessors.register(DefuseLinks(renderer), "defuse_links", 1)
    return '<div class="prose">' + renderer.convert(body) + "</div>"


def render_task(task_id: str, paths: list[Path]) -> bytes:
    parts = [f"<h1>{html.escape(task_id)}</h1>"]
    if len(paths) > 1:
        # Not resolved for the reader: an identifier is a promise of uniqueness, and two
        # files holding one is the thing they need to see, not a coin the view flips.
        parts.append(
            f'<div class="problems"><strong>{len(paths)} files claim this identifier.'
            "</strong> An identifier is allocated once and never reused — this needs "
            "fixing in the registry.</div>"
        )
    for path in paths:
        with _scan_lock:
            configure_registry()
            relative = regen.rel(path)
            data = regen.parse_frontmatter(path)
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            content = f"[unreadable: {exc}]"
        parts.append(f'<p><code class="path">{html.escape(relative)}</code></p>')
        if data:
            # An optional field left blank is shown as a dash, not as the empty value
            # YAML parsed it into: a row reading `blocked_by: None` looks like an answer.
            rows = "".join(
                f"<tr><th>{html.escape(str(k))}</th>"
                f"<td>{html.escape(str(v)) if v not in (None, '') else '—'}</td></tr>"
                for k, v in data.items()
            )
            parts.append(f'<table class="fields">{rows}</table>')
        else:
            parts.append(
                '<p class="empty">No readable header — the file is shown as it stands.</p>'
            )
        parts.append(render_body(content))
    parts.append('<p><a href="/">← back to the registry</a></p>')
    return page(task_id, "".join(parts))


def render_not_found(task_id: str) -> bytes:
    body = (
        "<h1>No such task</h1>"
        "<p>Nothing in this repository carries the identifier "
        f'<code class="path">{html.escape(task_id)}</code> — neither a live task nor an '
        "archived one.</p>"
        '<p><a href="/">← back to the registry</a></p>'
    )
    return page("No such task", body)


def render_bad_path(path: str) -> bytes:
    """A path with more than one segment is not a failed lookup.

    Answering it with "no task carries this identifier" would state something the view
    never checked, and send the reader looking for a task rather than at their address
    bar. The two 404s say different things because they mean different things.
    """
    body = (
        "<h1>Not a task address</h1>"
        f'<p><code class="path">{html.escape(path)}</code> is not one — a task lives at '
        "its bare identifier, as in <code class=\"path\">/LABS-244</code>. Nothing was "
        "looked up.</p>"
        '<p><a href="/">← back to the registry</a></p>'
    )
    return page("Not a task address", body)


# ----------------------------------------------------------------------------- serving


class Handler(BaseHTTPRequestHandler):
    server_version = "task-registry-view"
    protocol_version = "HTTP/1.1"

    def __getattr__(self, name: str):
        """Any verb this class does not implement is refused, not answered.

        BaseHTTPRequestHandler looks for `do_<VERB>` and replies 501 when it finds
        nothing. 501 says "this server has no idea what that is"; 405 says "this server
        knows and will not". The second is the true statement, and routing every unknown
        verb here is what makes read-only a property of the code you can read in one
        place rather than an inference from the absence of writes.
        """
        if name.startswith("do_"):
            return self._method_not_allowed
        raise AttributeError(name)

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _method_not_allowed(self) -> None:
        self._respond(
            405,
            b"405 Method Not Allowed - this view only reads.\n",
            "text/plain; charset=utf-8",
            extra={"Allow": "GET, HEAD"},
        )

    def _respond(
        self,
        status: int,
        body: bytes,
        ctype: str = "text/html; charset=utf-8",
        extra: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # A task page carries client names, amounts and the substance of commercial
        # commitments. Loopback keeps that off the network; this keeps it off the disk,
        # where a browser cache would otherwise leave copies nobody thinks to clear.
        # Same header, for the same reason, as tools/proto-foto/gallery.py.
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        try:
            if path == HEALTH_PATH:
                # The root is reported so a probe can tell *which* repository the
                # instance already on this port is serving — two checkouts and one
                # fixed port is a normal situation here.
                payload = json.dumps(
                    {
                        "service": SERVICE_ID,
                        "root": str(getattr(regen, "REPO_ROOT", ROOT or "")),
                        "port": PORT,
                    }
                ).encode("utf-8")
                self._respond(200, payload, "application/json; charset=utf-8")
                return
            if path in ("", "/"):
                self._respond(200, render_index(date.today()))
                return
            task_id = path.strip("/")
            if "/" in task_id:
                self._respond(404, render_bad_path(path))
                return
            paths = resolve_id(task_id)
            if not paths:
                self._respond(404, render_not_found(task_id))
                return
            self._respond(200, render_task(task_id, paths))
        except Exception as exc:  # noqa: BLE001 — a broken file must not take the view down
            self.log_error("%s", exc)
            body = page(
                "Error",
                f'<h1>Something went wrong</h1><pre class="body">{html.escape(str(exc))}</pre>',
            )
            self._respond(500, body)


class View(ThreadingHTTPServer):
    """The listening socket, with address reuse decided per platform.

    HTTPServer sets SO_REUSEADDR, which on POSIX means "ignore lingering TIME_WAIT
    connections from the instance that just stopped" — worth having, restarts are
    frequent. On Windows the same option means something else entirely: it lets a second
    process bind a port another process is actively listening on. That would quietly
    produce the second server this view is required never to start, so on Windows the
    option comes off and a failed bind is allowed to mean what it says.
    """

    allow_reuse_address = os.name != "nt"


def probe(port: int, root: Path | None = None) -> str:
    """Who is holding this port: our own service, or somebody else's.

    Answers "ours", "foreign", or "other-root:<path>" when `root` is given and the
    instance on the port serves a different checkout.

    Asked only after the bind has already failed, so "nothing there" is not one of the
    answers. Occupancy is settled by the bind — authoritative, instant, and immune to a
    firewall that drops connections to closed ports instead of refusing them, which is
    what this machine does. Identity is settled here, by a request to a known endpoint,
    because that is the only thing that can tell our own instance from a stranger's.
    """
    url = f"http://127.0.0.1:{port}{HEALTH_PATH}"
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return "foreign"
    if payload.get("service") != SERVICE_ID:
        return "foreign"
    # Same service, possibly the wrong repository. The port is fixed and this machine
    # carries more than one checkout, so an instance serving a sibling directory answers
    # every question correctly and resolves every identifier - to somebody else's tasks.
    # It is the only failure here that looks like success, so it gets its own answer.
    if root is not None and Path(str(payload.get("root", ""))).resolve() != root.resolve():
        return "other-root:" + str(payload.get("root", ""))
    return "ours"


def report_port_state(port: int, address: str) -> int:
    """One word on stdout saying what a caller may do with this port, and nothing else.

    Four answers, because four situations call for four different moves and silence on
    the port distinguishes none of them: `ready` (use it), `free` (start one), and the
    two refusals. Exit stays 0 throughout - this is a question, and being told the port
    is taken is an answer to it, not a failure to answer.

    Occupancy is settled by a bind, the same way main() settles it, because a firewall
    that drops rather than refuses makes a connection attempt say "closed" about a port
    that is open. The socket is released immediately; whoever starts next races nobody
    but the operator, and the start path already handles losing that race.
    """
    try:
        probe_socket = View(("127.0.0.1", port), Handler)
    except OSError:
        state = probe(port, regen.REPO_ROOT)
        if state == "ours":
            print(f"ready {address}")
        elif state.startswith("other-root:"):
            print(f"busy-other-root {state.split(':', 1)[1]}")
        else:
            print("busy-foreign")
        return 0
    probe_socket.server_close()
    print("free")
    return 0


def main() -> int:
    global ROOT, SCHEMA, PORT

    parser = argparse.ArgumentParser(description="Serve a read-only view of the task registry.")
    parser.add_argument("--root", default=None,
                        help="repository root (default: git toplevel, else cwd)")
    parser.add_argument("--schema", default=None,
                        help="path to schema.yaml (default: the one next to this script)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"port on the loopback interface (default: {DEFAULT_PORT})")
    parser.add_argument("--probe", action="store_true",
                        help="report the port's state in one word and exit, starting nothing")
    args = parser.parse_args()

    # Windows consoles default to a legacy codepage that cannot encode the status icons
    # a validation complaint may carry. Same guard as regen_today.py and the linter.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    ROOT, SCHEMA, PORT = args.root, args.schema, args.port
    address = f"http://127.0.0.1:{args.port}/"

    # Fails fast and loudly if the contract is broken, rather than on the first request.
    configure_registry()

    if args.probe:
        return report_port_state(args.port, address)

    try:
        # Loopback only. Not a hardening measure bolted on afterwards — it is the reason
        # this view ships without authentication at all.
        server = View(("127.0.0.1", args.port), Handler)
    except OSError:
        # The port is taken. Whose it is decides whether that is good news.
        state = probe(args.port, regen.REPO_ROOT)
        if state == "ours":
            print(f"[=] already serving at {address}")
            return 0
        if state.startswith("other-root:"):
            # Answers correctly, resolves every identifier, and serves the wrong tasks.
            print(f"[!] port {args.port} serves another checkout ({state.split(':', 1)[1]})"
                  " — stop it or pass --port", file=sys.stderr)
            return 1
        print(f"[!] port {args.port} is held by another process — stop it or pass --port",
              file=sys.stderr)
        return 1

    print(f"[+] task registry view at {address} (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[=] stopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
