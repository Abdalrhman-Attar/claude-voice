#!/usr/bin/env python3
# Kokoro TTS server: keeps the model loaded, listens on a local socket (see transport.py).
# Each connection sends one JSON request (see handle); replies queue across sessions.
# Progress is written to status.json for the Speak pane. Exits after IDLE_TIMEOUT idle.
#
# Code lives beside this file; data (venv, models, settings, status, log, socket) lives in
# KOKORO_HOME, ~/.claude/kokoro by default.
import json, os, re, signal, socket, sys, threading, time, queue, zlib
import arabic as arabic_voice, magpie, system_voice, transport
from transcripts import Watcher
from collections import OrderedDict

CODE = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("KOKORO_HOME", os.path.expanduser("~/.claude/kokoro"))
LEXICON = os.path.join(HOME, "pronounce.json")
DEFAULT_LEXICON = os.path.join(CODE, "pronounce.default.json")
SETTINGS = os.path.join(HOME, "settings.json")
STATUS = os.path.join(HOME, "status.json")
FEED = os.path.join(HOME, "replies.json")  # recent replies from every agent, for the VS Code panel
FEED_KEEP = 200
LOG = os.path.join(HOME, "server.log")
LOG_KEEP = 300  # lines kept when the log is trimmed
LOG_MAX_BYTES = 64 * 1024
DEFAULTS = {"voice": "af_heart", "speed": 1.1, "projectVoices": False}
# Voices handed out per project when "projectVoices" is on, so sessions can be told apart by ear.
PROJECT_VOICES = ["af_heart", "am_michael", "bf_emma", "am_adam", "af_bella", "bm_george", "af_nicole", "af_sky"]
# Kokoro voice prefix -> espeak language, so a British voice gets British pronunciation.
LANGS = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi", "i": "it", "j": "ja", "p": "pt-br", "z": "cmn"}
MAX_CHUNK = 250  # chars; plain-text requests group sentences up to this size
IDLE_TIMEOUT = 3600
MULTI_WINDOW = 1800  # sessions heard from within this window count as active


def settings():
    """Voice and speed from settings.json (written by the Speak pane), re-read per reply."""
    try:
        return {**DEFAULTS, **json.load(open(SETTINGS))}
    except Exception:
        return dict(DEFAULTS)


def voice_for(conf, label):
    """The voice for a project: its own when per-project voices are on, else the chosen one."""
    if conf.get("projectVoices") and label:
        return PROJECT_VOICES[zlib.crc32(label.encode()) % len(PROJECT_VOICES)]
    return conf["voice"]


def lang_for(voice):
    return LANGS.get(voice[:1], "en-us")


def lexicon():
    """The person's pronounce.json, with the default list's words they do not have (re-read each time)."""
    words = {}
    for path in (DEFAULT_LEXICON, LEXICON):
        try:
            words.update({k: v for k, v in json.load(open(path)).items() if not k.startswith("_")})
        except Exception:
            pass
    return words


def pronounce(text, words=None):
    """Swaps words for spellings Kokoro says right, per the lexicon."""
    words = lexicon() if words is None else words
    if not words:
        return text
    keys = sorted(words, key=len, reverse=True)
    pattern = re.compile(r"(?<![\w.])(" + "|".join(map(re.escape, keys)) + r")(?![\w])")
    return pattern.sub(lambda m: words[m.group(1)], text)


URL = re.compile(r"https?://(?:www\.)?([^/\s)]+)\S*")
# A path: rooted (/, ~/, ./, ../) or with at least two slashes; "and/or" is left alone.
PATH = re.compile(r"(?<![\w@:])(?:(?:~|\.{1,2})?/(?:[\w.@-]+/)*|(?:[\w.@-]+/){2,})([\w.@-]+)/?")
FILE_LINE = re.compile(r"\b(?:[\w.-]+/)*([\w-]+\.[A-Za-z]\w*):(\d+)\b")
FILE_REF = re.compile(r"\s*\(\s*`?[\w./~-]+\.\w+:\d+(?:-\d+)?`?\s*\)")  # "(server.py:71)" after a mention
TABLE_LINE = re.compile(r"^\s*\|.*$", re.M)


