#!/usr/bin/env python3
# Kokoro TTS server: keeps the model loaded, listens on a unix socket.
# Each connection sends one JSON request (see handle); replies queue across sessions.
# Progress is written to status.json for the Speak pane. Exits after IDLE_TIMEOUT idle.
#
# Code lives beside this file; data (venv, models, settings, status, log, socket) lives in
# KOKORO_HOME, ~/.claude/kokoro by default.
import json, os, re, socket, sys, threading, time, queue

CODE = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("KOKORO_HOME", os.path.expanduser("~/.claude/kokoro"))
SOCK = os.path.join(HOME, "kokoro.sock")
LEXICON = os.path.join(HOME, "pronounce.json")
DEFAULT_LEXICON = os.path.join(CODE, "pronounce.default.json")
SETTINGS = os.path.join(HOME, "settings.json")
STATUS = os.path.join(HOME, "status.json")
LOG = os.path.join(HOME, "server.log")
LOG_KEEP = 300  # lines kept when the log is trimmed
LOG_MAX_BYTES = 64 * 1024
DEFAULTS = {"voice": "af_heart", "speed": 1.1}
MAX_CHUNK = 250  # chars; plain-text requests group sentences up to this size
IDLE_TIMEOUT = 3600
MULTI_WINDOW = 1800  # sessions heard from within this window count as active


def settings():
    """Voice and speed from settings.json (written by the Speak pane), re-read per reply."""
    try:
        return {**DEFAULTS, **json.load(open(SETTINGS))}
    except Exception:
        return dict(DEFAULTS)


def pronounce(text):
    """Swaps words for spellings Kokoro says right, per pronounce.json (re-read each time)."""
    try:
        path = LEXICON if os.path.exists(LEXICON) else DEFAULT_LEXICON
        words = {k: v for k, v in json.load(open(path)).items() if not k.startswith("_")}
    except Exception:
        return text
    if not words:
        return text
    keys = sorted(words, key=len, reverse=True)
    pattern = re.compile(r"(?<![\w.])(" + "|".join(map(re.escape, keys)) + r")(?![\w])")
    return pattern.sub(lambda m: words[m.group(1)], text)


URL = re.compile(r"https?://(?:www\.)?([^/\s)]+)\S*")
# A path: rooted (/, ~/, ./, ../) or with at least two slashes; "and/or" is left alone.
PATH = re.compile(r"(?<![\w@:])(?:(?:~|\.{1,2})?/(?:[\w.@-]+/)*|(?:[\w.@-]+/){2,})([\w.@-]+)/?")
FILE_LINE = re.compile(r"\b([\w-]+\.[A-Za-z]\w*):(\d+)\b")
TABLE_LINE = re.compile(r"^\s*\|.*$", re.M)


# "live" in replies is nearly always the adjective (the site is live, live reload); the phonemizer
# sometimes picks the verb (after "already"). Say it as "lyve" unless a subject or "to" comes right before.
LIVE = re.compile(r"(?<!\bI )(?<!\byou )(?<!\bwe )(?<!\bthey )(?<!\bto )(?<!\bwho )(?<!\bpeople )\b([Ll])ive\b")


VERSION = re.compile(r"\b\d+(?:\.\d+){1,3}\b")
DOTTED = re.compile(r"\b([A-Za-z_][\w-]*)\.(?=[A-Za-z_\d])")  # server.py, careem.com


def speakable(text):
    """What is said for a piece of text: links as their site, paths as their file, no tables."""
    text = TABLE_LINE.sub(" ", text)
    text = URL.sub(lambda m: m.group(1), text)
    text = PATH.sub(lambda m: m.group(1), text)
    text = FILE_LINE.sub(r"\1 line \2", text)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"[*#`_>|]", "", text)
    text = LIVE.sub(lambda m: m.group(1) + "yve", text)
    text = re.sub(r":\s*$", ".", text)  # a label ending in a colon is said as a full sentence
    text = pronounce(text).strip()
    text = VERSION.sub(lambda m: m.group(0).replace(".", " point "), text)
    text = DOTTED.sub(r"\1 dot ", text)
    # Kokoro can stutter on the last sound of a sentence that ends without punctuation.
    if text and not re.search(r"[.!?][\"')\]]*$", text):
        text += "."
    return text


