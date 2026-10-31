#!/usr/bin/env python3
# Reads replies from other coding agents aloud through the same Kokoro server as Claude Code.
#   agent_hook.py codex '<json>'   Codex `notify` (JSON as the last argument)
#   agent_hook.py gemini           Gemini CLI hook, AfterAgent or Notification (JSON on stdin)
#   agent_hook.py cursor           Cursor hook, afterAgentResponse (JSON on stdin)
# The hook returns at once: muting, away checks and speech happen in a detached worker,
# so the agent never waits on the voice. Settings are the Speak pane's (settings.json).
import json, os, subprocess, sys, time

CODE = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("KOKORO_HOME", os.path.expanduser("~/.claude/kokoro"))
SUMMARY_OVER = 1200  # chars; with summaries on, longer replies are cut to their first paragraph


def project(path):
    return os.path.basename(os.path.normpath(path)) if path else ""


def event(agent, data):
    """(kind 'reply' or 'alert', text, session, project) from an agent's hook payload, or None."""
    if agent == "codex":
        if data.get("type") != "agent-turn-complete":
            return None
        return "reply", data.get("last-assistant-message") or "", data.get("thread-id", ""), project(data.get("cwd"))
    if agent == "gemini":
        where = project(data.get("cwd"))
        if data.get("hook_event_name") == "Notification":
            message = data.get("message") or ""
            tool = message.removeprefix("Tool ").split(" requires ")[0] if message.startswith("Tool ") else ""
            return "alert", f"I need permission to use {tool}." if tool else "I need your confirmation.", \
                data.get("session_id", ""), where
        return "reply", data.get("prompt_response") or "", data.get("session_id", ""), where
    if agent == "cursor":
        roots = data.get("workspace_roots") or [""]
        return "reply", data.get("text") or "", data.get("conversation_id", ""), project(roots[0])
    return None


def settings():
    try:
        return json.load(open(os.path.join(HOME, "settings.json")))
    except Exception:
        return {}


def run(cmd, stdin=None):
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=30)


def work(req):
    """In the detached worker: honours mute and away settings, then hands the request to tts.py."""
    conf = settings()
    if conf.get("muted") or conf.get("mutedUntil", 0) > time.time() * 1000:
        return
    tts = os.path.join(CODE, "tts.py")
    must_be_away = req.pop("alert", False) or conf.get("awayOnly")
    if must_be_away and run([sys.executable, tts, "--check-away", os.environ.get("__CFBundleIdentifier", "")]).returncode != 0:
        return  # the terminal is in front: the person can see it
    run([sys.executable, tts, "--json"], json.dumps(req))


def request(agent, kind, text, session, where):
    from markdown import pauses_for, to_sentences

    label = f"{agent.capitalize()} in {where}" if where else agent.capitalize()
    if kind == "alert":
        return {"mode": "reply", "urgent": True, "alert": True, "sentences": [text], "ref": None,
                "session": f"{agent}:{session}", "label": label}
    sentences = to_sentences(text)
    if settings().get("summarize", True) and sum(len(s[0]) for s in sentences) > SUMMARY_OVER:
        first = sentences[0][1] if sentences else 0
        sentences = [s for s in sentences if s[1] == first] + [("The rest is on screen.", first + 1, -1)]
    if not sentences:
        return None
    return {"mode": "reply", "sentences": [s[0] for s in sentences], "pauses": pauses_for(sentences), "ref": None,
            "session": f"{agent}:{session}", "label": label}


def detach(req):
    """Starts this script again as a worker that outlives the hook."""
    cmd = [sys.executable, os.path.abspath(__file__), "--work"]
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, creationflags=flags, text=True)
    else:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, start_new_session=True, text=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p.stdin.write(json.dumps(req))
    p.stdin.close()


def main():
    sys.path.insert(0, CODE)
    if sys.argv[1:2] == ["--work"]:
        work(json.load(sys.stdin))
        return
    agent = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        raw = sys.argv[-1] if agent == "codex" else sys.stdin.read()
        found = event(agent, json.loads(raw or "{}"))
    except ValueError:
        return
    if found and found[1].strip():
        req = request(agent, *found)
        if req:
            detach(req)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # a voice problem must never break the agent's turn