# "live" in replies is nearly always the adjective (the site is live, live reload); the phonemizer
# sometimes picks the verb (after "already"). Say it as "lyve" unless a subject or "to" comes right before.
LIVE = re.compile(r"(?<!\bI )(?<!\byou )(?<!\bwe )(?<!\bthey )(?<!\bto )(?<!\bwho )(?<!\bpeople )\b([Ll])ive\b")


# Secrets are never read out: keys, bearer tokens and --token style flags become "redacted".
SECRETS = [
    (re.compile(r"\b(?:sk|pk|rk)[-_](?:live_|test_|proj-|ant-)?[-_A-Za-z0-9]{16,}"), "a redacted key"),
    (re.compile(r"\b(?:ghp|gho|ghs|ghu|github_pat|xox[abpr]|glpat)[-_][A-Za-z0-9_]{16,}"), "a redacted key"),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|\bAIza[-_A-Za-z0-9]{30,}"), "a redacted key"),
    (re.compile(r"\b(Bearer|Basic)\s+[-._~+/A-Za-z0-9]{12,}=*", re.I), r"\1 redacted"),
    # key=value or "key": "value" where the value looks generated: 8+ characters with a digit.
    (re.compile(r"((?:api[-_]?key|secret|token|password|passwd|auth)[\"']?\s*[:=]\s*[\"']?)(?=[^\s\"',]*\d)[^\s\"',]{8,}", re.I), r"\1redacted"),
    (re.compile(r"(--?(?:token|password|api-key|secret)[ =])\S+", re.I), r"\1redacted"),
    (re.compile(r"\beyJ[-_A-Za-z0-9]{10,}\.[-_A-Za-z0-9]{10,}\.[-_A-Za-z0-9]{10,}"), "a redacted token"),
]


def redact(text):
    for pattern, said in SECRETS:
        text = pattern.sub(said, text)
    return text


# Symbols said as words; emoji, box drawing and arrows-as-decoration are dropped.
SYMBOLS = [
    (re.compile(r"\s*(?:→|->|=>|⟶)\s*"), " to "),
    (re.compile(r"\s*(?:←|<-)\s*"), " from "),
    (re.compile(r"\s*(?:≥|>=)\s*"), " at least "),
    (re.compile(r"\s*(?:≤|<=)\s*"), " at most "),
    (re.compile(r"\s*≠\s*|\s*!==?\s*"), " not equal to "),
    (re.compile(r"\s+===?\s+"), " equals "),
    (re.compile(r"\s*\|\|\s*"), " or "),
    (re.compile(r"\s*&&\s*"), " and "),
    (re.compile(r"\s*≈\s*"), " about "),
    (re.compile(r"\s+&\s+|&amp;"), " and "),
    (re.compile(r"(?<=\d)\s*%"), " percent"),
    (re.compile(r"(?<=\d)\s*[x×]\s*(?=\d)"), " by "),
    (re.compile(r"(?<=\d)×"), " times"),
    (re.compile(r"(?<![\w])~(?=\d)"), "about "),
    (re.compile(r"(?<=\w)@(?=\w)"), " at "),
    (re.compile(r"\s+\+\s+"), " plus "),
    (re.compile(r"\s+/\s+"), " or "),
    (re.compile(r"\s+[—–]\s+|[—–]"), ", "),
    (re.compile("[\u2500-\u259F\u2800-\u28FF\u25A0-\u25FF\u2700-\u27BF\U0001F000-\U0001FAFF\u2600-\u26FF\uFE0F\u200D]"), ""),
]


def symbols(text):
    for pattern, said in SYMBOLS:
        text = pattern.sub(said, text)
    return text


MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
UNITS = {"ms": "milliseconds", "px": "pixels", "fps": "frames per second", "Hz": "hertz", "kHz": "kilohertz",
         "MHz": "megahertz", "GHz": "gigahertz", "kbps": "kilobits per second", "Mbps": "megabits per second",
         "Gbps": "gigabits per second", "KB": "kilobytes", "kB": "kilobytes", "MB": "megabytes", "GB": "gigabytes",
         "TB": "terabytes", "min": "minutes", "k": "thousand", "K": "thousand"}
