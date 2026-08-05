#!/usr/bin/env bash
# Install the repo-tracked git hooks into .git/hooks/.
# Re-run safely — it overwrites what it installed last time.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOOKS_SRC="$REPO_ROOT/tools/hooks"
HOOKS_DST="$REPO_ROOT/.git/hooks"

mkdir -p "$HOOKS_DST"

for hook in pre-commit; do
  src="$HOOKS_SRC/$hook"
  dst="$HOOKS_DST/$hook"
  if [[ ! -f "$src" ]]; then
    echo "skip: $src not found"
    continue
  fi
  rm -f "$dst"
  # Prefer a symlink so hook edits take effect without reinstalling. Windows without
  # developer mode cannot create one, so fall back to a copy — at the cost of having
  # to re-run this script after every hook change.
  if ln -s "../../tools/hooks/$hook" "$dst" 2>/dev/null; then
    echo "linked: $dst -> tools/hooks/$hook"
  else
    cp "$src" "$dst"
    chmod +x "$dst" 2>/dev/null || true
    echo "copied: $dst"
  fi
done

echo
echo "Git hooks installed. Re-run this script after pulling hook changes."