def chunks(text):
    sentences = [s.strip() for s in re.split(r"(?<=[.!?;])\s+|\n+", text) if s.strip()]
    if not sentences:
        return []
    out = [sentences[0]]  # first chunk alone so speech starts fast
    cur = ""
    for s in sentences[1:]:
        if cur and len(cur) + len(s) > MAX_CHUNK:
            out.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        out.append(cur)
    return out


def log(*parts):
    """One line to server.log (this process's stderr), keeping the file to its last lines."""
    print(time.strftime("%H:%M:%S"), *parts, file=sys.stderr, flush=True)
    try:
        if os.path.getsize(LOG) > LOG_MAX_BYTES:
            trim_log()
    except OSError:
        pass


def trim_log():
    try:
        lines = open(LOG).readlines()[-LOG_KEEP:]
        with open(LOG, "w") as f:  # stderr appends, so it carries on at the new end
            f.writelines(lines)
    except OSError:
        pass


def write_status(**fields):
    tmp = STATUS + ".tmp"
    with open(tmp, "w") as f:
        json.dump({**fields, "t": time.time()}, f)
    os.replace(tmp, STATUS)


FADE_SECONDS = 0.012  # fade on pause, resume, skip and stop, so cuts do not click
BLOCK_SECONDS = 0.05  # audio is written in blocks this long
LATENCY_SECONDS = 0.2  # device buffer: slack for the moments generation keeps Python busy
stream = None
underruns = 0


def output(rate):
    """The one output stream, kept open while speaking so sentences join without a gap."""
    global stream
    if stream is not None and stream.samplerate != rate:
        stream.close()
        stream = None
    if stream is None:
        stream = sd.OutputStream(samplerate=rate, channels=1, dtype="float32", latency=LATENCY_SECONDS)
    if not stream.active:
        stream.start()
        write(stream, np.zeros(int(LATENCY_SECONDS * rate), dtype="float32"), quiet=True)  # prime the buffer
    return stream


def write(out, data, quiet=False):
    global underruns
    if out.write(np.ascontiguousarray(data, dtype="float32").reshape(-1, 1)) and not quiet:
        underruns += 1
        log("underrun", underruns)


def feed_silence(seconds=BLOCK_SECONDS):
    """Silence for an open stream, so a gap never leaves the device replaying its last block."""
    if stream is not None and stream.active:
        write(stream, np.zeros(int(seconds * stream.samplerate), dtype="float32"))


def rest_output():
    """Lets the device go idle once nothing is left to say: the buffer plays out over silence."""
    if stream is not None and stream.active:
        feed_silence(LATENCY_SECONDS + BLOCK_SECONDS)
        stream.stop()


MAX_PAUSE = 2.0  # seconds; a longer requested pause is cut to this


def items_of(req):
    """(sentence index or None, text, seconds of quiet before it) for each piece of a request."""
    if req.get("sentences"):
        offset = int(req.get("offset") or 0)
        pauses = req.get("pauses") or []
        return [(offset + i, t, min(MAX_PAUSE, float(pauses[i]) if i < len(pauses) else 0.0))
                for i, t in enumerate(req["sentences"])]
    return [(None, c, 0.0) for c in chunks(req.get("text", ""))]


