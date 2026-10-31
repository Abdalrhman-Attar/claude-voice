# Numbers in Arabic text as Arabic words, for voices that skip digits (MagpieTTS has no Arabic
# number reading of its own). Cardinal numbers in the plain masculine form, up to the billions;
# a decimal is read digit-wise after فاصلة ("ثلاثة فاصلة خمسة").
import re

ONES = ["صفر", "واحد", "اثنان", "ثلاثة", "أربعة", "خمسة", "ستة", "سبعة", "ثمانية", "تسعة", "عشرة",
        "أحد عشر", "اثنا عشر", "ثلاثة عشر", "أربعة عشر", "خمسة عشر", "ستة عشر", "سبعة عشر", "ثمانية عشر", "تسعة عشر"]
TENS = ["", "", "عشرون", "ثلاثون", "أربعون", "خمسون", "ستون", "سبعون", "ثمانون", "تسعون"]
HUNDREDS = ["", "مئة", "مئتان", "ثلاثمئة", "أربعمئة", "خمسمئة", "ستمئة", "سبعمئة", "ثمانمئة", "تسعمئة"]
# (value, one, two, three to ten, eleven and up)
SCALES = [(10 ** 9, "مليار", "ملياران", "مليارات", "مليار"),
          (10 ** 6, "مليون", "مليونان", "ملايين", "مليون"),
          (1000, "ألف", "ألفان", "آلاف", "ألف")]
DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
# A number on its own, or joined to و ("و25" is "وخمسة وعشرون").
NUMBER = re.compile(r"(?:(?<=\u0648)|(?<![\w.]))(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?![\w])")


def below_thousand(n):
    parts = []
    if n >= 100:
        parts.append(HUNDREDS[n // 100])
        n %= 100
    if n >= 20:
        parts.append(f"{ONES[n % 10]} و{TENS[n // 10]}" if n % 10 else TENS[n // 10])
    elif n:
        parts.append(ONES[n])
    return " و".join(parts)


def words(n):
    """A whole number as Arabic words."""
    if n < 20:
        return ONES[n]
    parts = []
    for value, one, two, few, many in SCALES:
        count, n = divmod(n, value)
        if count == 1:
            parts.append(one)
        elif count == 2:
            parts.append(two)
        elif 3 <= count <= 10:
            parts.append(f"{below_thousand(count)} {few}")
        elif count:
            parts.append(f"{below_thousand(count)} {many}")
    if n:
        parts.append(below_thousand(n))
    return " و".join(parts)


def spell_numbers(text):
    """Digits (Western or Arabic-Indic) as Arabic words: "عندي 25 ملفا" -> "عندي خمسة وعشرون ملفا"."""
    def say(m):
        whole = words(int(m.group(1).replace(",", "")))
        if m.group(2):
            whole += " فاصلة " + " ".join(ONES[int(d)] for d in m.group(2))
        return whole

    return NUMBER.sub(say, text.translate(DIGITS))
