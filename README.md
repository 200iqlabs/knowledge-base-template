# Knowledge Base Template

A file-based knowledge base your AI coding agent can actually work in — with the
conventions written down, plus a linter and a task-registry generator that hold those
conventions in place.

The point is not the folder layout. The point is that **every fact has exactly one
home**, so the agent reads the current answer instead of finding three copies and having
to guess which one is stale.

Two things follow from that, and they are what make this different from a folder
structure you could sketch in five minutes:

- **State, tasks, and the file map are separated on purpose.** `status.md` holds state,
  `tasks/` holds tasks, `catalog.md` holds the map. None of them repeats another. The
  rules say why, so you can tell which ones you may bend.
- **The generated parts are generated.** A pre-commit hook rewrites the task index and
  the `AUTO` section of every `status.md`, so the summaries are never a hand-maintained
  copy that quietly rots.

## What is in here

| Path | What it holds |
|---|---|
| `CLAUDE.md` | The rules themselves — the working core the agent loads |
| `context/` | The knowledge base: entities, their state, their tasks |
| `tools/context-lint/` | Deterministic, read-only consistency checks over `context/` |
| `tools/tasks/` | Task registry generator and the on-demand report |
| `tools/hooks/` | Pre-commit hook that keeps the generated sections current |
| `skills/`, `.claude/commands/` | `/setup`, `/lint`, `/close-session`, `/today` |

## Requirements

- Python 3.10+ with PyYAML (`pip install pyyaml`)
- git
- An agent that reads `CLAUDE.md` (Claude Code, or any tool you point at it)

## First run

```bash
pip install pyyaml
python tools/context-lint/lint.py     # 0 findings, 1 entity scanned
python tools/tasks/regen.py --check   # 3 live tasks, 0 errors
```

Both commands report on `EXAMPLE_PROJECT`, the fictional entity this template ships
with. It exists so the first run returns something visible: a tool that prints nothing
looks the same whether it is working or scanning an empty configuration. Note that the
linter reports the **number of entities scanned** alongside the findings, for exactly
that reason.

Then configure the base for yourself:

```
/setup
```

That walks through the language of the generated boards, task owners, your scope roots,
the thresholds, installing the hook, and finally removing the example entity. All of it
is configuration — you should never need to edit a `.py` file.

If you would rather do it by hand, the same ground is covered by
`tools/tasks/schema.yaml` and `tools/context-lint/config.yaml`, both commented, plus:

```bash
bash tools/hooks/install.sh
```

## Removing the example entity

Do this once you have a real entity of your own — until then it is what makes the tools
return a visible result.

```bash
git rm -r context/projects/EXAMPLE_PROJECT
rm context/tasks/sprint-2026-W32.md
python tools/tasks/regen.py
```

Then drop its row from `context/projects/_index.md`.

## Changing the language

The generator writes prose into your files — headings, table headers, empty states.
Those strings live in the `labels:` and `report_labels:` blocks of
`tools/tasks/schema.yaml`, not in the code, so translating your boards means editing
YAML. Keep the `{placeholders}` intact.

Console output stays English: it leaves no trace in the repo, so there is nothing for it
to drift against.

## License

MIT — see [LICENSE](LICENSE).
