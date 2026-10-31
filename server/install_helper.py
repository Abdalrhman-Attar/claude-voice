#!/usr/bin/env python3
# Second half of install.sh / install.ps1, run with the voice's own Python environment:
# models, pronunciation list, the ctrl+x s shortcut and starting the server at login.
#   install_helper.py install [--agents codex,gemini,cursor,mcp] [--arabic] | uninstall
#   install_helper.py arabic         adds the Arabic voice (about 230 MB)
#   install_helper.py connect codex,gemini,cursor,mcp | disconnect codex,gemini,cursor
import json, os, shutil, ssl, subprocess, sys, urllib.request

CODE = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("KOKORO_HOME", os.path.expanduser("~/.claude/kokoro"))
MODELS = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
PYTHON = sys.executable
# A git checkout runs its own server/ (edits apply at once); a copy elsewhere, such as inside the VS
# Code extension's versioned folder, is copied to KOKORO_HOME/app so start-at-login survives updates.
IN_REPO = os.path.isdir(os.path.join(CODE, "..", ".git"))
APP = CODE if IN_REPO else os.path.join(HOME, "app")
SERVER = os.path.join(APP, "server.py")
LOG = os.path.join(HOME, "server.log")
AGENT_ID = "com.claude.kokoro-voice"  # macOS LaunchAgent
UNIT = "claude-kokoro-voice.service"  # Linux systemd user unit
RUN_KEY = "ClaudeKokoroVoice"  # Windows HKCU Run value


def tls():
    """Certificates for the download. Python from python.org on macOS ships without any until its
    Install Certificates step is run, so certifi's bundle is used when it is there."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def fetch(url, path, name):
    print(f"Downloading {name}")
    with urllib.request.urlopen(url, context=tls()) as r, open(path + ".part", "wb") as f:
        total, done = int(r.headers.get("Content-Length") or 0), 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done * 100 // total}% of {total >> 20} MB", end="", flush=True)
    print()
    os.replace(path + ".part", path)


def download():
    for name in ("kokoro-v1.0.onnx", "voices-v1.0.bin"):
        path = os.path.join(HOME, name)
        if not (os.path.exists(path) and os.path.getsize(path) > 0):
            fetch(f"{MODELS}/{name}", path, name)


def arabic():
    """The Arabic voice (Nabra-82M) and its diacritizer (CATT), about 230 MB."""
    import zipfile

    sys.path.insert(0, CODE)
    import arabic as voice

    os.makedirs(voice.DIR, exist_ok=True)
    for name, url in voice.FILES.items():
        path = os.path.join(voice.DIR, name)
        if name == "catt.zip":
            if os.path.exists(os.path.join(voice.DIR, "encoder.onnx")):
                continue
            fetch(url, path, "the Arabic diacritizer")
            with zipfile.ZipFile(path) as z:
                for member in ("encoder.onnx", "decoder.onnx"):
                    with z.open(member) as src, open(os.path.join(voice.DIR, member), "wb") as dst:
                        shutil.copyfileobj(src, dst)
            os.remove(path)
        elif not os.path.exists(path):
            fetch(url, path, "the Arabic voice" if name.endswith(".onnx") else name)
    print("Arabic voice installed. It is used from the next Arabic sentence.")


def lexicon():
    path = os.path.join(HOME, "pronounce.json")
    if not os.path.exists(path):
        shutil.copy(os.path.join(CODE, "pronounce.default.json"), path)


def copy_app():
    if IN_REPO:
        return
    os.makedirs(APP, exist_ok=True)
    for f in os.listdir(CODE):
        if f.endswith((".py", ".json", ".txt")):
            shutil.copy(os.path.join(CODE, f), os.path.join(APP, f))
    shutil.copytree(os.path.join(CODE, "catt"), os.path.join(APP, "catt"), dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))


def claude_cli():
    """The claude command, from PATH or a login shell's PATH (GUI apps often lack the user's PATH)."""
    found = shutil.which("claude")
    if found or sys.platform == "win32":
        return found
    shell = os.environ.get("SHELL", "/bin/zsh")
    try:
        out = subprocess.run([shell, "-lc", "command -v claude"], capture_output=True, text=True, timeout=15).stdout.strip()
        return out.splitlines()[-1] if out else None
    except (OSError, subprocess.SubprocessError):
        return None


def plugin():
    """Installs the Claude Code plugin (speech while Claude types, the in-terminal panel)."""
    cli = claude_cli()
    if not cli:
        print("Claude Code plugin: claude command not found. In Claude Code run:\n"
              "  /plugin install speak-pane --marketplace Abdalrhman-Attar/claude-voice")
        return False
    run = lambda *a: subprocess.run([cli, "plugin", *a], capture_output=True, text=True, timeout=180)
    listed = run("list")
    if "speak-pane" in (listed.stdout or ""):
        print("Claude Code plugin: already installed")
        return True
    run("marketplace", "add", "Abdalrhman-Attar/claude-voice")
    done = run("install", "speak-pane@claude-voice")
    if done.returncode == 0:
        print("Claude Code plugin: installed (run /reload-plugins in open Claude Code sessions)")
        return True
    print("Claude Code plugin: could not install automatically:", (done.stderr or done.stdout).strip()[:300])
    print("  In Claude Code run: /plugin install speak-pane --marketplace Abdalrhman-Attar/claude-voice")
    return False


def check_audio():
    try:
        import sounddevice  # noqa: F401
    except OSError:
        print("Audio library missing. On Debian or Ubuntu: sudo apt install libportaudio2"
              "  (Fedora: sudo dnf install portaudio)")


def shortcut():
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
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print("Shortcut: ctrl+x s stops the voice (while the Speak pane is open); /hush works anywhere")


def macos_agent(on):
    plist = os.path.expanduser(f"~/Library/LaunchAgents/{AGENT_ID}.plist")
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{domain}/{AGENT_ID}"], capture_output=True)
    if not on:
        if os.path.exists(plist):
            os.remove(plist)
        return
    os.makedirs(os.path.dirname(plist), exist_ok=True)
    with open(plist, "w") as f:
        f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{AGENT_ID}</string>
  <key>ProgramArguments</key>
  <array><string>{PYTHON}</string><string>{SERVER}</string></array>
  <key>EnvironmentVariables</key>
  <dict><key>KOKORO_HOME</key><string>{HOME}</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
  <key>StandardErrorPath</key><string>{LOG}</string>
  <key>StandardOutPath</key><string>/dev/null</string>
</dict>
</plist>
""")
    return subprocess.run(["launchctl", "bootstrap", domain, plist]).returncode == 0


