#!/bin/sh
# atlasix installer (POSIX). Alternative for agents: read AGENT.md at the repo root.
set -eu

REPO_URL="${ATLASIX_REPO:-https://github.com/acidsugarx/atlasix}"

have() { command -v "$1" >/dev/null 2>&1; }

if ! have uv; then
  echo "uv not found — installing"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

if [ -d "$REPO_URL" ]; then
  SRC="$REPO_URL"  # local checkout (testing)
else
  SRC="git+$REPO_URL"
fi

uv tool install --force "$SRC"
echo
atlasix --help >/dev/null && echo "atlasix installed. Next: atlasix init && atlasix pack list"
