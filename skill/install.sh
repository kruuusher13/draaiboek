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
python3 -m venv .venv 2>/dev/null || true
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
