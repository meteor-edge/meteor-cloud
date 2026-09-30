#!/usr/bin/env bash
# Clone meteor-ui into ui/ so console has a real git remote (meteor-edge/meteor-ui).
# The public website is sparse-skipped so it does not appear in this tree.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UI_REMOTE="${METEOR_UI_REMOTE:-git@github.com:meteor-edge/meteor-ui.git}"
UI_REF="${METEOR_UI_REF:-main}"
UI_DIR="$ROOT/ui"
PLACEHOLDER="$ROOT/console"

if [ -d "$UI_DIR/.git" ]; then
  echo "Updating ui/ from ${UI_REMOTE} (${UI_REF})..."
  git -C "$UI_DIR" remote set-url origin "$UI_REMOTE"
  git -C "$UI_DIR" fetch origin
  git -C "$UI_DIR" checkout "$UI_REF"
  git -C "$UI_DIR" pull --ff-only "origin" "$UI_REF"
else
  echo "Cloning ${UI_REMOTE} into ui/..."
  git clone --filter=blob:none --sparse --branch "$UI_REF" "$UI_REMOTE" "$UI_DIR"
  git -C "$UI_DIR" sparse-checkout set --cone console
fi

# One-time: keep edits from the old rsync copy under console/.
if [ -f "$PLACEHOLDER/package.json" ]; then
  echo "Moving local console files into ui/console..."
  mkdir -p "$UI_DIR/console"
  rsync -a --exclude README.md "$PLACEHOLDER/" "$UI_DIR/console/"
fi

# Public placeholder only (tracked README).
shopt -s dotglob nullglob
for path in "$PLACEHOLDER"/* "$PLACEHOLDER"/.[!.]* "$PLACEHOLDER"/..?*; do
  [ -e "$path" ] || continue
  [ "$(basename "$path")" = README.md ] && continue
  rm -rf "$path" 2>/dev/null || true
done

echo "Console remote: $(git -C "$UI_DIR" remote get-url origin)"
echo "  Edit:   ui/console"
echo "  Commit: git -C ui add console && git -C ui commit"
echo "  Push:   git -C ui push"
echo "  make install-console && make dev-console"