def linux_unit(on):
    if not shutil.which("systemctl"):
        if on:
            print("No systemd: the server will start with the first reply instead of at login.")
        return False
    unit = os.path.expanduser(f"~/.config/systemd/user/{UNIT}")
    ctl = ["systemctl", "--user"]
    if not on:
        subprocess.run([*ctl, "disable", "--now", UNIT], capture_output=True)
        if os.path.exists(unit):
            os.remove(unit)
        subprocess.run([*ctl, "daemon-reload"], capture_output=True)
        return
    os.makedirs(os.path.dirname(unit), exist_ok=True)
    with open(unit, "w") as f:
        f.write(f"""[Unit]
Description=Kokoro voice for Claude Code

[Service]
ExecStart="{PYTHON}" "{SERVER}"
Environment=KOKORO_HOME={HOME}
StandardOutput=null
StandardError=append:{LOG}
Restart=no

[Install]
WantedBy=default.target
""")
    subprocess.run([*ctl, "daemon-reload"], capture_output=True)
    if subprocess.run([*ctl, "enable", "--now", UNIT], capture_output=True).returncode != 0:
        print("Could not enable the systemd user unit: the server will start with the first reply.")
        return False
    return True


def windows_run_key(on):
    import winreg

    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                         winreg.KEY_SET_VALUE)
    with key:
        if on:
            pythonw = os.path.join(os.path.dirname(PYTHON), "pythonw.exe")
            winreg.SetValueEx(key, RUN_KEY, 0, winreg.REG_SZ, f'"{pythonw}" "{SERVER}"')
            subprocess.Popen([pythonw, SERVER], creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW)
            return True
        else:
            try:
                winreg.DeleteValue(key, RUN_KEY)
            except FileNotFoundError:
                pass


def autostart(on):
    if sys.platform == "darwin":
        started = macos_agent(on)
    elif sys.platform == "win32":
        started = windows_run_key(on)
    else:
        started = linux_unit(on)
    if on and started:
        print("Login: the voice server starts at login and stops after an idle hour")


HOOK = os.path.join(CODE, "agent_hook.py")
MCP = os.path.join(CODE, "mcp_server.py")
AGENTS = ("codex", "gemini", "cursor", "mcp")


