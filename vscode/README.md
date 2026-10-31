# Claude Voice

Hear replies from Claude Code, Codex, Gemini CLI and Cursor read aloud with [Kokoro](https://github.com/thewh1teagle/kokoro-onnx), a natural voice that runs entirely on your computer, and replay any sentence.

- **Replies panel** in the activity bar, in your theme's colors: a session picker grouped by agent keeps sessions in the same folder apart, and each reply is a list of sentences. Click one to read from there; on hover, ▷ reads just that sentence and ⏩ reads to the end. The sentence being spoken is highlighted and kept in view.
- **Controls** for play, pause, skip and stop, also from the status bar and the keyboard: ctrl+cmd+. stops, ctrl+cmd+, pauses, ctrl+cmd+/ skips (ctrl+alt on Windows and Linux).
- **Settings** shared with the Claude Code plugin: voice, speed, auto-speak, only when away, speak while typing, work updates, a voice per project, summaries.

## Setup

The voice runs locally (Python and a 350 MB model). Run **Claude Voice: Set up the voice** from the command palette, or the installer in the [repository](https://github.com/Abdalrhman-Attar/claude-voice). Replies are spoken by the Claude Code plugin and the agent hooks the installer adds; this extension shows and replays them.
