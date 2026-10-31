#!/usr/bin/env python3
# An MCP server (stdio) with `speak` and `stop_speaking` tools, for any MCP client: Claude Desktop,
# Cursor, Codex, Gemini CLI and others. Speech goes through the same Kokoro server and queue as
# Claude Code's replies. No dependencies beyond this folder.
import json, os, subprocess, sys

CODE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CODE)
from markdown import pauses_for, to_sentences  # noqa: E402

TOOLS = [
    {
        "name": "speak",
        "description": "Read text aloud to the user with a natural local voice. Use for a short spoken "
                       "summary when the user wants to hear results, e.g. while away from the screen. "
                       "Markdown is fine: code blocks and tables are described, not read.",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string", "description": "What to say"}},
                        "required": ["text"]},
    },
    {
        "name": "stop_speaking",
        "description": "Stop reading aloud.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]
client = {"name": "MCP"}
session = f"mcp:{os.getppid()}"


def tts(req):
    home = os.environ.get("KOKORO_HOME", os.path.expanduser("~/.claude/kokoro"))
    try:
        conf = json.load(open(os.path.join(home, "settings.json")))
    except Exception:
        conf = {}
    if req["mode"] != "stop" and conf.get("muted"):
        return "The voice is muted."
    label = f"{client['name']} in {os.path.basename(os.getcwd())}"
    subprocess.run([sys.executable, os.path.join(CODE, "tts.py"), "--json"],
                   input=json.dumps({**req, "session": session, "label": label}), text=True,
                   capture_output=True, timeout=30)
    return "Stopped." if req["mode"] == "stop" else "Speaking."


def call(name, args):
    if name == "speak":
        sentences = to_sentences(str(args.get("text", "")))
        if not sentences:
            return "Nothing to say."
        return tts({"mode": "reply", "sentences": [s[0] for s in sentences], "pauses": pauses_for(sentences),
                    "ref": None})
    if name == "stop_speaking":
        return tts({"mode": "stop"})
    raise KeyError(name)


def answer(msg):
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if method == "initialize":
        client["name"] = (params.get("clientInfo") or {}).get("name") or "MCP"
        return {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}}, "serverInfo": {"name": "claude-voice", "version": "1.0.0"}}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        try:
            text = call(params.get("name"), params.get("arguments") or {})
            return {"content": [{"type": "text", "text": text}]}
        except KeyError:
            return {"content": [{"type": "text", "text": f"Unknown tool {params.get('name')}"}], "isError": True}
        except Exception as err:
            return {"content": [{"type": "text", "text": f"Voice error: {err}"}], "isError": True}
    if method == "ping":
        return {}
    if mid is not None:
        raise LookupError(method)
    return None


for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    if "id" not in msg:
        answer(msg)  # a notification: no reply
        continue
    try:
        reply = {"jsonrpc": "2.0", "id": msg["id"], "result": answer(msg)}
    except LookupError as err:
        reply = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": f"Method not found: {err}"}}
    sys.stdout.write(json.dumps(reply) + "\n")
    sys.stdout.flush()