UNIT = re.compile(r"\b(\d+(?:\.\d+)?)\s?(" + "|".join(sorted(UNITS, key=len, reverse=True)) + r")\b")
IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
PORT = re.compile(r"\b(localhost|\d{1,3}(?:\.\d{1,3}){3}):(\d{2,5})\b")
DATE = re.compile(r"\b(\d{4})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b")
TIME = re.compile(r"(?<![\w:.])(\d{1,2}):([0-5]\d)(?::[0-5]\d)?(?![\w:])")
MONEY = re.compile(r"\$(\d[\d,]*(?:\.\d+)?)(?:\s?([kKMB])\b)?")
SCALE = {"k": "thousand", "K": "thousand", "M": "million", "B": "billion"}
ISSUE = re.compile(r"(?<![\w&])#(\d+)\b")
VER = re.compile(r"\bv(\d+(?:\.\d+)*)\b")
PAIR = re.compile(r"\b([A-Z]{1,5})/([A-Z]{1,5})\b")  # CI/CD, I/O, UI/UX
PLURAL = re.compile(r"(?<!')\b([A-Z]{2,5})s\b")  # PRs, APIs, IDs: the s is otherwise dropped
KEYS = re.compile(r"\b(Cmd|Ctrl|Shift|Alt|Opt|Option|Command|Control|Fn|Meta|Win)\+(?=\w)")  # Cmd+K, Ctrl+Shift+P


def spell(letters):
    """An acronym letter by letter; a lone A is otherwise read as the word "a"."""
    return " ".join("eigh" if c == "A" else c for c in letters)


def clock(m):
    """3:15 as "3 15", 3:05 as "3 oh 5", 3:00 as "3 o'clock"."""
    hour, minutes = int(m.group(1)), m.group(2)
    if minutes == "00":
        return f"{hour} o'clock"
    return f"{hour} oh {minutes[1]}" if minutes[0] == "0" else f"{hour} {minutes}"


def numbers(text):
    """Dates, times, money, units, versions, issue numbers, IP addresses and ports, said as words."""
    text = PORT.sub(lambda m: f"{m.group(1)} port {m.group(2)}", text)
    text = IP.sub(lambda m: m.group(0).replace(".", " dot "), text)
    text = DATE.sub(lambda m: f"{MONTHS[int(m.group(2)) - 1]} {int(m.group(3))}, {m.group(1)}", text)
    text = TIME.sub(clock, text)
    text = MONEY.sub(lambda m: f"{m.group(1)} {SCALE[m.group(2)] + ' ' if m.group(2) else ''}dollars", text)
    text = UNIT.sub(lambda m: f"{m.group(1)} {UNITS[m.group(2)]}", text)
    text = VER.sub(r"version \1", text)
    text = ISSUE.sub(r"number \1", text)
    return text


def acronyms(text, words):
    """CI/CD as two acronyms, w/ and w/o as words, and plural acronyms with their s."""
    text = re.sub(r"\bw/o\b", "without", text)
    text = re.sub(r"\bw/(?=\s)", "with", text)
    text = PAIR.sub(r"\1 \2", text)
    text = KEYS.sub(r"\1 ", KEYS.sub(r"\1 ", text))  # twice: Ctrl+Shift+P
    return PLURAL.sub(lambda m: m.group(0) if m.group(1) in words else spell(m.group(1)) + "'s", text)


VERSION = re.compile(r"\b\d+(?:\.\d+){1,3}\b")
DOTTED = re.compile(r"\b([A-Za-z_][\w-]*)\.(?=[A-Za-z_\d])")  # server.py, careem.com