class Job:
    """One request being spoken: items are (sentence index or None, text).

    While a job is still speaking it accepts more items (streamed sentences of the same reply);
    once everything it produced has played and nothing is left, it closes.
    """

    def __init__(self, req):
        self.session = req.get("session", "")
        self.ref = req.get("ref")
        self.stop = threading.Event()
        self.running = threading.Event()  # cleared while paused
        self.running.set()
        self.index = None
        self.speaking = False
        self.skipping = False
        self.accepting = True
        self.done = threading.Event()
        self.ready = queue.Queue()
        self.items = items_of(req)
        if req.get("prefix"):
            self.items.insert(0, (None, req["prefix"], 0.0))
        threading.Thread(target=self.produce, daemon=True).start()
        threading.Thread(target=self.play, daemon=True).start()

    def produce(self):
        while True:
            with cond:
                while not self.items:
                    if self.stop.is_set() or (self.ready.empty() and not self.speaking):
                        self.accepting = False
                        self.ready.put(None)
                        return
                    cond.wait(0.1)
                index, text, pause = self.items.pop(0)
            if self.stop.is_set():
                continue
            said = speakable(text)
            if not said:
                continue
            conf = settings()  # per sentence, so a voice or speed change applies right away
            try:
                with model_lock:
                    samples, rate = kokoro.create(said, voice=conf["voice"], speed=float(conf["speed"]), lang="en-us")
            except Exception as err:
                log("tts error:", err)
                continue
            self.ready.put((index, samples.astype("float32"), rate, pause))

    def report(self, state):
        write_status(session=self.session, ref=self.ref, index=self.index, state=state)

    def play(self):
        while True:
            try:
                item = self.ready.get(timeout=BLOCK_SECONDS)
            except queue.Empty:
                feed_silence()  # the next sentence is not ready: keep the device fed
                continue
            if item is None:
                break
            index, samples, rate, pause = item
            self.speaking = True
            try:
                self.running.wait()
                if pause and not self.stop.is_set():
                    feed_silence(pause)  # a breath before a new paragraph or list item
                if not self.stop.is_set():
                    self.index = index
                    self.report("playing")
                    self.play_samples(samples, rate)
            finally:
                self.speaking = False
        with cond:
            idle = not pending
        if idle:
            rest_output()
        self.done.set()
        with cond:
            if not pending:
                write_status(session=self.session, ref=None, index=None, state="idle")
            cond.notify_all()

    def play_samples(self, samples, rate):
        """Writes one sentence to the shared output stream in small blocks. Pausing fades to
        silence and keeps feeding silence (so the device never runs dry); resuming fades back in
        from the same sample. Skip and stop fade out, then end."""
        self.skipping = False
        out = output(rate)
        pos = 0
        gain = 1.0 if self.running.is_set() else 0.0
        step = 1.0 / max(1, int(FADE_SECONDS * rate))
        block = int(BLOCK_SECONDS * rate)
        while True:
            ending = self.stop.is_set() or self.skipping
            target = 0.0 if ending or not self.running.is_set() else 1.0
            if gain == 0.0 and target == 0.0:
                if ending:
                    return
                write(out, np.zeros(block, dtype="float32"))  # paused: hold the position
                continue
            if pos >= len(samples):
                return
            ramp = np.clip(gain + np.sign(target - gain) * step * np.arange(1, block + 1), 0.0, 1.0)
            chunk = samples[pos:pos + block]
            write(out, chunk * ramp[:len(chunk)])
            pos += len(chunk)
            gain = float(ramp[len(chunk) - 1])

    def toggle_pause(self):
        if self.running.is_set():
            self.running.clear()
            self.report("paused")
        else:
            self.running.set()
            self.report("playing")

    def skip(self):
        self.skipping = True
        self.running.set()

    def cancel(self):
        self.stop.set()
        self.running.set()


# Requests: {"session", "label", "mode", "all", "ref", "offset", and "sentences" (with "pauses",
# seconds of quiet before each) or "text"}.
#   reply: queued behind other sessions' audio; replaces the same session's older reply.
#   play:  a person pressed play; cuts in now, other sessions' queued replies wait.
#   stop:  silences one session (or every session with "all").
#   pause: pauses the reply playing now, or resumes it.
#   skip:  ends the sentence playing now; the reply goes on with the next.
#   "append": true adds streamed sentences to the same session's reply ("ref") instead of replacing it.
cond = threading.Condition()
pending = []
current = None
seen = {}


