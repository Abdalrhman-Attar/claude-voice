#!/usr/bin/env bash
# Sets up the local Kokoro voice for the speak-pane Claude Code plugin (macOS and Linux;
# Windows: install.ps1). Apple Silicon and Intel Macs, x86_64 and arm64 Linux.
#   ./install.sh            Python environment, models, start at login, ctrl+x s shortcut
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
if [[ -z "$PY" ]]; then
  if [[ "$(uname)" == "Darwin" ]]; then
    echo "Python 3.10 to 3.13 is needed: brew install python@3.12"
  else
    echo "Python 3.10 to 3.13 is needed, with venv: sudo apt install python3 python3-venv (or your distro's equivalent)"
  fi
  exit 1
fi

mkdir -p "$HOME_DIR"
echo "Python environment in $HOME_DIR/.venv ($("$PY" -c 'import platform; print(platform.python_version(), platform.machine())'))"
if command -v uv >/dev/null 2>&1; then
  [[ -x "$VENV_PY" ]] || uv venv -q --python "$PY" "$HOME_DIR/.venv"
  uv pip install -q --python "$VENV_PY" -r "$REPO/server/requirements.txt"
else
  [[ -x "$VENV_PY" ]] || "$PY" -m venv "$HOME_DIR/.venv"
  "$VENV_PY" -m pip install -q --upgrade pip
  "$VENV_PY" -m pip install -q -r "$REPO/server/requirements.txt"
fi

"$VENV_PY" "$REPO/server/install_helper.py" install