def speakable(text):
    """What is said for a piece of text: links as their site, paths as their file, no tables."""
    text = redact(text)
    text = TABLE_LINE.sub(" ", text)
    text = URL.sub(lambda m: m.group(1), text)
    text = PATH.sub(lambda m: m.group(1), text)
    if system_voice.mostly_arabic(text):
        # Said by the system's Arabic voice, which reads its own numbers: only markup goes.
        text = re.sub(r"```.*?```", " ", text, flags=re.S)
        return re.sub(r"\s{2,}", " ", re.sub(r"[*`_>|#]", "", text)).strip()
    text = FILE_REF.sub("", text)
    text = FILE_LINE.sub(r"\1 line \2", text)
    text = numbers(text)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"(?<=[A-Za-z0-9])_(?=[A-Za-z0-9])", " ", text)  # snake_case is said as two words
    text = symbols(text)
    text = re.sub(r"(?<!\w)#(?!\w)", "", text)  # a lone #, but not C# (the lexicon says it)
    text = re.sub(r"[*`_>|]", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([,.;:!?])(?=\s|$)", r"\1", text)  # "works , nice" but not ".NET"
    text = LIVE.sub(lambda m: m.group(1) + "yve", text)
    text = re.sub(r":\s*$", ".", text)  # a label ending in a colon is said as a full sentence
    words = lexicon()
    text = acronyms(text, words)
    text = pronounce(text, words).strip()
    text = VERSION.sub(lambda m: m.group(0).replace(".", " point "), text)
    text = DOTTED.sub(r"\1 dot ", text)
    # Kokoro can stutter on the last sound of a sentence that ends without punctuation.
    if text and not re.search(r"[.!?][\"')\]]*$", text):
        text += "."
    return text


# Not a sentence end: "e.g. this", "vs. that", "Dr. Smith".
NOT_END = "".join(f"(?<!\\b{re.escape(a)})" for a in ["e.g.", "i.e.", "vs.", "Dr.", "Mr.", "Mrs.", "Ms.", "St.", "cf.", "approx."])
SENTENCE_END = re.compile(r"(?<=[.!?;\u061F])" + NOT_END + r"\s+|\n+")


def chunks(text):
    sentences = [s.strip() for s in SENTENCE_END.split(text) if s.strip()]
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
    tmp = f"{STATUS}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w") as f:
        json.dump({**fields, "t": time.time()}, f)
    os.replace(tmp, STATUS)


FADE_SECONDS = 0.012  # fade on pause, resume, skip and stop, so cuts do not click
BLOCK_SECONDS = 0.05  # audio is written in blocks this long
LATENCY_SECONDS = 0.3  # device buffer: slack for the moments generation keeps Python busy
WARM_SECONDS = 30  # the stream stays open, fed silence, this long after the last sound
stream = None
out_rate = None
out_lock = threading.RLock()  # one writer at a time: a job's player or the warm keeper
last_sound = 0.0
underruns = 0


def device_rate():
    """The output device's own rate: audio is resampled to it so macOS never converts it."""
    global out_rate
    if out_rate is None:
        try:
            out_rate = int(sd.query_devices(kind="output")["default_samplerate"])
        except Exception:
            out_rate = 48000
    return out_rate


def output():
    """The one output stream, kept open while speaking and a while after, so sound never stops
    abruptly (some devices replay their last block when a stream stops or starves)."""
    global stream
    with out_lock:
        if stream is None:
            stream = sd.OutputStream(samplerate=device_rate(), channels=1, dtype="float32", latency=LATENCY_SECONDS)
        if not stream.active:
            stream.start()
            write(stream, np.zeros(int(LATENCY_SECONDS * stream.samplerate), dtype="float32"), quiet=True)  # prime
        return stream


def drop_output():
    """Forgets a stream the device broke (unplugged headphones, a new default output): the next
    sound opens a fresh one at the new device's rate."""
    global stream, out_rate
    with out_lock:
        old, stream, out_rate = stream, None, None
    with model_lock:
        cache.clear()  # made at the old device's rate
    try:
        if old is not None:
            old.close(ignore_errors=True)
    except Exception:
        pass


def write(out, data, quiet=False):
    """Blocking write; `quiet` writes are filler that neither logs an underrun nor counts as sound.
    False when the device failed, and the stream is dropped."""
    global underruns, last_sound
    with out_lock:
        if out is not stream:
            return False
        try:
            if out.write(np.ascontiguousarray(data, dtype="float32").reshape(-1, 1)) and not quiet:
                underruns += 1
                log("underrun", underruns)
        except sd.PortAudioError as err:
            log("audio device error:", err)
            drop_output()
            return False
    if not quiet:
        last_sound = time.time()
    return True


def feed_silence(seconds=BLOCK_SECONDS):
    """Silence for an open stream, so a gap never leaves the device replaying its last block."""
    with out_lock:
        if stream is not None and stream.active:
            write(stream, np.zeros(int(seconds * stream.samplerate), dtype="float32"))


