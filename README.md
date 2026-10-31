# claude-voice

Claude Code reads its replies aloud with [Kokoro](https://github.com/thewh1teagle/kokoro-onnx), a natural-sounding voice that runs entirely on your Mac. No account, no API key, nothing leaves your machine.

- **Speaks while it types.** Each sentence is read as soon as it is written.
- **Speak pane.** Every reply as a list of sentences: play one, read from any line on, pause, skip, stop, switch between replies, reload the whole chat.
- **Live highlight.** The sentence being spoken turns green.
- **Tech-aware.** Paths read as file names, links as their site, versions as "1 point 2", `snake_case` as two words, symbols like → and ≥ as words, code blocks and tables as a short note ("Python code, 12 lines"), and a pronunciation list (RAM, JSON, macOS, ...) you can extend.
- **Secrets stay quiet.** API keys, bearer tokens and `--token` values are read as "redacted".
- **Tells you when it is waiting.** When you are away from the terminal, permission prompts, questions and plans are said aloud ahead of everything else, and a long turn ends with "All done."
- **Several sessions.** Replies queue instead of talking over each other, with a short "From <project>" label, and each project can have its own voice.
- **Settings in the pane.** Voice, speed, mute, speak only when you are away from the terminal, summaries of long replies. British voices get British pronunciation.
- **Timed mute.** `/mute 30m`, `/mute 2h`, `/mute until 3pm`, `/mute off`.

## Install

Requires macOS, Python 3.10 to 3.12 and about 350 MB for the voice model.

```sh
git clone https://github.com/Abdalrhman-Attar/claude-voice.git
cd claude-voice
./install.sh
```

The installer creates `~/.claude/kokoro` (Python environment, model files, settings), starts the voice server at login, and binds **ctrl+x s** to Stop.

Then, in Claude Code:

```
/plugin install speak-pane --marketplace Abdalrhman-Attar/claude-voice
/reload-plugins
```

Open the pane with `/speak` (it opens by itself in a terminal 144 columns or wider).

## Use

| Key (in the pane) | Does |
| --- | --- |
| click a line | play that sentence |
| `»` | read from that line to the end |
| `p` | play the whole reply |
| `a` | pause or resume |
| `x` | skip to the next sentence |
| `s`, ctrl+x s, `/hush` | stop |
| `/mute [30m, until 3pm, off]` | mute for a while |
| `b` / `n` | previous / next reply |
| `j` / `k` | scroll the sentence list |
| `r` | load every reply of this chat |
| `v`, `m` | next voice, mute |

Sending a message stops the current reply.

## Pronunciation

Add words to `~/.claude/kokoro/pronounce.json`, as the word mapped to how it should sound:

```json
{ "Careem": "kareem", "kubectl": "cube control" }
```

Changes apply to the next sentence.

## Files

| Where | What |
| --- | --- |
| `hooks/` | the Claude Code plugin (pane, live speech, settings) |
| `server/` | the voice server, its client and the chat history reader |
| `~/.claude/kokoro/` | environment, models, `settings.json`, `pronounce.json`, `server.log` |

## Uninstall

```sh
./install.sh --uninstall
```

then `/plugin uninstall speak-pane` and delete `~/.claude/kokoro`.
