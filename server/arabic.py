# Arabic in a neural voice: Nabra-82M (a Kokoro fine-tune for Modern Standard Arabic, by oddadmix,
# Apache 2.0), after Shakkelha (MIT; shakkelha.py) restores the short vowels that written Arabic
# leaves out. `install_helper.py arabic` downloads both to KOKORO_HOME/arabic. Installs made
# before Shakkelha keep using CATT (Abjad AI, Apache 2.0; server/catt).
import os, re, threading

HOME = os.environ.get("KOKORO_HOME", os.path.expanduser("~/.claude/kokoro"))
DIR = os.path.join(HOME, "arabic")
FILES = {  # file here -> where it comes from
    "nabra.onnx": "https://huggingface.co/marwanelamami/nabra-82m-sherpa-onnx/resolve/7fe12ea85baeb7a38c58d7c5370c87c95b16adb9/model.fp16.onnx",
    "voices.bin": "https://huggingface.co/marwanelamami/nabra-82m-sherpa-onnx/resolve/7fe12ea85baeb7a38c58d7c5370c87c95b16adb9/voices.bin",
    "tokens.txt": "https://huggingface.co/marwanelamami/nabra-82m-sherpa-onnx/resolve/7fe12ea85baeb7a38c58d7c5370c87c95b16adb9/tokens.txt",
    # Shakkelha's weights, read out of its Keras model (see shakkelha.py)
    "shakkelha.npz": "https://github.com/Abdalrhman-Attar/claude-voice/releases/download/models-shakkelha-1/shakkelha-rnn3-big-avg20.npz",
}
SHAKKELHA_SHA256 = "e970e7d663483c1599ea9ff3218c82ace8fd80e68530feff89dcceefc9b93b8f"
# Older installs: CATT's encoder-decoder or encoder-only models, used when Shakkelha is missing.
DIACRITIZERS = (("ed-encoder.onnx", "ed-decoder.onnx"), ("encoder.onnx", "decoder.onnx"))
OWN_VOICE = "af_msa"  # Nabra's own voice; any Kokoro voice name also works, as Nabra is a Kokoro fine-tune
RATE = 24000
MAX_TOKENS = 508  # the voice pack has a style row for up to 510, with the two ends
# Arabic letters only: CATT drops anything else, so punctuation and digits stay outside its runs.
WORDS = re.compile(r"[ء-غف-يٱً-ْٰ]+(?:\s+[ء-غف-يٱً-ْٰ]+)*")
LETTER = "\u0621-\u063A\u0641-\u064A"
MARKS = "\u064B-\u0652\u0670"
END = "(?=\\s|[.,;:!?\u060C\u061B\u061F]|$)"
# A connecting alif (hamzat al-wasl): a bare ا before the article or a vowelless letter, after a
# vowelled prefix (بِ، وَ) or a long vowel. It is silent there, but espeak says it ("bi-aa-llugha");
# written as ٱ it is skipped ("billugha").
WASL_NEXT = f"(?=[{LETTER}]\u0652|\u0644[{LETTER}][{MARKS}]*\u0651|\u0644\u0652)"
WASL_AFTER_VOWEL = re.compile("(?<=[\u064E\u064F\u0650])\u0627" + WASL_NEXT)
# After a word, only a long vowel carries over ("fī l-khādim"); after a consonant, which a word
# ends in once its case ending is dropped, the alif is said ("ṣawtuk al-jadīd").
WASL_AFTER_WORD = re.compile("(?:(?<=[\u0627\u0649\u064A\u0648] )|(?<=[\u0627\u0649\u064A\u0648]\u0652 ))\u0627" + WASL_NEXT)
# A word's last vowel is its grammatical case ending, the mark the diacritizer gets wrong most
# often; it is dropped (as most people read aloud), along with the -n of tanween, for a sukun.
CASE_ENDING = re.compile(f"[\u064B-\u0650\u0652]+{END}")
TAA_AT_END = re.compile(f"\u0629\u0652?{END}")
# Words whose last vowel is part of the word, not a case ending: "thumma", not "thumm".
FIXED_ENDINGS = {"أن", "إن", "ليست"}  # kept as the diacritizer wrote them
FIXED_WORDS = {  # and these spelled in full, as the diacritizer sometimes leaves their last vowel off
    "ثم": "ثُمَّ", "هو": "هُوَ", "هي": "هِيَ", "لكن": "لَكِنَّ", "كأن": "كَأَنَّ", "ليت": "لَيْتَ", "لعل": "لَعَلَّ",
    "حيث": "حَيْثُ", "منذ": "مُنْذُ", "أين": "أَيْنَ", "كيف": "كَيْفَ", "ليس": "لَيْسَ",
}
SAID_WASL = re.compile(f"(?<![{LETTER}{MARKS}\u0671])\u0627(?=[\u0621-\u0643\u0645-\u064A]\u0652)")
BARE_HAMZA_BELOW = re.compile(f"\u0625(?![{MARKS}])")  # إ is always "ʔi"; espeak drops it without its kasra
PUNCTUATION = str.maketrans({"\u060C": ",", "\u061B": ";", "\u061F": "?"})
_lock = threading.Lock()
_model = None


