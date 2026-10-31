# Languages Kokoro has no voice for (Arabic), said by the system's own voice for that language:
# macOS `say` (Majed), Windows SAPI (an installed Arabic voice), Linux espeak-ng. Without one,
# that text is left out; Kokoro would read each Arabic letter by its English name.
import os, re, shutil, subprocess, sys, tempfile, wave

ARABIC = "؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿"
LETTER = re.compile(f"[{ARABIC}]")
# A stretch of Arabic, with the spaces, digits and punctuation between its words, and a number
# right after it ("الساعة الآن 10"), which belongs to the Arabic.
RUN = re.compile(f"[{ARABIC}]+(?:[\\s\\d،؛؟.,:!?()\"'-]+[{ARABIC}]+)*(?:\\s*\\d+(?:[.,]\\d+)*)?[؟،.!?]?")
# A letter standing alone is said by its name ("ع ح خ" is "ʕayn, ḥāʔ, khāʔ"), not as a bare sound.
LETTER_NAMES = {
    "ء": "همزة", "ا": "ألف", "أ": "ألف", "إ": "ألف", "آ": "ألف", "ب": "باء", "ت": "تاء", "ث": "ثاء", "ج": "جيم",
    "ح": "حاء", "خ": "خاء", "د": "دال", "ذ": "ذال", "ر": "راء", "ز": "زاي", "س": "سين", "ش": "شين",
    "ص": "صاد", "ض": "ضاد", "ط": "طاء", "ظ": "ظاء", "ع": "عين", "غ": "غين", "ف": "فاء", "ق": "قاف",
    "ك": "كاف", "ل": "لام", "م": "ميم", "ن": "نون", "ه": "هاء", "ي": "ياء", "ة": "تاء مربوطة", "ى": "ألف مقصورة",
}  # و alone is "and", written apart by mistake
LONE_LETTER = re.compile(f"(?<![{ARABIC}])([{''.join(LETTER_NAMES)}])(?![{ARABIC}])")


def letter_names(text):
    """Lone letters as their names, with a short pause after each in a run of them."""
    named = LONE_LETTER.sub(lambda m: LETTER_NAMES[m.group(1)] + "،", text)
    return re.sub("،(?=\\s*(?:[.,:;!?)،؛؟]|$))", "", named)
BASE_WPM = 180  # the voices' rate at speed 1
_voices = {}


def has_arabic(text):
    return bool(LETTER.search(text))


def arabic_share(text):
    """The part of a text's letters that are Arabic, from 0 to 1."""
    letters = re.findall(rf"[A-Za-z{ARABIC}]", text)
    return sum(1 for c in letters if LETTER.match(c)) / len(letters) if letters else 0.0


def mostly_arabic(text):
    letters = re.findall(rf"[A-Za-z{ARABIC}]", text)
    return bool(letters) and sum(1 for c in letters if LETTER.match(c)) * 2 >= len(letters)


def pieces(text, whole=True):
    """[(text, is_arabic)] in order: with whole, a mostly Arabic sentence stays in one piece (a
    system voice reads its English words well enough); otherwise each Arabic stretch is cut out."""
    if not has_arabic(text):
        return [(text, False)]
    if whole and mostly_arabic(text):
        return [(text, True)]
    out, pos = [], 0
    for m in RUN.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], False))
        out.append((m.group(0), True))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], False))
    return [(t, a) for t, a in out if re.search(rf"[A-Za-z0-9{ARABIC}]", t)]


def mac_voice():
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=5).stdout
    names = [m.group(1).strip() for m in re.finditer(r"^(.+?)\s+ar_\w+\s+#", out, re.M)]
    return names[0] if names else None


def voice():
    """The system's Arabic voice (a name, or True where none has to be named), or None."""
    if "ar" not in _voices:
        found = None
        try:
            if sys.platform == "darwin":
                found = mac_voice()
            elif sys.platform == "win32":
                found = windows(None, 1.0, probe=True)
            else:
                found = shutil.which("espeak-ng") or shutil.which("espeak")
        except Exception:
            found = None
        _voices["ar"] = found
    return _voices["ar"]


WINDOWS = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$v = $s.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.TwoLetterISOLanguageName -eq 'ar' } | Select-Object -First 1
if (-not $v) { exit 3 }
if ($env:VOICE_OUT -eq '') { exit 0 }
$s.SelectVoice($v.VoiceInfo.Name)
$s.Rate = [int]$env:VOICE_RATE
$s.SetOutputToWaveFile($env:VOICE_OUT)
$s.Speak([Console]::In.ReadToEnd())
$s.Dispose()
"""


def windows(text, speed, probe=False, out=""):
    env = {**os.environ, "VOICE_OUT": out, "VOICE_RATE": str(max(-10, min(10, round((speed - 1) * 10))))}
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", WINDOWS], input=text or "",
                       env=env, capture_output=True, text=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.returncode == 0 if probe else r.returncode


def read_wav(path):
    import numpy as np

    with wave.open(path) as w:
        frames = w.readframes(w.getnframes())
        width, channels, rate = w.getsampwidth(), w.getnchannels(), w.getframerate()
    kind = {1: np.uint8, 2: np.int16, 4: np.int32}[width]
    x = np.frombuffer(frames, dtype=kind).astype("float32")
    x = (x - 128) / 128 if width == 1 else x / float(2 ** (8 * width - 1))
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)
    return x, rate


def synth(text, speed):
    """(samples, rate) of Arabic text in the system's voice, or None when there is none."""
    name = voice()
    if not name:
        return None
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        wpm = str(int(BASE_WPM * speed))
        if sys.platform == "darwin":
            subprocess.run(["say", "-v", name, "-r", wpm, "-o", path, "--file-format=WAVE",
                            "--data-format=LEI16@24000", "--", text], check=True, timeout=60, capture_output=True)
        elif sys.platform == "win32":
            if windows(text, speed, out=path) != 0:
                return None
        else:
            subprocess.run([name, "-v", "ar", "-s", wpm, "-w", path, "--", text], check=True, timeout=60,
                           capture_output=True)
        return read_wav(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
