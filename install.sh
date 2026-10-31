#!/usr/bin/env bash
# Sets up the local Kokoro voice for the speak-pane Claude Code plugin (macOS and Linux;
# Windows: install.ps1). Apple Silicon and Intel Macs, x86_64 and arm64 Linux.
#   ./install.sh            Python environment, models, start at login, ctrl+x s shortcut
#   ./install.sh --agents codex,gemini,cursor,mcp   also read other agents' replies aloud
#   ./install.sh --arabic   also the Arabic voice (about 700 MB)
#   ./install.sh --uninstall
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${KOKORO_HOME:-$HOME/.claude/kokoro}"
export KOKORO_HOME="$HOME_DIR"
VENV_PY="$HOME_DIR/.venv/bin/python"

if [[ "${1:-}" == "--uninstall" ]]; then
  if [[ -x "$VENV_PY" ]]; then
    "$VENV_PY" "$REPO/server/install_helper.py" uninstall
  else
    echo "Nothing installed in $HOME_DIR."
  fi
  exit 0
fi

case "$(uname)" in
  Darwin|Linux) ;;
  *) echo "On Windows run install.ps1 in PowerShell."; exit 1 ;;
esac

# Python 3.10 to 3.13: onnxruntime has no wheels for newer ones yet.
PY=""
for v in 3.12 3.13 3.11 3.10; do
  for c in "/opt/homebrew/bin/python$v" "/usr/local/bin/python$v" "/usr/bin/python$v" "python$v"; do
    if command -v "$c" >/dev/null 2>&1; then PY="$(command -v "$c")"; break 2; fi
  done
done
if [[ -z "$PY" ]] && command -v python3 >/dev/null 2>&1 \
    && python3 -c 'import sys; sys.exit(not (3, 10) <= sys.version_info[:2] <= (3, 13))'; then
  PY="$(command -v python3)"
fi
# No suitable Python: uv fetches a private Python 3.12 for the voice (nothing else on the system changes).
UV="$(command -v uv || true)"
[[ -z "$UV" && -x "$HOME/.local/bin/uv" ]] && UV="$HOME/.local/bin/uv"
if [[ -z "$PY" && -z "$UV" ]]; then
  echo "Python 3.10 to 3.13 not found: installing uv to fetch one (https://astral.sh/uv)"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null
  UV="$HOME/.local/bin/uv"
fi

mkdir -p "$HOME_DIR"
if [[ -n "$UV" ]]; then
  [[ -x "$VENV_PY" ]] || "$UV" venv -q --python "${PY:-3.12}" "$HOME_DIR/.venv"
  echo "Python environment in $HOME_DIR/.venv ($("$VENV_PY" -c 'import platform; print(platform.python_version(), platform.machine())'))"
  echo "Installing the voice's Python packages"
  "$UV" pip install -q --python "$VENV_PY" -r "$REPO/server/requirements.txt"
else
  [[ -x "$VENV_PY" ]] || "$PY" -m venv "$HOME_DIR/.venv"
  echo "Python environment in $HOME_DIR/.venv ($("$VENV_PY" -c 'import platform; print(platform.python_version(), platform.machine())'))"
  echo "Installing the voice's Python packages"
  "$VENV_PY" -m pip install -q --upgrade pip
  "$VENV_PY" -m pip install -q -r "$REPO/server/requirements.txt"
fi

"$VENV_PY" "$REPO/server/install_helper.py" install "$@"