def busy():
    return bool(pending) or (current is not None and not current.done.is_set())


def drop(session):
    pending[:] = [r for r in pending if r.get("session", "") != session]
    if current is not None and current.session == session:
        current.cancel()


def append(req):
    """Adds streamed sentences to the same reply, speaking or still waiting; False if neither."""
    session, ref = req.get("session", ""), req.get("ref")
    if current is not None and current.session == session and current.ref == ref and current.accepting \
            and not current.stop.is_set():
        current.items.extend(items_of(req))
        return True
    for queued in pending:
        if queued.get("session", "") == session and queued.get("ref") == ref:
            old, new = queued.get("sentences") or [], req.get("sentences", [])
            queued["pauses"] = (queued.get("pauses") or [0.0] * len(old)) + (req.get("pauses") or [0.0] * len(new))
            queued["sentences"] = old + new
            return True
    return False


def handle(req):
    mode = req.get("mode", "play")
    session = req.get("session", "")
    now = time.time()
    with cond:
        if session:
            seen[session] = now
        if mode == "stop":
            if req.get("all"):
                pending.clear()
                if current is not None:
                    current.cancel()
            else:
                drop(session)
        elif mode == "pause":
            if current is not None and not current.done.is_set():
                current.toggle_pause()
        elif mode == "skip":
            if current is not None and not current.done.is_set():
                current.skip()
        elif req.get("append") and append(req):
            pass
        elif req.get("sentences") or req.get("text", "").strip():
            if not req.get("append"):
                drop(session)
            if mode == "reply":
                active = [s for s, t in seen.items() if now - t < MULTI_WINDOW]
                if len(active) > 1 and req.get("label") and not req.get("append"):
                    req = {**req, "prefix": f"From {req['label']}."}
                pending.append(req)
            else:
                if current is not None:
                    current.cancel()
                pending.insert(0, req)
        cond.notify_all()


def worker():
    global current
    while True:
        with cond:
            while not pending or (current is not None and not current.done.is_set()):
                cond.wait(timeout=1)
            req = pending.pop(0)
            first = (req.get("sentences") or [req.get("text", "")])[0]
            log("play", req.get("session") or "-", first[:60])
            current = Job(req)


def parse(data):
    """A JSON request, or plain text from an older client (played at once)."""
    text = data.decode(errors="ignore")
    try:
        req = json.loads(text)
        if isinstance(req, dict):
            return req
    except ValueError:
        pass
    return {"text": text, "mode": "play" if text.strip() else "stop", "all": True}


def bind():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.bind(SOCK)
    except OSError:
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.connect(SOCK)
            raise SystemExit(0)  # another server is already running
        except (ConnectionRefusedError, FileNotFoundError):
            os.unlink(SOCK)  # stale socket from a dead server
            s.bind(SOCK)
    s.listen(4)
    s.settimeout(15)
    return s


if __name__ == "__main__":
    server = bind()
    try:
        import numpy as np
        import sounddevice as sd
        from kokoro_onnx import Kokoro

        trim_log()
        log("server started")
        kokoro = Kokoro(os.path.join(HOME, "kokoro-v1.0.onnx"), os.path.join(HOME, "voices-v1.0.bin"))
        model_lock = threading.Lock()
        threading.Thread(target=worker, daemon=True).start()
        last_active = time.time()
        while True:
            try:
                conn, _ = server.accept()
            except socket.timeout:
                with cond:
                    if busy():
                        last_active = time.time()
                    elif time.time() - last_active > IDLE_TIMEOUT:
                        break
                continue
            with conn:
                conn.settimeout(5)
                data = b""
                while chunk := conn.recv(65536):
                    data += chunk
            handle(parse(data))
            last_active = time.time()
    finally:
        server.close()
        try:
            os.unlink(SOCK)
        except FileNotFoundError:
            pass
        write_status(session="", ref=None, index=None, state="idle")
