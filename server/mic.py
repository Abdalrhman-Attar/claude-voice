# Whether something is recording from the microphone, so speech can pause while you talk: by default
# only voice typing (Claude Code's voice input, the system's dictation, dictation apps); with "any",
# every program, calls included. Browsers often keep the microphone open, so "any" is not the default.
#   macOS 14+: CoreAudio lists the processes running audio input (our own process excluded); older
#              macOS: whether the default input device is running at all.
#   Windows:   an app holding the microphone has a capability record with no stop time yet.
#   Linux:     an ALSA capture stream is running (PipeWire and PulseAudio open one while recording).
import glob, os, re, subprocess, sys

# Programs that record only to turn speech into typing (matched against the program's name or path).
DICTATION = re.compile(r"(^|[/\\])(claude|node|bun|sox|rec|corespeechd|DictationIM|localspeechrecognition|"
                       r"SpeechRecognitionCore|Wispr Flow|superwhisper|MacWhisper|VoiceInk|Aqua Voice|Whispering|"
                       r"VoiceTypingTool|TextInputHost|SpeechRuntime)(\.exe)?$", re.I)


def fourcc(code):
    return int.from_bytes(code.encode(), "big")


class MacMic:
    def __init__(self):
        import ctypes

        self.c = ctypes
        self.ca = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")

        class Address(ctypes.Structure):
            _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("element", ctypes.c_uint32)]

        self.Address = Address
        self.ca.AudioObjectGetPropertyDataSize.argtypes = [ctypes.c_uint32, ctypes.POINTER(Address), ctypes.c_uint32,
                                                           ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        self.ca.AudioObjectGetPropertyData.argtypes = [ctypes.c_uint32, ctypes.POINTER(Address), ctypes.c_uint32,
                                                       ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        self.me = os.getpid()
        self.names = {}  # pid -> program path

    def get(self, obj, selector, kind, count=1):
        c = self.c
        addr = self.Address(fourcc(selector), fourcc("glob"), 0)
        size = c.c_uint32(c.sizeof(kind) * count)
        out = (kind * count)()
        if self.ca.AudioObjectGetPropertyData(obj, c.byref(addr), 0, None, c.byref(size), out) != 0:
            return None
        return list(out)[: size.value // c.sizeof(kind)]

    def processes(self):
        c = self.c
        addr = self.Address(fourcc("prs#"), fourcc("glob"), 0)
        size = c.c_uint32(0)
        if self.ca.AudioObjectGetPropertyDataSize(1, c.byref(addr), 0, None, c.byref(size)) != 0:
            return None
        return self.get(1, "prs#", c.c_uint32, size.value // 4) or []

    def name(self, pid):
        if pid not in self.names:
            out = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
            self.names[pid] = out
        return self.names[pid]

    def active(self, which):
        c = self.c
        procs = self.processes()
        if procs is not None:  # macOS 14+: per process
            for p in procs:
                running = self.get(p, "piri", c.c_uint32)  # kAudioProcessPropertyIsRunningInput
                if not (running and running[0]):
                    continue
                pid = self.get(p, "ppid", c.c_int32)
                pid = pid[0] if pid else 0
                if pid == self.me:
                    continue
                if which == "any" or DICTATION.search(self.name(pid)):
                    return True
            return False
        if which != "any":
            return False  # older macOS cannot say which program records
        device = self.get(1, "dIn ", c.c_uint32)  # the default input device
        running = device and self.get(device[0], "gone", c.c_uint32)  # running somewhere
        return bool(running and running[0])


def windows_active(which):
    import winreg

    def walk(key):
        i = 0
        while True:
            try:
                name = winreg.EnumKey(key, i)
            except OSError:
                return
            i += 1
            with winreg.OpenKey(key, name) as sub:
                try:
                    if winreg.QueryValueEx(sub, "LastUsedTimeStop")[0] == 0:
                        yield name.replace("#", "\\")
                except OSError:
                    pass
                if name == "NonPackaged":
                    yield from walk(sub)

    base = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base) as key:
            return any(which == "any" or DICTATION.search(app) for app in walk(key))
    except OSError:
        return False


def linux_active(which):
    if which != "any":
        return False  # ALSA shows the sound server, not which program records
    for status in glob.glob("/proc/asound/card*/pcm*c/sub*/status"):
        try:
            if "RUNNING" in open(status).read():
                return True
        except OSError:
            pass
    return False


_mac = None


def active(which="dictation"):
    """True while voice typing ("dictation") or any program ("any") records from the microphone;
    False when unsure."""
    global _mac
    try:
        if sys.platform == "darwin":
            if _mac is None:
                _mac = MacMic()
            return _mac.active(which)
        if sys.platform == "win32":
            return windows_active(which)
        return linux_active(which)
    except Exception:
        return False
