#!/usr/bin/env python3
# Second half of install.sh / install.ps1, run with the voice's own Python environment:
# models, pronunciation list, the ctrl+x s shortcut and starting the server at login.
#   install_helper.py install | uninstall
import json, os, shutil, subprocess, sys, urllib.request

CODE = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("KOKORO_HOME", os.path.expanduser("~/.claude/kokoro"))
MODELS = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
PYTHON = sys.executable
SERVER = os.path.join(CODE, "server.py")
LOG = os.path.join(HOME, "server.log")
AGENT_ID = "com.claude.kokoro-voice"  # macOS LaunchAgent
UNIT = "claude-kokoro-voice.service"  # Linux systemd user unit
RUN_KEY = "ClaudeKokoroVoice"  # Windows HKCU Run value


def download():
    for name in ("kokoro-v1.0.onnx", "voices-v1.0.bin"):
        path = os.path.join(HOME, name)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            continue
        print(f"Downloading {name}")
        with urllib.request.urlopen(f"{MODELS}/{name}") as r, open(path + ".part", "wb") as f:
            total, done = int(r.headers.get("Content-Length") or 0), 0
            while chunk := r.read(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r  {done * 100 // total}% of {total >> 20} MB", end="", flush=True)
        print()
        os.replace(path + ".part", path)


def lexicon():
    path = os.path.join(HOME, "pronounce.json")
    if not os.path.exists(path):
        shutil.copy(os.path.join(CODE, "pronounce.default.json"), path)


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


if __name__ == "__main__":
    if sys.argv[1:] == ["uninstall"]:
        autostart(False)
        print(f"Start at login removed. Data is left in {HOME} (delete it to free about 350 MB).")
        sys.exit(0)
    os.makedirs(HOME, exist_ok=True)
    download()
    lexicon()
    check_audio()
    autostart(True)
    shortcut()
    print("""
Voice installed. In Claude Code, add the plugin if you have not:
  /plugin install speak-pane --marketplace Abdalrhman-Attar/claude-voice
then run /reload-plugins. Open the panel with /speak.""")
