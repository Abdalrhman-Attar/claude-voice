# Read Aloud for Claude Code

Hear replies from Claude Code, Codex, Gemini CLI and Cursor read aloud with [Kokoro](https://github.com/thewh1teagle/kokoro-onnx), a natural voice that runs entirely on your computer, and replay any sentence.

- **Replies panel** in the activity bar, in your theme's colors: a session picker grouped by agent keeps sessions in the same folder apart, and each reply is a list of sentences. Click one to read from there; on hover, ▷ reads just that sentence and ⏩ reads to the end. The sentence being spoken is highlighted and kept in view.
- **Controls** for play, pause, skip and stop, also from the status bar and the keyboard: ctrl+cmd+. stops, ctrl+cmd+, pauses, ctrl+cmd+/ skips (ctrl+alt on Windows and Linux).
- **Settings** shared with the Claude Code plugin: voice, speed, auto-speak, only when away, speak while typing, work updates, a voice per project, summaries.

## Setup

Open the **Read Aloud** panel and click **Set up**. It installs everything in one step, with progress shown: Python if you need it (a private copy, via uv), the voice and its 350 MB model, start at login, and the Claude Code plugin. Then **Test the voice**.

Finished Claude Code replies are spoken right away, read from Claude Code's session files. The plugin adds speech while Claude types. Codex, Gemini CLI and Cursor can be connected with the installer in the [repository](https://github.com/Abdalrhman-Attar/claude-voice).

**Arabic:** run **Read Aloud: Add the Arabic voice** from the Command Palette to read Arabic in Nabra, a neural Arabic voice (about 250 MB). Without it, Arabic uses your system's Arabic voice (Majed on macOS).
