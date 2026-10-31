# Shakkelha, an Arabic diacritizer (Fadel et al., EMNLP 2019; https://github.com/AliOsm/shakkelha,
# MIT License): its best model, the normalized RNN trained on the big dataset with its last 20
# epochs averaged (1.69% diacritic error rate on its test set), run here with NumPy alone. The
# weights were read out of that model's Keras file into shakkelha.npz; the tables below are its
# RNN_BIG_CHARACTERS_MAPPING and RNN_REV_CLASSES_MAPPING.
import numpy as np

# Character ids: 0 <PAD>, 1 <SOS>, 2 <EOS>, 3 <UNK>, then these from 4 on.
CHARACTERS = ('\n !"&\'()*+,-./0123456789:;=[]_`{}~«»،؛؟ءآأؤإئابةتثجحخدذرزسشصضطظعغفقكلمنهوىي٠١٢٤'
              '‍‏–’“…﴾﴿')
SOS, EOS, UNK = 1, 2, 3
CHAR_IDS = {c: i + 4 for i, c in enumerate(CHARACTERS)}
# The mark each class adds after a letter; "" for none and for the special classes 15 to 18.
CLASSES = ["", "َ", "ً", "ُ", "ٌ", "ِ", "ٍ", "ْ", "ّ",
           "َّ", "ًّ", "ُّ", "ٌّ", "ِّ", "ٍّ", "", "", "", ""]
LETTERS = set("ءآأؤإئابةتثجحخدذرزسشصضطظعغفقكلمنهوىي")
MARKS = str.maketrans("", "", "ًٌٍَُِّْ")


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


class Shakkelha:
    def __init__(self, path):
        with np.load(path, allow_pickle=False) as w:
            self.w = {k: w[k].astype(np.float32) for k in w.files}

    def lstm(self, x, name):
        """One direction of a Keras LSTM layer (gates i, f, c, o), every step's output."""
        k, r, b = self.w[name + "_k"], self.w[name + "_r"], self.w[name + "_b"]
        units = r.shape[0]
        h = np.zeros(units, np.float32)
        c = np.zeros(units, np.float32)
        inputs = x @ k + b
        out = np.empty((len(x), units), np.float32)
        for t in range(len(x)):
            z = inputs[t] + h @ r
            i, f = sigmoid(z[:units]), sigmoid(z[units:2 * units])
            c = f * c + i * np.tanh(z[2 * units:3 * units])
            h = sigmoid(z[3 * units:]) * np.tanh(c)
            out[t] = h
        return out

    def both_ways(self, x, layer):
        forward = self.lstm(x, f"{layer}_forward")
        backward = self.lstm(x[::-1], f"{layer}_backward")[::-1]
        return np.concatenate([forward, backward], axis=1)

    def __call__(self, text):
        """Text with its short vowels, shadda and sukun restored (any marks it had are redone)."""
        w = self.w
        plain = text.translate(MARKS)
        ids = [SOS] + [CHAR_IDS.get(ch, UNK) for ch in plain] + [EOS]
        x = self.both_ways(self.both_ways(w["embeddings"][ids], "lstm1"), "lstm2")
        x = np.maximum(0, x @ w["dense1_k"] + w["dense1_b"])
        x = np.maximum(0, x @ w["dense2_k"] + w["dense2_b"])
        best = np.argmax(x @ w["out_k"] + w["out_b"], axis=1)[1:]
        return "".join(ch + (CLASSES[int(k)] if ch in LETTERS else "") for ch, k in zip(plain, best))
