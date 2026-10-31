# Markdown reply -> speakable sentences with pauses, for agents other than Claude Code.
# A port of hooks/sentences.ts (toSentences, pausesFor); keep the two in step.
import re

LANGUAGES = {
    "py": "Python", "python": "Python", "ts": "TypeScript", "tsx": "TypeScript", "typescript": "TypeScript",
    "js": "JavaScript", "jsx": "JavaScript", "javascript": "JavaScript", "json": "JSON", "yaml": "YAML",
    "yml": "YAML", "sh": "shell", "bash": "shell", "zsh": "shell", "shell": "shell", "console": "shell",
    "powershell": "shell", "ps1": "shell", "html": "HTML", "css": "CSS", "sql": "SQL", "go": "Go",
    "rust": "Rust", "rs": "Rust", "swift": "Swift", "kotlin": "Kotlin", "dart": "Dart", "java": "Java",
    "ruby": "Ruby", "rb": "Ruby", "diff": "diff", "toml": "TOML", "md": "markdown", "markdown": "markdown",
}
SPOKEN = r"[A-Za-z0-9\u0600-\u06FF]"  # a piece with nothing to say has none of these
PARAGRAPH_PAUSE = 0.5
LINE_PAUSE = 0.25

# A sentence end, except after a quoted list number ("1. Does") or an abbreviation ("e.g. this").
_NOT_AFTER = ["e.g.", "i.e.", "vs.", "Dr.", "Mr.", "Mrs.", "Ms.", "St.", "cf.", "approx."]
SENTENCE_END = re.compile(
    r"(?<=[.!?\u061F])(?<![“\"'(]\d\.)(?<![“\"'(]\d\d\.)(?<![“\"'(]\d\d\d\.)"
    + "".join(f"(?<!\\b{re.escape(a)})" for a in _NOT_AFTER) + r"\s+")


def describe_code(lang, body):
    name = LANGUAGES.get(lang.strip().lower(), "")
    lines = len([l for l in body.split("\n") if l.strip()])
    if name == "shell":
        return "Shell command." if lines <= 1 else f"Shell commands, {lines} lines."
    what = f"{name} code" if name else "Code block"
    return f"{what}, one line." if lines <= 1 else f"{what}, {lines} lines."


def describe_table(rows):
    n = len([r for r in rows.split("\n") if re.match(r"^\s*\|", r) and not re.match(r"^\s*\|[\s:|-]*$", r)]) - 1
    return "Table, one row." if n <= 1 else f"Table, {n} rows."


def to_sentences(answer):
    """[(text, paragraph, line)] for a markdown reply; code and tables become a short note."""
    text = re.sub(r"```([^\n`]*)\n?([\s\S]*?)```", lambda m: f"\n\n{describe_code(m.group(1), m.group(2))}\n\n", answer)
    text = re.sub(r"(?:^[ \t]*\|.*(?:\n|$))+", lambda m: f"\n\n{describe_table(m.group(0))}\n\n", text, flags=re.M)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(?<![A-Za-z0-9])_+|_+(?![A-Za-z0-9])", "", text)  # emphasis, not snake_case
    text = re.sub(r"^\s*#+\s*", "", text, flags=re.M)  # headings
    text = re.sub(r"(?<!\w)#(?![\w])", "", text)  # a lone #, but not #42, C# or #channel
    text = re.sub(r"(?<!\|)\|(?!\|)", "", text)  # a table border, but not ||
    text = re.sub(r"[*`>]", "", text)
    text = re.sub(r"^\s*[-+]\s+", "", text, flags=re.M)

    lines = [(l.strip(), para) for para, block in enumerate(re.split(r"\n\s*\n", text))
             for l in re.split(r"\n+", block) if re.search(SPOKEN, l)]
    # A short label line ending in a colon ("Fix:") joins the line after it.
    joined = []
    i = 0
    while i < len(lines):
        cur, para = lines[i]
        if i + 1 < len(lines) and cur.endswith(":") and len(cur.split()) <= 3:
            lines[i + 1] = (f"{cur} {lines[i + 1][0]}", para)
        else:
            joined.append((cur, para))
        i += 1

    out = []
    for line, (l, para) in enumerate(joined):
        marker = (re.match(r"^\d+[.)]\s+", l) or [""])[0]
        parts = []
        for s in SENTENCE_END.split(l[len(marker):]):
            s = s.strip()
            if not re.search(SPOKEN, s):
                continue
            if parts and re.search(r":\s*\d+[.)]$", parts[-1]):
                parts[-1] = f"{parts[-1]} {s}"  # "Steps: 1." goes back with its item
            else:
                parts.append(s)
        if parts and marker:
            parts[0] = f"{marker.strip()} {parts[0]}"
        out += [(s, para, line) for s in parts]
    return out


def pauses_for(sentences):
    """Seconds of quiet before each sentence: longer at a paragraph, shorter at a new line."""
    out = []
    for k, (_, para, line) in enumerate(sentences):
        if k == 0:
            out.append(0.0)
        else:
            _, p, l = sentences[k - 1]
            out.append(PARAGRAPH_PAUSE if p != para else LINE_PAUSE if l != line else 0.0)
    return out