def rest_output():
    """Lets the device go idle once nothing is left to say: the buffer plays out over silence."""
    with out_lock:
        if stream is not None and stream.active:
            write(stream, np.zeros(int((LATENCY_SECONDS + BLOCK_SECONDS) * stream.samplerate), dtype="float32"), quiet=True)
            stream.stop()


def keep_warm():
    """Feeds silence between replies, then rests the device once WARM_SECONDS pass quietly."""
    while True:
        try:
            warm_once()
        except Exception as err:
            log("keep warm:", err)
            drop_output()
            time.sleep(1)


def warm_once():
    with cond:
        playing = current is not None and not current.done.is_set()
    if playing or stream is None or not stream.active:
        time.sleep(BLOCK_SECONDS)
    elif time.time() - last_sound > WARM_SECONDS:
        rest_output()
    else:
        with out_lock:
            if stream is not None and stream.active:
                write(stream, np.zeros(int(BLOCK_SECONDS * stream.samplerate), dtype="float32"), quiet=True)


TARGET_RMS = 0.1  # every sentence is levelled to about this loudness (Kokoro's own average)
MAX_GAIN, MIN_GAIN = 1.4, 0.7
LIMIT = 0.92  # peaks above this are softly rounded off


def master(samples, rate):
    """Even loudness from sentence to sentence, a soft limiter, then the device's rate."""
    x = samples.astype("float32")
    voiced = x[np.abs(x) > 0.01]
    if len(voiced):
        rms = float(np.sqrt(np.mean(voiced ** 2)))
        x = x * min(MAX_GAIN, max(MIN_GAIN, TARGET_RMS / max(rms, 1e-6)))
    over = np.abs(x) > LIMIT
    if over.any():
        x[over] = np.sign(x[over]) * (LIMIT + (1 - LIMIT) * np.tanh((np.abs(x[over]) - LIMIT) / (1 - LIMIT)))
    target = device_rate()
    if rate != target:
        n = int(round(len(x) * target / rate))
        x = np.interp(np.arange(n) * (rate / target), np.arange(len(x)), x).astype("float32")
    return x


CACHE_ITEMS = 160  # sentences kept ready to play again (a few MB)
cache = OrderedDict()


def synth(said, voice, speed, arabic=""):
    """A sentence's audio at the device rate, from the cache when it was made before."""
    key = (said, voice, speed, arabic)
    with model_lock:
        hit = cache.get(key)
        if hit is not None:
            cache.move_to_end(key)
            return hit
        audio = np.concatenate([master(*made) for made in voices_for(said, voice, speed, arabic)] or [np.zeros(0, "float32")])
        cache[key] = audio
        while len(cache) > CACHE_ITEMS:
            cache.popitem(last=False)
        return audio


def voices_for(said, voice, speed, arabic):
    """(samples, rate) for each piece of a sentence: Kokoro's voice, and for Arabic Magpie's, Nabra's or the system's."""
    out = []
    # Arabic is read by MagpieTTS when it is installed and no Nabra voice is chosen, else by Nabra,
    # else by the system's voice. Nabra reads only Arabic, so English words go to Kokoro; the
    # others take a mostly Arabic sentence whole.
    use_magpie = magpie.installed() and (not arabic or arabic in magpie.VOICES)
    nabra = not use_magpie and arabic_voice.installed()
    for text, is_arabic in system_voice.pieces(said, whole=not nabra):
        if not is_arabic:
            out.append(kokoro.create(text, voice=voice, speed=speed, lang=lang_for(voice)))
            continue
        text = system_voice.letter_names(text)
        made = None
        try:
            if use_magpie:
                made = magpie.synth(arabic_voice.for_magpie(text), arabic or magpie.DEFAULT_VOICE)
            elif nabra:
                made = arabic_voice.synth(text, speed, arabic or arabic_voice.OWN_VOICE)
        except Exception as err:
            log("arabic voice error:", err)
        if made is None:
            made = system_voice.synth(text, speed)
        if made is None:
            if not warned_no_voice:
                warned_no_voice.append(True)
                log("no Arabic system voice installed: Arabic text is skipped")
        else:
            out.append(made)
    return out


