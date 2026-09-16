#!/usr/bin/env bash
# Install the draaiboek skill for any agent on this machine.
set -euo pipefail

DEST="${1:-$HOME/draaiboek}"
REPO="https://github.com/kruuusher13/draaiboek.git"

if [ -d "$DEST/.git" ]; then
  git -C "$DEST" pull --quiet
else
  git clone --quiet "$REPO" "$DEST"
fi

cd "$DEST"

# The default python3 is often the system one -- 3.9 on macOS -- which is too
# old. Find a usable interpreter rather than assuming the first one on PATH.
# Homebrew's python is often absent from a non-interactive PATH, which is the
# shell an agent runs in, so look there by name as well.
PY=""
for candidate in \
    python3.14 python3.13 python3.12 \
    /opt/homebrew/bin/python3.14 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 \
    /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)' 2>/dev/null; then
    PY="$candidate"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Needs Python 3.12 or newer; none found on PATH." >&2
  echo "Install one (brew install python@3.12) and run this again." >&2
  exit 1
fi
echo "Using $("$PY" -V)"
"$PY" -m venv .venv 2>/dev/null || true
# A fresh venv can ship a pip too old for a pyproject-only editable install.
.venv/bin/pip install --quiet --upgrade pip setuptools wheel
.venv/bin/pip install --quiet -e .

mkdir -p "$HOME/.draaiboek" && chmod 700 "$HOME/.draaiboek"
[ -f "$HOME/.draaiboek/env" ] || {
  cp .env.example "$HOME/.draaiboek/env"
  chmod 600 "$HOME/.draaiboek/env"
  echo "Created ~/.draaiboek/env — fill in the credentials before using it."
}

cat <<JSON

Installed at $DEST

Add this to the agent's MCP configuration:

{"mcpServers": {"draaiboek": {
  "command": "$DEST/.venv/bin/draaiboek",
  "args": ["serve"]
}}}

Give the agent $DEST/skill/SKILL.md as a skill.
Then check everything is reachable:

  $DEST/.venv/bin/draaiboek doctor
JSON
