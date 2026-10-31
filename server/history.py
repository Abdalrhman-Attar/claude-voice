#!/usr/bin/env python3
# Prints a JSON list of Claude's final reply per turn for a session id, oldest first.
# A turn starts at a prompt the person typed; its reply is the text after the turn's last tool call.
import glob, json, os, sys

matches = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{sys.argv[1]}.jsonl"))
if not matches:
    print("[]")
    sys.exit(0)

replies, current = [], []
for line in open(matches[0]):
    try:
        e = json.loads(line)
    except Exception:
        continue
    if e.get("isSidechain") or e.get("isMeta"):
        continue
    content = (e.get("message") or {}).get("content")
    if e.get("type") == "user":
        is_prompt = isinstance(content, str) or (
            isinstance(content, list) and not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content))
        if is_prompt:
            if current:
                replies.append("\n\n".join(current))
            current = []
    elif e.get("type") == "assistant" and isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                current = []  # text before a tool call is narration, not the reply
            elif b.get("type") == "text" and b.get("text", "").strip():
                current.append(b["text"])
if current:
    replies.append("\n\n".join(current))
print(json.dumps(replies))
