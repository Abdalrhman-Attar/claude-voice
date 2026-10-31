#!/usr/bin/env bash
# Sets up the local Kokoro voice for the speak-pane Claude Code plugin (macOS).
#   ./install.sh            venv, models, login agent, ctrl+x s shortcut
#   ./install.sh --uninstall
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${KOKORO_HOME:-$HOME/.claude/kokoro}"
AGENT_ID="com.claude.kokoro-voice"
AGENT="$HOME/Library/LaunchAgents/$AGENT_ID.plist"
MODELS="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

if [[ "${1:-}" == "--uninstall" ]]; then
  launchctl bootout "gui/$(id -u)/$AGENT_ID" 2>/dev/null || true
  rm -f "$AGENT"
  echo "Login agent removed. Data is left in $HOME_DIR (delete it to free ~350 MB)."
  exit 0
fi

[[ "$(uname)" == "Darwin" ]] || { echo "macOS only for now."; exit 1; }

PY=""
for v in 3.12 3.11 3.10; do
  for c in "/opt/homebrew/bin/python$v" "/usr/local/bin/python$v" "python$v"; do
    if command -v "$c" >/dev/null 2>&1; then PY="$(command -v "$c")"; break 2; fi
  done
done
[[ -n "$PY" ]] || { echo "Python 3.10 to 3.12 is needed (brew install python@3.12)."; exit 1; }

mkdir -p "$HOME_DIR"
echo "Python environment in $HOME_DIR/.venv"
if command -v uv >/dev/null 2>&1; then
  [[ -x "$HOME_DIR/.venv/bin/python" ]] || uv venv -q --python "$PY" "$HOME_DIR/.venv"
  uv pip install -q --python "$HOME_DIR/.venv/bin/python" -r "$REPO/server/requirements.txt"
else
  [[ -x "$HOME_DIR/.venv/bin/python" ]] || "$PY" -m venv "$HOME_DIR/.venv"
  "$HOME_DIR/.venv/bin/pip" install -q -r "$REPO/server/requirements.txt"
fi

for f in kokoro-v1.0.onnx voices-v1.0.bin; do
  if [[ ! -s "$HOME_DIR/$f" ]]; then
    echo "Downloading $f"
    curl -fL --progress-bar -o "$HOME_DIR/$f" "$MODELS/$f"
  fi
done
[[ -f "$HOME_DIR/pronounce.json" ]] || cp "$REPO/server/pronounce.default.json" "$HOME_DIR/pronounce.json"

echo "Login agent: the voice server starts at login and stops after an idle hour"
mkdir -p "$(dirname "$AGENT")"
cat > "$AGENT" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$AGENT_ID</string>
  <key>ProgramArguments</key>
  <array>
    <string>$HOME_DIR/.venv/bin/python</string>
    <string>$REPO/server/server.py</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict><key>KOKORO_HOME</key><string>$HOME_DIR</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
  <key>StandardErrorPath</key><string>$HOME_DIR/server.log</string>
  <key>StandardOutPath</key><string>/dev/null</string>
</dict>
</plist>
PLIST
launchctl bootout "gui/$(id -u)/$AGENT_ID" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$AGENT"

echo "Shortcut: ctrl+x s stops the voice from the prompt (while the Speak pane is open)"
"$HOME_DIR/.venv/bin/python" - <<'PY'
import json, os
path = os.path.expanduser("~/.claude/keybindings.json")
data = json.load(open(path)) if os.path.exists(path) else {
    "$schema": "https://www.schemastore.org/claude-code-keybindings.json",
    "$docs": "https://code.claude.com/docs/en/keybindings",
    "bindings": [],
}
blocks = data.setdefault("bindings", [])
glob = next((b for b in blocks if b.get("context") == "Global"), None)
if glob is None:
    glob = {"context": "Global", "bindings": {}}
    blocks.append(glob)
glob.setdefault("bindings", {}).setdefault("ctrl+x s", "app:toggleDiffPreSession")
json.dump(data, open(path, "w"), indent=2)
PY

cat <<DONE

Voice installed. In Claude Code, add the plugin if you have not:
  /plugin install speak-pane --marketplace Abdalrhman-Attar/claude-voice
then run /reload-plugins. Open the panel with /speak.
DONE
