# Voice test

Paste this into Claude Code ("read this back to me") or play it from the Speak pane to hear every rule at once.

## Acronyms and plurals

I merged 3 PRs, fixed 2 APIs and renamed the IDs. The CLI, the IDE and CI/CD all pass, and I/O is faster. The QA team gave an ETA in the FAQ. We run on AWS with GPUs, an LLM and a REST API, using JSON, YAML and SQL.

## Numbers, units and time

Load time dropped from 200ms to 16ms at 60fps, using 4GB of RAM and a 16px font. Release v2.1.0 shipped on 2026-10-31 at 3:15, fixing issue #42. The meeting is at 9:05, not 10:00. It costs $20 a month, or $1.5k a year, for 10k users. Coverage is up 12%, and the build is ~30 seconds.

## Code and the web

Open localhost:3000 or 127.0.0.1:8080. The bug was in src/hooks/register.tsx:212, in the speak_pane module, where a == b && c || d, and x != y. Read the docs at https://github.com/Abdalrhman-Attar/claude-voice. Press Cmd+K, then Ctrl+Shift+P, then Esc.

```ts
const answer = 42
```

| Tool | Version |
| --- | --- |
| Node.js | 22 |
| Vite | 6 |

## Names

Use Redis with Deno and Vite, then lint with ESLint and check tsconfig. Next.js and Node.js, w/ i18n and a11y, w/o LaTeX. C++, C# and Objective-C. GitHub, macOS, iOS, nginx, kubectl and Xcode. The site is live, and the fix is in.

## Secrets stay quiet

The key was sk-proj-abcdefghijklmnop1234 and the header was Bearer abcdef123456789xyz.

## Lists and pauses

Steps to check:

1. Does it pause between paragraphs?
2. Does it read e.g. and i.e. without stopping, and vs. as versus?
3. Does the last word of the last sentence play once?
