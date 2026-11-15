# Checks what uninstall undoes besides start at login: python3 tests/install_helper_test.py
# Everything runs in a temporary home, with a stand-in for the claude command.
import json, os, subprocess, sys, tempfile, threading

home = tempfile.mkdtemp()
os.environ["HOME"] = os.environ["USERPROFILE"] = home  # what ~ means, on every system
os.environ["KOKORO_HOME"] = os.path.join(home, ".claude", "kokoro")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import install_helper as helper  # noqa: E402
import transport  # noqa: E402

keys = os.path.join(home, ".claude", "keybindings.json")


def write_keys(data):
    with open(keys, "w") as f:
        json.dump(data, f)


# The shortcut: a file setup made for it alone goes; other bindings stay; a rebound ctrl+x s stays.
helper.remove_shortcut()  # no file yet: nothing to do
helper.shortcut()
helper.remove_shortcut()
assert not os.path.exists(keys)

mine = {"bindings": [{"context": "Global", "bindings": {"ctrl+k": "app:mine"}}, {"context": "Chat", "bindings": {"ctrl+j": "chat:x"}}]}
write_keys(mine)
helper.shortcut()
helper.remove_shortcut()
assert json.load(open(keys)) == mine, json.load(open(keys))

write_keys({"bindings": [{"context": "Chat", "bindings": {"ctrl+j": "chat:x"}}]})
helper.shortcut()  # adds a Global block of its own
helper.remove_shortcut()
assert json.load(open(keys)) == {"bindings": [{"context": "Chat", "bindings": {"ctrl+j": "chat:x"}}]}

rebound = {"bindings": [{"context": "Global", "bindings": {"ctrl+x s": "app:other"}}]}
write_keys(rebound)
helper.remove_shortcut()
assert json.load(open(keys)) == rebound

# The plugin: uninstalled with its marketplace through the claude command.
calls = []
listed = "  ❯ speak-pane@claude-voice\n"
failing = set()


def fake_run(cmd, **_):
    calls.append(cmd[1:])
    return subprocess.CompletedProcess(cmd, 1 if cmd[2] in failing else 0, listed if cmd[1:] == ["plugin", "list"] else "", "")


helper.subprocess.run, real_run = fake_run, subprocess.run
helper.claude_cli = lambda: "claude"
assert helper.remove_plugin()
assert calls == [["plugin", "list"], ["plugin", "uninstall", "speak-pane@claude-voice"],
                 ["plugin", "marketplace", "remove", "claude-voice"]], calls

calls.clear()
listed = "  ❯ firebase@firebase\n"  # the plugin went first: only the marketplace is left
assert helper.remove_plugin()
assert calls == [["plugin", "list"], ["plugin", "marketplace", "remove", "claude-voice"]], calls

calls.clear()
listed, failing = "  ❯ speak-pane@claude-voice\n", {"uninstall"}
assert not helper.remove_plugin()  # says how to do it by hand, and keeps the marketplace
assert calls == [["plugin", "list"], ["plugin", "uninstall", "speak-pane@claude-voice"]], calls

calls.clear()
helper.claude_cli = lambda: None
assert not helper.remove_plugin() and calls == []
helper.subprocess.run = real_run

# The server: a running one is asked to quit; with none running there is nothing to do.
server, token = transport.listen()
got = []


def serve_one():
    conn, _ = server.accept()
    with conn:
        data = b""
        while chunk := conn.recv(65536):
            data += chunk
    got.append(json.loads(data))


t = threading.Thread(target=serve_one)
t.start()
helper.stop_server()
t.join(5)
transport.close(server)
assert [{k: v for k, v in r.items() if k != "token"} for r in got] == [{"mode": "quit"}], got
assert not token or got[0]["token"] == token
helper.stop_server()  # no server now

# Disconnecting agents that were never connected creates none of their files.
helper.connect(["codex", "gemini", "cursor"], on=False)
for made in (".codex", ".gemini", ".cursor"):
    assert not os.path.exists(os.path.join(home, made)), made
print("install_helper: all checks pass")
