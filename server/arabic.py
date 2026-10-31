# Arabic in a neural voice: Nabra-82M (a Kokoro fine-tune for Modern Standard Arabic, by oddadmix,
# Apache 2.0), after CATT (Abjad AI, Apache 2.0) restores the short vowels that written Arabic
# leaves out. Both are ONNX models that `install_helper.py arabic` downloads to KOKORO_HOME/arabic.
import os, re, threading

HOME = os.environ.get("KOKORO_HOME", os.path.expanduser("~/.claude/kokoro"))
DIR = os.path.join(HOME, "arabic")
FILES = {  # file here -> where it comes from
    "nabra.onnx": "https://huggingface.co/marwanelamami/nabra-82m-sherpa-onnx/resolve/7fe12ea85baeb7a38c58d7c5370c87c95b16adb9/model.fp16.onnx",
    "voices.bin": "https://huggingface.co/marwanelamami/nabra-82m-sherpa-onnx/resolve/7fe12ea85baeb7a38c58d7c5370c87c95b16adb9/voices.bin",
    "tokens.txt": "https://huggingface.co/marwanelamami/nabra-82m-sherpa-onnx/resolve/7fe12ea85baeb7a38c58d7c5370c87c95b16adb9/tokens.txt",
    "catt.zip": "https://github.com/abjadai/catt/releases/download/v2/eo_model_onnx.zip",  # encoder.onnx, decoder.onnx
}
RATE = 24000
MAX_TOKENS = 508  # the voice pack has a style row for up to 510, with the two ends
# Arabic letters only: CATT drops anything else, so punctuation and digits stay outside its runs.
WORDS = re.compile(r"[ء-غف-يٱً-ْٰ]+(?:\s+[ء-غف-يٱً-ْٰ]+)*")
PUNCTUATION = str.maketrans({"\u060C": ",", "\u061B": ";", "\u061F": "?"})
_lock = threading.Lock()
_model = None


def installed():
    return all(os.path.exists(os.path.join(DIR, f)) for f in ("nabra.onnx", "voices.bin", "tokens.txt", "encoder.onnx", "decoder.onnx"))


class Model:
    def __init__(self):
        import numpy as np
        import onnxruntime as ort
        from catt.tokenizer import TashkeelTokenizer

        self.np = np
        self.tts = ort.InferenceSession(os.path.join(DIR, "nabra.onnx"), providers=["CPUExecutionProvider"])
        self.encoder = ort.InferenceSession(os.path.join(DIR, "encoder.onnx"), providers=["CPUExecutionProvider"])
        self.decoder = ort.InferenceSession(os.path.join(DIR, "decoder.onnx"), providers=["CPUExecutionProvider"])
        self.voices = np.fromfile(os.path.join(DIR, "voices.bin"), dtype=np.float32).reshape(-1, 256)
        self.vocab = {}
        for line in open(os.path.join(DIR, "tokens.txt"), encoding="utf-8"):
            sym, _, idx = line.rstrip("\n").rpartition(" ")
            if sym:
                self.vocab[sym] = int(idx)
        self.tok = TashkeelTokenizer()
        from kokoro_onnx.tokenizer import Tokenizer

        Tokenizer()  # points the phonemizer at the espeak-ng that kokoro-onnx ships

    def tashkeel(self, words):
        """Arabic words with their short vowels restored (CATT, encoder only)."""
        np, tok = self.np, self.tok
        ids, _ = tok.encode(words, test_match=False)
        src = np.asarray([ids[1:-1]], dtype=np.int64)  # without BOS and EOS
        pad = tok.letters_map["<PAD>"]
        keep = src != pad
        mask = np.repeat(keep.reshape(1, 1, 1, -1), src.shape[1], axis=2) & keep.reshape(1, 1, -1, 1)
        enc = self.encoder.run(None, {"src": src, "src_mask": mask})[0]
        pred = np.argmax(self.decoder.run(None, {"enc_src": enc})[0], axis=-1)
        pred[src == tok.letters_map[" "]] = tok.tashkeel_map[tok.no_tashkeel_tag]
        return tok.decode(src, pred)[0]

    def vowels(self, words):
        try:
            return self.tashkeel(re.sub(r"\s+", " ", words))
        except Exception:
            return words  # a letter CATT does not know: espeak guesses the vowels instead

    def phonemes(self, text):
        import phonemizer
        from kokoro_onnx.tokenizer import _espeak_lock

        text = text.translate(PUNCTUATION)  # espeak keeps only the Latin forms as pauses
        with _espeak_lock:
            p = phonemizer.phonemize(text, "ar", preserve_punctuation=True, with_stress=True)
        p = re.sub(r"(?<=\S)\.(?=\S)", "", p)  # espeak's syllable dots would be heard as pauses
        for mark in "̪ˤ[]{}":
            p = p.replace(mark, "")
        return p.strip()

    def say(self, text, speed):
        np = self.np
        vowelled = WORDS.sub(lambda m: self.vowels(m.group(0)), text)
        ids = [self.vocab[c] for c in self.phonemes(vowelled) if c in self.vocab][:MAX_TOKENS]
        if not ids:
            return None
        audio = self.tts.run(None, {"input_ids": np.asarray([[0, *ids, 0]], dtype=np.int64),
                                    "ref_s": self.voices[len(ids)].reshape(1, 256),
                                    "speed": np.asarray([speed], dtype=np.float32)})[0]
        return audio.squeeze().astype("float32"), RATE


def synth(text, speed):
    """(samples, rate) of Arabic text in Nabra's voice, or None when it is not installed."""
    global _model
    if not installed():
        return None
    with _lock:
        if _model is None:
            _model = Model()
        return _model.say(text, speed)
