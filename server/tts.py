#!/usr/bin/env python3
# Client for the Kokoro server, starting the server when needed.
#   tts.py --json                  request JSON on stdin (see server.handle)
#   tts.py [--session ID] [--label NAME] [--mode reply|play|stop|pause|skip] [--all]   text on stdin
#   tts.py --check-away BUNDLE_ID  exit 0 when that app is not frontmost, 1 when it is
import argparse, json, os, socket, subprocess, sys, time

CODE = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("KOKORO_HOME", os.path.expanduser("~/.claude/kokoro"))
SOCK = os.path.join(HOME, "kokoro.sock")

args = argparse.ArgumentParser()
args.add_argument("--json", action="store_true")
args.add_argument("--check-away")
args.add_argument("--session", default="")
args.add_argument("--label", default="")
args.add_argument("--mode", choices=["reply", "play", "stop", "pause", "skip"], default="play")
args.add_argument("--all", action="store_true")
opts = args.parse_args()

if opts.check_away is not None:
    front = subprocess.run(["lsappinfo", "front"], capture_output=True, text=True).stdout.strip()
    info = subprocess.run(["lsappinfo", "info", "-only", "bundleid", front], capture_output=True, text=True).stdout
    sys.exit(1 if f'"{opts.check_away}"' in info else 0)

if opts.json:
    request = json.load(sys.stdin)
else:
    text = sys.stdin.read().strip() if opts.mode not in ("stop", "pause", "skip") else ""
    mode = "stop" if opts.mode in ("reply", "play") and not text else opts.mode
    request = {"text": text, "session": opts.session, "label": opts.label, "mode": mode, "all": opts.all}
payload = json.dumps(request).encode()


def send():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(SOCK)
    s.sendall(payload)
    s.close()


try:
    send()
except (ConnectionRefusedError, FileNotFoundError):
    if request.get("mode") in ("stop", "pause", "skip"):
        sys.exit(0)  # nothing is playing without a server
    subprocess.Popen([sys.executable, os.path.join(CODE, "server.py")],
                     stdout=subprocess.DEVNULL, stderr=open(os.path.join(HOME, "server.log"), "a"),
                     start_new_session=True)
    for _ in range(100):
        time.sleep(0.2)
        try:
            send()
            break
        except (ConnectionRefusedError, FileNotFoundError):
            pass