def has_shakkelha():
    return os.path.exists(os.path.join(DIR, "shakkelha.npz"))


def diacritizer():
    """(encoder, decoder, autoregressive) paths of the best CATT model here, or None."""
    for k, pair in enumerate(DIACRITIZERS):
        paths = [os.path.join(DIR, f) for f in pair]
        if all(os.path.exists(p) for p in paths):
            return (*paths, k == 0)
    return None


def installed():
    return (has_shakkelha() or diacritizer() is not None) and all(os.path.exists(os.path.join(DIR, f)) for f in ("nabra.onnx", "voices.bin", "tokens.txt"))


class Model:
    def __init__(self):
        import numpy as np
        import onnxruntime as ort
        from catt.tokenizer import TashkeelTokenizer

        self.np = np
        self.tts = ort.InferenceSession(os.path.join(DIR, "nabra.onnx"), providers=["CPUExecutionProvider"])
        self.shakkelha = None
        if has_shakkelha():
            from shakkelha import Shakkelha

            self.shakkelha = Shakkelha(os.path.join(DIR, "shakkelha.npz"))
        else:
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
        # espeak's Arabic numbers borrow the English r and double a word's first letter: "ˈʕʕˈaʃaɹa" (10).
        p = p.replace("ɹ", "r").replace("ʕʕ", "ʕ").replace("-", "")
        p = re.sub(r"(^|\s)([ˈˌ]?)([^\Wːˈˌ])\3", r"\1\2\3", p)
        p = re.sub(r"([^\Wːˈˌ])\1\1+", r"\1\1", p)  # "ʔannnusχa": a doubled letter is two, never three
        return p.strip()

    def say(self, text, speed, voice):
        np = self.np
        if self.shakkelha:  # the whole sentence, punctuation and all, for context
            vowelled = reading(self.shakkelha(text))
        else:
            vowelled = reading(WORDS.sub(lambda m: self.vowels(m.group(0)), text))
        ids = [self.vocab[c] for c in self.phonemes(vowelled) if c in self.vocab][:MAX_TOKENS]
        if not ids:
            return None
        audio = self.tts.run(None, {"input_ids": np.asarray([[0, *ids, 0]], dtype=np.int64),
                                    "ref_s": self.voices.get(voice, self.voices[OWN_VOICE])[len(ids)].reshape(1, 256),
                                    "speed": np.asarray([speed], dtype=np.float32)})[0]
        return audio.squeeze().astype("float32"), RATE


def shadda_first(text):
    """espeak reads a shadda only when it comes before the vowel ("thumma", not "thumm")."""
    return re.sub("([\u064B-\u0650])\u0651", "\u0651\\1", text)


def ending(word):
    plain = strip_marks(word)
    if plain in FIXED_WORDS:
        return shadda_first(FIXED_WORDS[plain])
    return word if plain in FIXED_ENDINGS else CASE_ENDING.sub("\u0652", word)


def strip_marks(word):
    return re.sub(f"[{MARKS}]", "", word).replace("\u0671", "\u0627")


def reading(vowelled):
    """Vowelled text as it is read aloud: connecting alifs silent, words without case endings."""
    text = shadda_first(vowelled)
    # Shakkelha writes the connecting alif inside a word with its kasra ("الاِخْتِبَار"): the kasra
    # belongs to the letter before ("al-ikhtibār"), and the alif is silent.
    text = re.sub(f"(?<=[{LETTER}])\u0627\u0650", "\u0650\u0671", text)
    text = re.sub(f"(?<![{LETTER}{MARKS}])([\u0627\u0671]\u0644)(?=[\u0621-\u0625])", "\\1\u0652", text)  # "al-ʔaṣdiqāʔ"
    text = WASL_AFTER_WORD.sub("\u0671", WASL_AFTER_VOWEL.sub("\u0671", text))
    text = BARE_HAMZA_BELOW.sub("\u0625\u0650", text)
    # A connecting alif that is said (at the start, or after a consonant) is "ʔi" before a vowelless
    # letter other than the article's lam: "iftaḥ", "istakhdim"; espeak would say "aftaḥ".
    text = SAID_WASL.sub("\u0625\u0650", text)
    # A sukun, else espeak adds a vowel of its own ("ṣawtuka"); particles keep their fixed vowel.
    text = re.sub(f"[{LETTER}{MARKS}\u0671]+", lambda m: ending(m.group(0)), text)
    return TAA_AT_END.sub("\u0647\u0652", text)  # ـة without its ending is said "-ah"


def synth(text, speed, voice=OWN_VOICE):
    """(samples, rate) of Arabic text read by Nabra in the given voice, or None when it is not installed."""
    global _model
    if not installed():
        return None
    with _lock:
        if _model is None:
            _model = Model()
        return _model.say(text, speed, voice)
