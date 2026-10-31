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
    # CATT's encoder-decoder model, the more accurate of its two: ed-encoder.onnx, ed-decoder.onnx
    "catt.zip": "https://github.com/abjadai/catt/releases/download/v2/ed_model_onnx.zip",
}
DIACRITIZERS = (("ed-encoder.onnx", "ed-decoder.onnx"), ("encoder.onnx", "decoder.onnx"))  # best first
OWN_VOICE = "af_msa"  # Nabra's own voice; any Kokoro voice name also works, as Nabra is a Kokoro fine-tune
RATE = 24000
MAX_TOKENS = 508  # the voice pack has a style row for up to 510, with the two ends
# Arabic letters only: CATT drops anything else, so punctuation and digits stay outside its runs.
WORDS = re.compile(r"[ء-غف-يٱً-ْٰ]+(?:\s+[ء-غف-يٱً-ْٰ]+)*")
PUNCTUATION = str.maketrans({"\u060C": ",", "\u061B": ";", "\u061F": "?"})
_lock = threading.Lock()
_model = None


def diacritizer():
    """(encoder, decoder, autoregressive) paths of the best CATT model here, or None."""
    for k, pair in enumerate(DIACRITIZERS):
        paths = [os.path.join(DIR, f) for f in pair]
        if all(os.path.exists(p) for p in paths):
            return (*paths, k == 0)
    return None


def installed():
    return diacritizer() is not None and all(os.path.exists(os.path.join(DIR, f)) for f in ("nabra.onnx", "voices.bin", "tokens.txt"))


class Model:
    def __init__(self):
        import numpy as np
        import onnxruntime as ort
        from catt.tokenizer import TashkeelTokenizer

        self.np = np
        self.tts = ort.InferenceSession(os.path.join(DIR, "nabra.onnx"), providers=["CPUExecutionProvider"])
        encoder, decoder, self.autoregressive = diacritizer()
        self.encoder = ort.InferenceSession(encoder, providers=["CPUExecutionProvider"])
        self.decoder = ort.InferenceSession(decoder, providers=["CPUExecutionProvider"])
        self.voices = {OWN_VOICE: np.fromfile(os.path.join(DIR, "voices.bin"), dtype=np.float32).reshape(-1, 256)}
        kokoro_voices = os.path.join(HOME, "voices-v1.0.bin")
        if os.path.exists(kokoro_voices):
            self.voices.update({k: v.reshape(-1, 256) for k, v in np.load(kokoro_voices).items()})
        self.vocab = {}
        for line in open(os.path.join(DIR, "tokens.txt"), encoding="utf-8"):
            sym, _, idx = line.rstrip("\n").rpartition(" ")
            if sym:
                self.vocab[sym] = int(idx)
        self.tok = TashkeelTokenizer()
        from kokoro_onnx.tokenizer import Tokenizer

        Tokenizer()  # points the phonemizer at the espeak-ng that kokoro-onnx ships

    def tashkeel(self, words):
        """Arabic words with their short vowels restored by CATT."""
        np, tok = self.np, self.tok
        ids, _ = tok.encode(words, test_match=False)
        src = np.asarray([ids if self.autoregressive else ids[1:-1]], dtype=np.int64)  # encoder only: no BOS, EOS
        n = src.shape[1]
        keep = src != tok.letters_map["<PAD>"]
        mask = np.repeat(keep.reshape(1, 1, 1, n), n, axis=2) & keep.reshape(1, 1, n, 1)
        enc = self.encoder.run(None, {"src": src, "src_mask": mask})[0]
        space = src == tok.letters_map[" "]
        none = tok.tashkeel_map[tok.no_tashkeel_tag]
        if not self.autoregressive:
            pred = np.argmax(self.decoder.run(None, {"enc_src": enc})[0], axis=-1)
            pred[space] = none
            return tok.decode(src, pred)[0]
        # Encoder-decoder: one mark per letter, each chosen knowing the marks before it.
        trg = np.asarray([[tok.tashkeel_map["<BOS>"]]], dtype=np.int64)
        for _ in range(n - 1):
            t = trg.shape[1]
            out = self.decoder.run(None, {"trg": trg, "enc_src": enc,
                                          "trg_mask": np.tril(np.ones((t, t), bool)).reshape(1, 1, t, t),
                                          "src_trg_mask": np.repeat(keep.reshape(1, 1, 1, n), t, axis=2)})[0]
            trg = np.concatenate([trg, out[:, -1:].argmax(-1)], axis=1)
            trg[space[:, :trg.shape[1]]] = none
        return tok.decode(src, trg)[0]

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

    def say(self, text, speed, voice):
        np = self.np
        vowelled = WORDS.sub(lambda m: self.vowels(m.group(0)), text)
        ids = [self.vocab[c] for c in self.phonemes(vowelled) if c in self.vocab][:MAX_TOKENS]
        if not ids:
            return None
        audio = self.tts.run(None, {"input_ids": np.asarray([[0, *ids, 0]], dtype=np.int64),
                                    "ref_s": self.voices.get(voice, self.voices[OWN_VOICE])[len(ids)].reshape(1, 256),
                                    "speed": np.asarray([speed], dtype=np.float32)})[0]
        return audio.squeeze().astype("float32"), RATE


def synth(text, speed, voice=OWN_VOICE):
    """(samples, rate) of Arabic text read by Nabra in the given voice, or None when it is not installed."""
    global _model
    if not installed():
        return None
    with _lock:
        if _model is None:
            _model = Model()
        return _model.say(text, speed, voice)
