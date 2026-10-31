# Checks the Claude Code session-file reader: python3 tests/transcripts_test.py
import json, os, sys, tempfile, time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import transcripts  # noqa: E402

def line(**e):
    return json.dumps(e) + "\n"

def assistant(mid, blocks, stop, **extra):
    return line(type="assistant", cwd="/work/shop", message={"id": mid, "content": blocks, "stop_reason": stop}, **extra)

replies, titles = [], []
with tempfile.TemporaryDirectory() as root:
    os.makedirs(os.path.join(root, "proj"))
    path = os.path.join(root, "proj", "sess-1.jsonl")
    w = transcripts.Watcher(lambda *a: replies.append(a), lambda s, t: titles.append(t), projects=root)
    w.started = time.time() - 60  # the file is new: read from its start
    with open(path, "w") as f:
        f.write(line(type="user", cwd="/work/shop", message={"content": "fix the login"}))
        f.write(assistant("m1", [{"type": "text", "text": "Let me look."}], "tool_use"))
        f.write(assistant("m1", [{"type": "tool_use", "id": "t1", "name": "Read"}], "tool_use"))
        f.write(line(type="user", message={"content": [{"type": "tool_result", "tool_use_id": "t1"}]}))
        f.write(assistant("x9", [{"type": "text", "text": "Subagent chatter."}], "end_turn", isSidechain=True))
        f.write(assistant("m2", [{"type": "thinking", "thinking": "..."}], "end_turn"))
        f.write(assistant("m2", [{"type": "text", "text": "Fixed the login."}], "end_turn"))
        f.write(assistant("m2", [{"type": "text", "text": "Tests pass."}], "end_turn"))
        f.write(line(type="ai-title", aiTitle="Login fix", sessionId="sess-1"))
    now = time.time()
    w.scan(now)
    assert replies == [], "a reply waits until the file is quiet"
    w.scan(now + transcripts.SETTLE + 0.1)
    assert len(replies) == 1, replies
    session, cwd, title, reply_id, text = replies[0]
    assert (session, cwd, title, reply_id) == ("sess-1", "/work/shop", "Login fix", "m2"), replies[0]
    assert text == "Fixed the login.\n\nTests pass.", text  # not the narration, not the subagent
    assert titles == ["Login fix"], titles

    with open(path, "a") as f:  # the next turn
        f.write(line(type="user", message={"content": "thanks"}))
        f.write(assistant("m3", [{"type": "text", "text": "You're welcome."}], "end_turn"))
    w.scan(now + 10)
    w.scan(now + 20)
    assert [r[4] for r in replies] == ["Fixed the login.\n\nTests pass.", "You're welcome."], replies
print("transcripts: all checks pass")