def hook_command(agent):
    return f'"{PYTHON}" "{HOOK}" {agent}'


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        shutil.copy(path, path + ".bak")
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def codex(on):
    """Codex runs `notify` after each turn; config.toml allows one, so an existing one is kept."""
    path = os.path.expanduser("~/.codex/config.toml")
    lines = open(path).read().splitlines() if os.path.exists(path) else []
    mine = [i for i, l in enumerate(lines) if l.startswith("notify") and "agent_hook.py" in l]
    if not on:
        if mine:
            del lines[mine[0]]
            with open(path, "w") as f:
                f.write("\n".join(lines) + "\n")
        return
    if mine:
        return print("Codex: already connected")
    if any(l.strip().startswith("notify") for l in lines):
        return print(f"Codex: {path} already has a notify program; replace it with\n"
                     f"  notify = ['{PYTHON}', '{HOOK}', 'codex']")
    first_table = next((i for i, l in enumerate(lines) if l.strip().startswith("[")), len(lines))
    lines.insert(first_table, f"notify = ['{PYTHON}', '{HOOK}', 'codex']  # claude-voice reads replies aloud")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print("Codex: connected (replies are read when a turn ends)")


def gemini(on):
    """Gemini CLI: AfterAgent for replies and Notification for permission prompts."""
    path = os.path.expanduser("~/.gemini/settings.json")
    data = load_json(path)
    hooks = data.setdefault("hooks", {})
    for name in ("AfterAgent", "Notification"):
        kept = [d for d in hooks.get(name, []) if not any("agent_hook.py" in h.get("command", "") for h in d.get("hooks", []))]
        if on:
            kept.append({"hooks": [{"type": "command", "command": hook_command("gemini"), "timeout": 10000}]})
        if kept:
            hooks[name] = kept
        else:
            hooks.pop(name, None)
    if not hooks:
        data.pop("hooks")
    if on:
        data.setdefault("tools", {})["enableHooks"] = True
    save_json(path, data)
    if on:
        print("Gemini CLI: connected (replies, and permission prompts when you are away)")


def cursor(on):
    """Cursor (editor and cursor-agent with hooks): afterAgentResponse."""
    path = os.path.expanduser("~/.cursor/hooks.json")
    data = load_json(path)
    data.setdefault("version", 1)
    hooks = data.setdefault("hooks", {})
    kept = [h for h in hooks.get("afterAgentResponse", []) if "agent_hook.py" not in h.get("command", "")]
    if on:
        kept.append({"command": hook_command("cursor")})
    if kept:
        hooks["afterAgentResponse"] = kept
    else:
        hooks.pop("afterAgentResponse", None)
    save_json(path, data)
    if on:
        print("Cursor: connected (needs a Cursor version with hooks, 1.7 or newer)")


def mcp(on):
    if on:
        print(f"""MCP server (speak and stop_speaking tools) for any MCP client. Add to its MCP config:
  "claude-voice": {{ "command": {json.dumps(PYTHON)}, "args": [{json.dumps(MCP)}] }}
  Codex:  codex mcp add claude-voice -- "{PYTHON}" "{MCP}"
  Gemini: gemini mcp add claude-voice "{PYTHON}" "{MCP}"
""")


def connect(names, on=True):
    for name in names:
        if name not in AGENTS:
            print(f"Unknown agent {name}: use {', '.join(AGENTS)}")
            continue
        globals()[name](on)


def agent_list(arg):
    return [a.strip() for a in arg.split(",") if a.strip()]


if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["arabic"]:
        arabic()
        sys.exit(0)
    if args == ["plugin"]:
        sys.exit(0 if plugin() else 1)
    if args[:1] in (["connect"], ["disconnect"]) and len(args) == 2:
        connect(agent_list(args[1]), args[0] == "connect")
        sys.exit(0)
    if args == ["uninstall"]:
        connect(["codex", "gemini", "cursor"], on=False)
        autostart(False)
        print(f"Start at login removed. Data is left in {HOME} (delete it to free about 350 MB).")
        sys.exit(0)
    os.makedirs(HOME, exist_ok=True)
    download()
    lexicon()
    check_audio()
    copy_app()
    autostart(True)
    shortcut()
    if "--arabic" in args:
        arabic()
    if "--no-plugin" not in args:
        plugin()
    if "--agents" in args and args.index("--agents") + 1 < len(args):
        connect(agent_list(args[args.index("--agents") + 1]))
    print("""
Voice installed. Replies from Claude Code are read aloud from now on.
Open Claude Code sessions: run /reload-plugins. Open the panel with /speak.""")
