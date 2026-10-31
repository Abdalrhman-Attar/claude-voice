# Checks how Arabic is cut out of a sentence and handed to the Arabic voice: python3 tests/arabic_test.py
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import arabic, markdown, system_voice  # noqa: E402

pieces = system_voice.pieces
assert pieces("Plain English.") == [("Plain English.", False)]
assert pieces("مرحبا، كيف حالك اليوم؟") == [("مرحبا، كيف حالك اليوم؟", True)]
# A system voice reads a mostly Arabic sentence whole; Nabra reads only the Arabic.
mixed = "اضغط على زر Save ثم أعد التشغيل."
assert pieces(mixed) == [(mixed, True)]
assert pieces(mixed, whole=False) == [("اضغط على زر", True), (" Save ", False), ("ثم أعد التشغيل.", True)]
assert pieces("The button says حفظ التغييرات in the app.") == [
    ("The button says ", False), ("حفظ التغييرات", True), (" in the app.", False)]
# The diacritizer gets letters only; punctuation and digits stay out of its runs.
assert arabic.WORDS.findall("عندي 3 ملفات، جديدة؟") == ["عندي", "ملفات", "جديدة"]
assert "،؛؟".translate(arabic.PUNCTUATION) == ",;?"
assert [s for s, _, _ in markdown.to_sentences("مرحبا بك. هل تريد المتابعة؟ نعم")] == [
    "مرحبا بك.", "هل تريد المتابعة؟", "نعم"]
# Read aloud: case endings become a sukun, connecting alifs go silent after a vowel, particles stay.
import re  # noqa: E402

def read(text):
    return arabic.reading(text)


def shadda_first(text):  # how reading() orders a shadda and its vowel
    return re.sub("([\u064B-\u0650])\u0651", "\u0651\\1", text)


assert read("بِاللُّغَةِ الْعَرَبِيَّةِ.") == shadda_first("بِٱللُّغَهْ الْعَرَبِيَّهْ.")
assert read("فِي الْخَادِمِ") == shadda_first("فِي ٱلْخَادِمْ")
assert read("صَوْتُكَ الْجَدِيدُ") == "صَوْتُكْ الْجَدِيدْ"  # after a consonant the article is said
assert read("ثُمَّ أَعِدْ") == shadda_first("ثُمَّ أَعِدْ")
assert read("إلَى") == shadda_first("إِلَى")
assert read("\u062b\u064f\u0645\u064e\u0651") == "\u062b\u064f\u0645\u0651\u064e"  # Shakkelha's vowel-then-shadda is turned around for espeak
assert read("الاِخْتِبَارَاتِ") == shadda_first("الِٱخْتِبَارَاتْ")
assert read("مَعَ الأَصْدِقَاءِ") == shadda_first("مَعْ الْأَصْدِقَاءْ")

# Lone letters by name; a number right after Arabic stays with it; a spoken connecting alif is "ʔi".
assert system_voice.letter_names("ع ح خ") == "عين، حاء، خاء"
assert system_voice.letter_names("و هذا") == "و هذا"
assert pieces("Numbers: الساعة الآن 10.", whole=False) == [("Numbers: ", False), ("الساعة الآن 10.", True)]
assert read("افْتَحْ") == "إِفْتَحْ"
assert read("ثُمّ") == shadda_first("ثُمَّ")

# Numbers as Arabic words, for MagpieTTS.
import arabic_numbers  # noqa: E402

assert arabic_numbers.words(2026) == "ألفان وستة وعشرون"
assert arabic_numbers.words(3000) == "ثلاثة آلاف"
assert arabic_numbers.words(2500000) == "مليونان وخمسمئة ألف"
assert arabic_numbers.spell_numbers("عندي 3 ملفات و25 اختبارا") == "عندي ثلاثة ملفات وخمسة وعشرون اختبارا"
assert arabic_numbers.spell_numbers("السرعة 3.5 و١٢ وv2") == "السرعة ثلاثة فاصلة خمسة واثنا عشر وv2"

# Magpie's runtime is chosen per platform; Windows on ARM and others without a build fall back to Nabra.
import magpie  # noqa: E402

assert all(name.startswith("nemo-speech-0.3.0-") and len(digest) == 64 for name, digest in magpie.BUILDS.values())

# Shakkelha itself, when its weights are installed here (run with the voice's Python for NumPy).
import importlib.util  # noqa: E402

if os.path.exists(os.path.join(arabic.DIR, "shakkelha.npz")) and importlib.util.find_spec("numpy"):
    from shakkelha import Shakkelha

    shakkelha = Shakkelha(os.path.join(arabic.DIR, "shakkelha.npz"))
    said = shakkelha("أصلحت مشكلتين في الخادم، وجميع الاختبارات تعمل الآن.")
    assert said.startswith("أَصْلَحْتُ مُشْكِلَتَيْنِ فِي الْخَادِمِ"), said
    assert shakkelha("Save الملف").startswith("Save ")  # other letters pass through
    assert "\u063A\u064E\u0651" in shakkelha("ثم شغّل البرنامج")  # a written shadda is kept, with a vowel
print("arabic: all checks pass")
