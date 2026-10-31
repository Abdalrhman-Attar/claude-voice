# Arabic in NVIDIA's MagpieTTS Multilingual 357M (NVIDIA Open Model License), run by NVIDIA's
# NeMo-Speech.cpp (Apache 2.0) as a local server that keeps the model loaded. `install_helper.py
# arabic` downloads the runtime for this platform and the model to KOKORO_HOME/magpie.
import atexit, json, os, platform, secrets, socket, subprocess, sys, threading, time, urllib.request, wave, io

HOME = os.environ.get("KOKORO_HOME", os.path.expanduser("~/.claude/kokoro"))
DIR = os.path.join(HOME, "magpie")
MODELS = os.path.join(DIR, "models")  # NEMO_SPEECH_MODEL_DIR
READY = os.path.join(DIR, "ready")  # written once the runtime and the model are both in place
MODEL = "nvidia/magpie_tts_multilingual_357m"
VOICES = ["Jason", "Sofia", "Aria", "Leo", "John"]
DEFAULT_VOICE = "Jason"
LANGUAGE = "ar-MSA"
IDLE_STOP = 15 * 60  # the runtime holds the model in memory; it is stopped after this long unused

RELEASE = "https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v0.3.0"
# Platform -> (release file, its SHA-256 from the release's SHA256SUMS). CPU builds where a GPU
# build needs drivers to be present (CUDA, Vulkan); Apple Silicon uses Metal.
BUILDS = {
    ("darwin", "arm64"): ("nemo-speech-0.3.0-macos-aarch64-metal.tar.gz",
                          "2b972ed8b5dba07492212acf8cb75cbf34ecb141198ffa1997febe84b7c4d9ed"),
    ("darwin", "x86_64"): ("nemo-speech-0.3.0-macos-x86_64-cpu.tar.gz",
                           "97e98aa5eb82dffa4f513f26336b5301d792c9fb43f720ac8f9520a0e92ffe3f"),
    ("linux", "x86_64"): ("nemo-speech-0.3.0-linux-x86_64-cpu.tar.gz",
                          "255f12b722ab15b2a38a51cf125d56e3fa97058b528f7e895b39d6a473deb06e"),
    ("linux", "aarch64"): ("nemo-speech-0.3.0-linux-aarch64-cpu.tar.gz",
                           "56858eaf50d19b05902c043f65d0057352bdc107ae380c41053bfbddfc204bf1"),
    ("win32", "amd64"): ("nemo-speech-0.3.0-windows-x86_64-cpu.zip",
                         "4ed9740e361e4653f56c3dce7d1e81883a1c8b831c1bd7d01567fff1130b5b05"),
}


def build():
    """(release file, SHA-256) for this machine, or None where NVIDIA has no CPU or Metal build."""
    machine = platform.machine().lower()
    return BUILDS.get((sys.platform, {"aarch64": "arm64" if sys.platform == "darwin" else "aarch64",
                                      "arm64": "arm64" if sys.platform == "darwin" else "aarch64"}.get(machine, machine)))


def binary():
    """The nemo-speech program, wherever the release archive put its bin folder."""
    name = "nemo-speech.exe" if sys.platform == "win32" else "nemo-speech"
    for root, _, files in os.walk(os.path.join(DIR, "runtime")):
        if name in files and os.path.basename(root) == "bin":
            return os.path.join(root, name)
    return None


def installed():
    return os.path.exists(READY) and binary() is not None


class Runtime:
    """The nemo-speech server: started on first use, on a free localhost port with a random key."""

    def __init__(self):
        self.proc = None
        self.port = 0
        self.key = ""
        self.used = 0.0
        self.lock = threading.Lock()
        threading.Thread(target=self.reaper, daemon=True).start()
        atexit.register(self.stop)

    def env(self):
        return {**os.environ, "NEMO_SPEECH_MODEL_DIR": MODELS}

    def start(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.key = secrets.token_hex(16)
        log = open(os.path.join(HOME, "magpie.log"), "a")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(
            [binary(), "serve", "--tts-model", MODEL, "--host", "127.0.0.1", "--port", str(self.port), "--no-ui",
             "--api-key", self.key, "--quiet"],
            env=self.env(), stdout=log, stderr=log, stdin=subprocess.DEVNULL, creationflags=flags)
        deadline = time.time() + 90  # loading the model takes a few seconds, longer on a CPU build
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"nemo-speech exited ({self.proc.returncode}); see magpie.log")
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2).read()
                return
            except OSError:
                time.sleep(0.3)
        self.stop()
        raise RuntimeError("nemo-speech did not start in time")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def reaper(self):
        while True:
            time.sleep(30)
            with self.lock:
                if self.proc and time.time() - self.used > IDLE_STOP:
                    self.stop()

    def speak(self, text, voice):
        """(samples as float32, rate) for the text."""
        import numpy as np

        with self.lock:
            self.used = time.time()
            if self.proc is None or self.proc.poll() is not None:
                self.start()
            body = json.dumps({"input": text, "voice": voice, "language": LANGUAGE, "response_format": "wav"}).encode()
            req = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/audio/speech", body,
                                         {"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
            data = urllib.request.urlopen(req, timeout=120).read()
            self.used = time.time()
        with wave.open(io.BytesIO(data)) as w:
            rate = w.getframerate()
            pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        return pcm.astype("float32") / 32768.0, rate


_runtime = None


def synth(text, voice=DEFAULT_VOICE):
    """(samples, rate) of Arabic text in a Magpie voice, or None when Magpie is not installed."""
    global _runtime
    if not installed():
        return None
    if _runtime is None:
        _runtime = Runtime()
    return _runtime.speak(text, voice if voice in VOICES else DEFAULT_VOICE)
