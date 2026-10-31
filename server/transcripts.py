# Finished Claude Code replies, read from its session files (~/.claude/projects/*/<session>.jsonl),
# so replies are listed and spoken without the Claude Code plugin. Also reads each session's title.
#
# A reply is the text of a turn's final assistant message (stop_reason "end_turn"), the part after
# its last tool call; subagents (isSidechain) are skipped. Files are followed from where they ended
# when first seen, so old replies are never re-spoken.
import glob, json, os, threading, time

PROJECTS = os.path.expanduser("~/.claude/projects")
RECENT = 15 * 60  # only files written in the last 15 minutes are followed
SETTLE = 1.5  # seconds a file must be quiet before its finished reply is handed on
SCAN_EVERY = 1.0


class Session:
    def __init__(self, path, offset):
        self.path = path
        self.offset = offset
        self.session = os.path.splitext(os.path.basename(path))[0]
        self.title = ""
        self.cwd = ""
        self.texts = []  # text of the turn's final message so far
        self.message = None  # its message id
        self.done = False  # that message ended the turn
        self.changed = 0.0


class Watcher:
    """Calls on_reply(session, cwd, title, reply_id, text) for each finished reply, and on_title(session, title)."""

    def __init__(self, on_reply, on_title, projects=PROJECTS):
        self.on_reply = on_reply
        self.on_title = on_title
        self.projects = projects
        self.sessions = {}
        self.ends = None  # path -> size at the first scan: older content is never spoken

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()
        return self

    def run(self):
        while True:
            try:
                self.scan()
            except Exception:
                pass
            time.sleep(SCAN_EVERY)

    def scan(self, now=None):
        now = now or time.time()
        first = self.ends is None
        if first:
            self.ends = {}
        for path in glob.glob(os.path.join(self.projects, "*", "*.jsonl")):
            try:
                st = os.stat(path)
            except OSError:
                continue
            if first:
                self.ends[path] = st.st_size
            s = self.sessions.get(path)
            if s is None:
                if path in self.ends:
                    # Seen at start-up: followed from where it ended then, once it is in use again
                    # (an old session resumed later must not have its whole history read out).
                    if now - st.st_mtime > RECENT and st.st_size == self.ends[path]:
                        continue
                    s = Session(path, self.ends[path])
                else:
                    s = Session(path, 0)  # created since start-up
                self.sessions[path] = s
                self.read_title(s)
            if st.st_size > s.offset:
                self.read(s, now)
            elif st.st_size < s.offset:
                s.offset = 0  # rewritten
            if s.done and now - s.changed >= SETTLE:
                self.finish(s)

    def read_title(self, s):
        """The latest title in the whole file, written by Claude Code as it names the session."""
        try:
            with open(s.path, "rb") as f:
                for line in f:
                    if b'"ai-title"' in line:
                        title = json.loads(line).get("aiTitle")
                        if title:
                            s.title = title
                    if not s.cwd and b'"cwd"' in line:
                        s.cwd = json.loads(line).get("cwd") or ""
        except (OSError, ValueError):
            pass
        if s.title:
            self.on_title(s.session, s.title)

    def read(self, s, now):
        with open(s.path, "rb") as f:
            f.seek(s.offset)
            data = f.read()
        end = data.rfind(b"\n") + 1  # a line still being written waits for the next look
        if end == 0:
            return
        s.offset += end
        s.changed = now
        for line in data[:end].splitlines():
            try:
                self.entry(s, json.loads(line))
            except ValueError:
                continue

    def entry(self, s, e):
        kind = e.get("type")
        if kind == "ai-title" and e.get("aiTitle"):
            if e["aiTitle"] != s.title:
                s.title = e["aiTitle"]
                self.on_title(s.session, s.title)
            return
        if e.get("isSidechain") or e.get("isMeta"):
            return
        if e.get("cwd"):
            s.cwd = e["cwd"]
        message = e.get("message") or {}
        content = message.get("content")
        if kind == "user":
            prompt = isinstance(content, str) or (
                isinstance(content, list) and not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content))
            if prompt:
                self.finish(s)
                self.reset(s)
            return
        if kind != "assistant" or not isinstance(content, list):
            return
        if message.get("id") != s.message:
            if s.done:
                self.finish(s)
            self.reset(s)
            s.message = message.get("id")
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                s.texts = []
            elif isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip():
                s.texts.append(b["text"])
        if message.get("stop_reason") == "end_turn":
            s.done = True

    def reset(self, s):
        s.texts, s.message, s.done = [], None, False

    def finish(self, s):
        if s.done and s.texts:
            self.on_reply(s.session, s.cwd, s.title, s.message or f"{s.session}:{s.offset}", "\n\n".join(s.texts))
        self.reset(s)
