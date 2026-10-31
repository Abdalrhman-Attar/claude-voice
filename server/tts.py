#!/usr/bin/env python3
# Client for the Kokoro server, starting the server when needed.
#   tts.py --json                  request JSON on stdin (see server.handle)
#   tts.py [--session ID] [--label NAME] [--mode reply|play|stop|pause|skip] [--all]   text on stdin
#   tts.py --check-away [BUNDLE_ID]   exit 0 when the terminal running us is not in front, 1 when it is
#                                    (macOS: the app with that bundle id; elsewhere: our own window)
import argparse, json, os, subprocess, sys, time

import transport

CODE = os.path.dirname(os.path.abspath(__file__))
HOME = transport.HOME

args = argparse.ArgumentParser()
args.add_argument("--json", action="store_true")
args.add_argument("--check-away", nargs="?", const="")
args.add_argument("--session", default="")
args.add_argument("--label", default="")
args.add_argument("--mode", choices=["reply", "play", "stop", "pause", "skip"], default="play")
args.add_argument("--all", action="store_true")
opts = args.parse_args()


CONSOLE_HOSTS = {"windowsterminal.exe", "openconsole.exe", "conhost.exe"}


def ancestors():
    """Our own process and every parent up to the root: the terminal is one of them."""
    import psutil

    procs = [psutil.Process()]
    try:
        procs += procs[0].parents()
    except psutil.Error:
        pass
    return procs


def front_pid():
    """The process owning the focused window, or None when the platform will not say."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        hwnd = ctypes.windll.user32.GetForegroundWindow()
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value or None
    # Linux and other X11 desktops; Wayland gives no answer and counts as away.
    for cmd in (["xdotool", "getactivewindow", "getwindowpid"],):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=2).stdout.strip()
            if out.isdigit():
                return int(out)
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        win = subprocess.run(["xprop", "-root", "_NET_ACTIVE_WINDOW"], capture_output=True, text=True, timeout=2).stdout
        wid = win.strip().split()[-1]
        out = subprocess.run(["xprop", "-id", wid, "_NET_WM_PID"], capture_output=True, text=True, timeout=2).stdout
        return int(out.strip().split()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def terminal_in_front(bundle):
    if sys.platform == "darwin":
        if not bundle:
            return False
        front = subprocess.run(["lsappinfo", "front"], capture_output=True, text=True).stdout.strip()
        info = subprocess.run(["lsappinfo", "info", "-only", "bundleid", front], capture_output=True, text=True).stdout
        return f'"{bundle}"' in info
    pid = front_pid()
    if pid is None:
        return False
    try:
        import psutil

        chain = ancestors()
        if pid in {p.pid for p in chain}:
            return True
        # A console host can draw the window from a process beside ours, not above it:
        # match those by program name instead.
        name = psutil.Process(pid).name().lower()
        return name in CONSOLE_HOSTS and name in {p.name().lower() for p in chain}
    except Exception:
        return False


if opts.check_away is not None:
    sys.exit(1 if terminal_in_front(opts.check_away) else 0)

if opts.json:
    request = json.load(sys.stdin)
else:
    text = sys.stdin.read().strip() if opts.mode not in ("stop", "pause", "skip") else ""
    mode = "stop" if opts.mode in ("reply", "play") and not text else opts.mode
    request = {"text": text, "session": opts.session, "label": opts.label, "mode": mode, "all": opts.all}


def start_server():
    log = open(os.path.join(HOME, "server.log"), "a")
    if sys.platform == "win32":
        # pythonw has no console window; detached so it outlives this client.
        exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        subprocess.Popen([exe if os.path.exists(exe) else sys.executable, os.path.join(CODE, "server.py")],
                         stdout=subprocess.DEVNULL, stderr=log, creationflags=flags)
    else:
        subprocess.Popen([sys.executable, os.path.join(CODE, "server.py")],
                         stdout=subprocess.DEVNULL, stderr=log, start_new_session=True)


try:
    transport.send(request)
except transport.NotRunning:
    if request.get("mode") in ("stop", "pause", "skip"):
        sys.exit(0)  # nothing is playing without a server
    start_server()
    for _ in range(100):
        time.sleep(0.2)
        try:
            transport.send(request)
            break
        except transport.NotRunning:
            pass