warned_no_voice = []
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
        self.label = req.get("label", "")
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
        for said in reversed(req.get("prefix") or []):
            self.items.insert(0, (None, said, 0.0))
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
                audio = synth(said, voice_for(conf, self.label), float(conf["speed"]), str(conf.get("arabicVoice") or ""))
            except Exception as err:
                log("tts error:", err)
                continue
            self.ready.put((index, audio, pause))

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
            index, samples, pause = item
            self.speaking = True
            try:
                self.running.wait()
                if pause and not self.stop.is_set():
                    feed_silence(pause)  # a breath before a new paragraph or list item
                if not self.stop.is_set():
                    self.index = index
                    self.report("playing")
                    self.play_samples(samples)
            finally:
                self.speaking = False
        self.done.set()  # the warm keeper takes over the stream from here
        with cond:
            if not pending:
                write_status(session=self.session, ref=None, index=None, state="idle")
            cond.notify_all()

    def play_samples(self, samples):
        """Writes one sentence to the shared output stream in small blocks. Pausing fades to
        silence and keeps feeding silence (so the device never runs dry); resuming fades back in
        from the same sample. Skip and stop fade out, then end."""
        self.skipping = False
        try:
            out = output()
        except Exception as err:
            log("audio device error:", err)
            drop_output()
            return
        rate = out.samplerate
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
                if not write(out, np.zeros(block, dtype="float32")):  # paused: hold the position
                    return
                continue
            if pos >= len(samples):
                return
            ramp = np.clip(gain + np.sign(target - gain) * step * np.arange(1, block + 1), 0.0, 1.0)
            chunk = samples[pos:pos + block]
            if not write(out, chunk * ramp[:len(chunk)]):
                return  # the device failed: the next sentence opens it again
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
#   "urgent": true (with reply) is an alert, such as a permission prompt: it goes to the front of the
#     queue without cutting off anything, and the same alert is not repeated within ALERT_REPEAT.
#   "intro": said before the reply, after the "From <project>" label.
#   record: {"reply": {"id", "sentences": [{"text", "para", "line"}], "agent"}} adds a reply to
#     replies.json (replacing one with the same id); nothing is said.
ALERT_REPEAT = 90
cond = threading.Condition()
pending = []
current = None
seen = {}
alerted = {}
quitting = threading.Event()


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
        if mode == "ping":
            pass  # the VS Code extension keeps the server (and the session reader) running
        elif mode == "quit":
            quitting.set()  # its code was updated: the next request starts the new one
        elif mode == "record":
            if req.get("session"):
                plugin_sessions.add(req["session"])
            record(req)
        elif mode == "stop":
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
        elif req.get("urgent"):
            key = (session, " ".join(req.get("sentences") or []))
            if now - alerted.get(key, 0) > ALERT_REPEAT:
                alerted[key] = now
                active = [s for s, t in seen.items() if now - t < MULTI_WINDOW]
                label = [f"From {req['label']}."] if len(active) > 1 and req.get("label") else []
                pending.insert(0, {**req, "prefix": label})
        elif req.get("sentences") or req.get("text", "").strip():
            if not req.get("append"):
                drop(session)
            if mode == "reply":
                # A streamed reply is labelled at its first sentence only, not at each later piece.
                first = not req.get("append") or not req.get("offset")
                active = [s for s, t in seen.items() if now - t < MULTI_WINDOW]
                prefix = [f"From {req['label']}."] if first and len(active) > 1 and req.get("label") else []
                if first and req.get("intro"):
                    prefix.append(req["intro"])
                req = {**req, "prefix": prefix}
                pending.append(req)
            else:
                if current is not None:
                    current.cancel()
                pending.insert(0, req)
        cond.notify_all()


def record(req):
    """Keeps the recent replies of every session and agent in one file; this server is its only writer."""
    reply = req.get("reply")
    if not isinstance(reply, dict) or not reply.get("id") or not isinstance(reply.get("sentences"), list):
        return
    try:
        feed = json.load(open(FEED))
        if not isinstance(feed, list):
            feed = []
    except Exception:
        feed = []
    session = req.get("session", "")
    entry = {"id": str(reply["id"]), "session": session, "label": req.get("label", ""),
             "cwd": req.get("cwd", ""), "title": titles.get(session, ""),
             "agent": reply.get("agent", "claude"), "t": time.time(), "sentences": reply["sentences"]}
    feed = [r for r in feed if not (r.get("id") == entry["id"] and r.get("session") == entry["session"])]
    feed = (feed + [entry])[-FEED_KEEP:]
    tmp = f"{FEED}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w") as f:
        json.dump(feed, f)
    os.replace(tmp, FEED)


