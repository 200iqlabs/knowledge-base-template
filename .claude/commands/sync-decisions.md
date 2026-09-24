---
description: Carry the accepted decisions from this knowledge base into a code repository, as a generated file its agent reads at startup
---

Carry the current decisions across to the code repository. Argument (optional — a target
name from `tools/decisions-bridge/config.yaml`, `--check` to compare without writing, or
a path to a code repository for a one-off run): $ARGUMENTS

## Steps

1. Run the bridge with the **Bash tool, not PowerShell** (PowerShell mangles the UTF-8
   output):

   ```bash
   python tools/decisions-bridge/bridge.py                   # every configured target
   python tools/decisions-bridge/bridge.py --target <name>   # one of them
   python tools/decisions-bridge/bridge.py --check           # compare, write nothing
   ```

   **An argument that is a path, not a target name**, is a one-off run: pass it as
   `--repo <path>` and ask the user which entity's decisions to carry
   (`--source context/projects/<ENTITY>`, repeatable; `--source context` adds the
   organisation-wide ones from `context/decisions/`). Then suggest adding it to the config
   so next time it is one word.

   **No targets configured** (exit 2, "no targets configured") is the normal state of a
   fresh knowledge base, not a failure. Ask the user where their code repository is and
   which entities it implements, and add the entry to `tools/decisions-bridge/config.yaml`
   — confirm it with them before writing.

2. Report per target, one line each: `WRITTEN`, `UP TO DATE` or `OUT OF DATE`, with the
   path. After `WRITTEN`, tell the user that the file changed **in the code repository**,
   and that the code repository's own commit is theirs to make — this command never runs
   git there.

3. Exit code `2` means nothing was written. Relay the message verbatim: a malformed
   decision file (fix the header named in it — see `CLAUDE.md` → **Decisions**), a
   missing code repository, or a file at the output path that the bridge did not generate
   (it refuses to overwrite somebody's own notes).

## What this command does not do

- **It does not run by itself.** No hook and no schedule call it. The code repository
  learns about a decision when somebody runs this — on purpose, so that person knows when
  it happened.
- **It does not write anything back.** The knowledge base is the source of truth; the file
  in the code repository is a reflection, rewritten in full on every run. A decision is
  changed here, then carried across again.
- **It does not commit** — neither here nor in the code repository.
