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
print("arabic: all checks pass")