# Replies read from Claude Code's session files, for sessions the plugin is not in (it records
# its own). The plugin's record of a reply arrives within moments of the file's, so each waits.
PLUGIN_GRACE = 3.0
titles = {}  # session -> Claude Code's title for it
plugin_sessions = set()  # sessions the Claude Code plugin has recorded replies for
AWAY_APPS = {"com.microsoft.VSCode", "com.todesktop.230313mzl4w4u92", "com.exafunction.windsurf", "com.apple.Terminal",
             "com.googlecode.iterm2", "com.mitchellh.ghostty", "dev.warp.Warp-Stable", "net.kovidgoyal.kitty",
             "com.github.wez.wezterm", "io.alacritty"}


def front_app_is_editor():
    """macOS: whether an editor or terminal is in front (the person is reading already)."""
    if sys.platform != "darwin":
        return False
    import subprocess
    try:
        front = subprocess.run(["lsappinfo", "front"], capture_output=True, text=True, timeout=2).stdout.strip()
        info = subprocess.run(["lsappinfo", "info", "-only", "bundleid", front], capture_output=True, text=True,
                              timeout=2).stdout
        return any(f'"{app}"' in info for app in AWAY_APPS)
    except Exception:
        return False


def on_title(session, title):
    if titles.get(session) == title:
        return
    titles[session] = title
    with cond:
        try:
            feed = json.load(open(FEED))
        except Exception:
            return
        if not any(r.get("session") == session and r.get("title") != title for r in feed):
            return
        for r in feed:
            if r.get("session") == session:
                r["title"] = title
        tmp = f"{FEED}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w") as f:
            json.dump(feed, f)
        os.replace(tmp, FEED)


def on_transcript_reply(session, cwd, title, reply_id, text):
    threading.Timer(PLUGIN_GRACE, transcript_reply, args=(session, cwd, reply_id, text)).start()


def transcript_reply(session, cwd, reply_id, text):
    """Records, then speaks as the plugin would, a reply from a session the plugin is not in."""
    from markdown import pauses_for, to_sentences

    if session in plugin_sessions:
        return
    full = to_sentences(text)
    if not full:
        return
    label = os.path.basename(os.path.normpath(cwd)) if cwd else ""
    with cond:
        record({"session": session, "label": label, "cwd": cwd,
                "reply": {"id": reply_id, "agent": "claude",
                          "sentences": [{"text": t, "para": p, "line": l} for t, p, l in full]}})
    conf = settings()
    if conf.get("muted") or conf.get("mutedUntil", 0) > time.time() * 1000:
        return
    if conf.get("awayOnly") and front_app_is_editor():
        return
    spoken = full
    if conf.get("summarize", True) and sum(len(t) for t, _, _ in full) > 1200:
        first = full[0][1]
        spoken = [x for x in full if x[1] == first] + [("The rest is on screen.", first + 1, -1)]
    handle({"mode": "reply", "session": session, "label": label, "ref": reply_id, "offset": 0,
            "sentences": [t for t, _, _ in spoken], "pauses": pauses_for(spoken)})


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


if __name__ == "__main__":
    if sys.stderr is None:  # pythonw on Windows has no stderr: log to the file directly
        sys.stderr = open(LOG, "a", buffering=1)
    server, token = transport.listen()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # so the socket and port file are removed
    try:
        import numpy as np
        import sounddevice as sd
        from kokoro_onnx import Kokoro

        trim_log()
        log("server started")
        kokoro = Kokoro(os.path.join(HOME, "kokoro-v1.0.onnx"), os.path.join(HOME, "voices-v1.0.bin"))
        model_lock = threading.Lock()
        threading.Thread(target=worker, daemon=True).start()
        threading.Thread(target=keep_warm, daemon=True).start()
        Watcher(on_transcript_reply, on_title).start()
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
            req = parse(data)
            if token and req.pop("token", None) != token:
                continue  # TCP is open to every local user: only our own clients carry the token
            handle(req)
            last_active = time.time()
            if quitting.is_set():
                break
    finally:
        transport.close(server)
        write_status(session="", ref=None, index=None, state="idle")
