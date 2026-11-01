import type { Playback, Sentence, SpeakSettings } from '../types'

export const DEFAULT_SETTINGS: SpeakSettings = {
  voice: 'af_heart',
  speed: 1.1,
  muted: false,
  awayOnly: false,
  summarize: true,
  live: true,
  workUpdates: true,
  projectVoices: false,
  mutedUntil: 0,
  mutedSessions: [],
  outputDevice: '',
}
export const IDLE: Playback = { mine: false, ref: null, index: null, state: 'idle' }

const LANGUAGES: Record<string, string> = {
  py: 'Python', python: 'Python', ts: 'TypeScript', tsx: 'TypeScript', typescript: 'TypeScript',
  js: 'JavaScript', jsx: 'JavaScript', javascript: 'JavaScript', json: 'JSON', yaml: 'YAML', yml: 'YAML',
  sh: 'shell', bash: 'shell', zsh: 'shell', shell: 'shell', console: 'shell', html: 'HTML', css: 'CSS',
  sql: 'SQL', go: 'Go', rust: 'Rust', rs: 'Rust', swift: 'Swift', kotlin: 'Kotlin', dart: 'Dart',
  java: 'Java', ruby: 'Ruby', rb: 'Ruby', diff: 'diff', toml: 'TOML', md: 'markdown', markdown: 'markdown',
}

// What is said in place of a code block: its language and size, not the code.
export function describeCode(lang: string, body: string): string {
  const name = LANGUAGES[lang.trim().toLowerCase()] ?? ''
  const lines = body.split('\n').filter(l => l.trim()).length
  if (name === 'shell') {
    return lines <= 1 ? 'Shell command.' : `Shell commands, ${lines} lines.`
  }
  const what = name ? `${name} code` : 'Code block'

  return lines <= 1 ? `${what}, one line.` : `${what}, ${lines} lines.`
}

// What is said in place of a table: its size.
function describeTable(rows: string): string {
  const n = rows.split('\n').filter(r => /^\s*\|/.test(r) && !/^\s*\|[\s:|-]*$/.test(r)).length - 1

  return n <= 1 ? 'Table, one row.' : `Table, ${n} rows.`
}

// A sentence end, except after a quoted list number ("1. Does") or an abbreviation ("e.g. this").
const SENTENCE_END = /(?<=[.!?\u061F])(?<![“"'(]\d{1,3}\.)(?<!\b(?:e\.g|i\.e|vs|Dr|Mr|Mrs|Ms|St|cf|approx)\.)\s+/

// A piece with none of these has nothing to say (Arabic letters count).
const SPOKEN = /[a-zA-Z0-9\u0600-\u06FF]/

// Turns a markdown reply into speakable sentences, each tagged with its paragraph and line.
// Code blocks and tables become one short sentence saying what is there.
export function toSentences(answer: string): Sentence[] {
  const text = answer
    .replace(/```([^\n`]*)\n?([\s\S]*?)```/g, (_m, lang: string, body: string) => `\n\n${describeCode(lang, body)}\n\n`)
    .replace(/(?:^[ \t]*\|.*(?:\n|$))+/gm, rows => `\n\n${describeTable(rows)}\n\n`)
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/(?<![A-Za-z0-9])_+|_+(?![A-Za-z0-9])/g, '') // emphasis, not snake_case
    .replace(/^\s*#+\s*/gm, '') // headings
    .replace(/(?<!\w)#(?!\w)/g, '') // a lone #, but not #42, C# or #channel
    .replace(/(?<!\|)\|(?!\|)/g, '') // a table border, but not ||
    .replace(/[*`>]/g, '')
    .replace(/^\s*[-+]\s+/gm, '')

  const lines = text
    .split(/\n\s*\n/)
    .flatMap((block, para) => block.split(/\n+/).map(l => ({ l: l.trim(), para })))
    .filter(x => SPOKEN.test(x.l))
  // A short label line ending in a colon ("Fix:") joins the line after it.
  const joined: { l: string; para: number }[] = []
  for (let i = 0; i < lines.length; i++) {
    const cur = lines[i]
    const next = lines[i + 1]
    if (cur && next && cur.l.endsWith(':') && cur.l.split(/\s+/).length <= 3) {
      lines[i + 1] = { l: `${cur.l} ${next.l}`, para: cur.para }
    } else if (cur) {
      joined.push(cur)
    }
  }

  return joined.flatMap(({ l, para }, line) => {
    // A list number ("1.", "2)") stays with its item instead of being a sentence.
    const marker = l.match(/^\d+[.)]\s+/)?.[0] ?? ''
    const parts = l
      .slice(marker.length)
      // Not at a colon ("Fix: the server") or after a quoted list number ("1. Does").
      .split(SENTENCE_END)
      .map(s => s.trim())
      .filter(s => SPOKEN.test(s))
      // A list number left by a joined label ("Steps: 1.") goes back with its item.
      .reduce<string[]>((acc, s) => {
        const last = acc[acc.length - 1]
        if (last !== undefined && /:\s*\d+[.)]$/.test(last)) {
          acc[acc.length - 1] = `${last} ${s}`
        } else {
          acc.push(s)
        }
        return acc
      }, [])
    if (parts.length > 0 && marker) {
      parts[0] = `${marker.trim()} ${parts[0]}`
    }

    return parts.map(s => ({ text: s, para, line }))
  })
}

export const PARAGRAPH_PAUSE = 0.5 // seconds of quiet before a new paragraph
export const LINE_PAUSE = 0.25 // before a new line in the same paragraph, such as a list item

// Seconds of quiet before each of sentences[from..]: longer at a paragraph, shorter at a line.
export function pausesFor(sentences: Sentence[], from = 0): number[] {
  return sentences.slice(from).map((s, k) => {
    const prev = sentences[from + k - 1]
    if (!prev || from + k === 0) {
      return 0
    }
    if (prev.para !== s.para) {
      return PARAGRAPH_PAUSE
    }
    return prev.line !== undefined && prev.line !== s.line ? LINE_PAUSE : 0
  })
}

// Sentences of reply text still streaming in: an unclosed code block is held back, and
// the last sentence waits for its end unless the text is final.
// A table still being written is held back too, so its row count is final when it is said.
export function completeSentences(text: string, isFinal: boolean): Sentence[] {
  const fences = text.split('```').length - 1
  const closed = fences % 2 === 1 ? text.slice(0, text.lastIndexOf('```')) : text
  const table = isFinal ? null : /(?:^[ \t]*\|.*(?:\n|$))+\s*$/m.exec(closed)
  const usable = table ? closed.slice(0, table.index) : closed
  const all = toSentences(usable)

  return isFinal || /\n\s*$/.test(usable) || usable !== text ? all : all.slice(0, -1)
}

// Rows sentence `i` takes in a column `width` cells wide, its paragraph gap included.
function rowsOf(sentences: Sentence[], i: number, width: number): number {
  const s = sentences[i]
  if (!s) {
    return 0
  }
  const gap = i > 0 && sentences[i - 1]?.para !== s.para ? 1 : 0

  return gap + Math.ceil((s.text.length + 2) / Math.max(1, width))
}

// How many sentences from `top` fit in `rows` (at least one).
export function fitting(sentences: Sentence[], top: number, rows: number, width: number): number {
  let used = 0
  let n = 0
  for (let i = top; i < sentences.length; i++) {
    used += rowsOf(sentences, i, width) - (i === top && i > 0 && sentences[i - 1]?.para !== sentences[i]?.para ? 1 : 0)
    if (used > rows && n > 0) {
      break
    }
    n++
  }

  return n
}

// The first sentence shown: `top`, unless the sentence being spoken is outside the window. The
// window's last sentence counts as outside (the estimate runs long when sentences wrap, so it
// may be cut off), so the list turns its page as that sentence starts, not after it ends.
export function windowStart(sentences: Sentence[], top: number, live: number | null, rows: number, width: number): number {
  const start = Math.min(Math.max(0, top), Math.max(0, sentences.length - 1))
  const shown = fitting(sentences, start, rows, width)
  const safe = live === sentences.length - 1 ? shown : Math.max(1, shown - 1)
  if (live === null || (live >= start && live < start + safe)) {
    return start
  }

  return Math.max(0, live - 1)
}

// settings.json as stored, with defaults for anything missing or of the wrong type.
export function parseSettings(raw: string | undefined): SpeakSettings {
  let v: Record<string, unknown> = {}
  try {
    const parsed: unknown = JSON.parse(raw ?? '{}')
    if (typeof parsed === 'object' && parsed !== null) {
      v = parsed as Record<string, unknown>
    }
  } catch {
    // unreadable: defaults
  }
  const d = DEFAULT_SETTINGS

  return {
    voice: typeof v.voice === 'string' ? v.voice : d.voice,
    speed: typeof v.speed === 'number' ? v.speed : d.speed,
    muted: typeof v.muted === 'boolean' ? v.muted : d.muted,
    awayOnly: typeof v.awayOnly === 'boolean' ? v.awayOnly : d.awayOnly,
    summarize: typeof v.summarize === 'boolean' ? v.summarize : d.summarize,
    live: typeof v.live === 'boolean' ? v.live : d.live,
    workUpdates: typeof v.workUpdates === 'boolean' ? v.workUpdates : d.workUpdates,
    projectVoices: typeof v.projectVoices === 'boolean' ? v.projectVoices : d.projectVoices,
    mutedUntil: typeof v.mutedUntil === 'number' ? v.mutedUntil : d.mutedUntil,
    mutedSessions: Array.isArray(v.mutedSessions)
      ? v.mutedSessions.filter((s): s is string => typeof s === 'string')
      : d.mutedSessions,
    outputDevice: typeof v.outputDevice === 'string' ? v.outputDevice : d.outputDevice,
  }
}

const MUTED_SESSIONS_KEPT = 50

// The output after `current` in the order offered: the system's default (''), then each device.
export function nextOutput(devices: string[], current: string): string {
  const all = ['', ...devices.filter(d => d !== '')]

  return all[(all.indexOf(current) + 1) % all.length] ?? ''
}

// The muted sessions with `session` muted or unmuted; the oldest are forgotten past a limit.
export function withSessionMuted(list: string[], session: string, muted: boolean): string[] {
  const rest = list.filter(s => s !== session)

  return (muted ? [...rest, session] : rest).slice(-MUTED_SESSIONS_KEPT)
}

// status.json as the server writes it, seen from session `me`.
export function parseStatus(raw: string | undefined, me: string): Playback {
  try {
    const s = JSON.parse(raw ?? '')
    if (s.state !== 'playing' && s.state !== 'paused') {
      return IDLE
    }
    const mine = s.session === me

    return { mine, ref: mine ? (s.ref ?? null) : null, index: mine ? (s.index ?? null) : null, state: s.state }
  } catch {
    return IDLE
  }
}

// When a timed mute ends, from what follows /mute: "30m", "2h", "90 min", "until 3pm", "until 15:30".
// `off` unmutes (0), nothing mutes until unmuted (Infinity), and anything else is null.
export function parseMute(args: string, now: number): number | null {
  const a = args.trim().toLowerCase()
  if (a === '') {
    return Infinity
  }
  if (a === 'off' || a === 'unmute') {
    return 0
  }
  const span = /^(?:for\s+)?(\d+(?:\.\d+)?)\s*(m|min|mins|minutes?|h|hr|hrs|hours?)$/.exec(a)
  if (span) {
    const n = Number(span[1])
    return now + n * (span[2]?.startsWith('h') ? 3_600_000 : 60_000)
  }
  const at = /^until\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$/.exec(a)
  if (at) {
    let hour = Number(at[1]) % 24
    const minute = Number(at[2] ?? 0)
    if (at[3] === 'pm' && hour < 12) {
      hour += 12
    }
    if (at[3] === 'am' && hour === 12) {
      hour = 0
    }
    const when = new Date(now)
    when.setHours(hour, minute, 0, 0)
    if (when.getTime() <= now) {
      when.setDate(when.getDate() + 1)
    }
    return when.getTime()
  }

  return null
}

// Whether a timed or plain mute holds at `now`.
export function isMuted(conf: SpeakSettings, now: number): boolean {
  return conf.muted || conf.mutedUntil > now
}

// "15:30" for a mute ending today, else "Mon 15:30".
export function untilText(when: number, now: number): string {
  const d = new Date(when)
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`

  return new Date(now).toDateString() === d.toDateString() ? hm : `${d.toDateString().slice(0, 3)} ${hm}`
}

// A short spoken preview of what a tool wants to do, for a permission alert.
export function permissionText(tool: string, input: unknown): string {
  const args = typeof input === 'object' && input !== null ? (input as Record<string, unknown>) : {}
  const str = (k: string) => (typeof args[k] === 'string' ? (args[k] as string) : '')
  const file = (k: string) => str(k).split('/').filter(Boolean).pop() ?? ''
  switch (tool) {
    case 'Bash': {
      const what = str('description') || str('command').split('\n')[0]?.slice(0, 80) || 'a command'
      return `I need permission to run: ${what}.`
    }
    case 'Edit':
    case 'MultiEdit':
    case 'Write':
    case 'NotebookEdit':
      return `I need permission to ${tool === 'Write' ? 'write' : 'edit'} ${file('file_path') || file('notebook_path') || 'a file'}.`
    case 'WebFetch':
      return `I need permission to fetch ${str('url').replace(/^https?:\/\/(www\.)?/, '').split('/')[0] || 'a page'}.`
    default:
      return `I need permission to use ${tool.replace(/^mcp__([^_]+)__/, '$1 ').replace(/[_-]+/g, ' ')}.`
  }
}

// What is said when Claude asks a question with choices.
export function questionText(input: unknown): string[] {
  const qs = (input as { questions?: { question?: unknown; options?: { label?: unknown }[] }[] } | null)?.questions
  if (!Array.isArray(qs)) {
    return []
  }

  return qs.flatMap(q => {
    const question = typeof q.question === 'string' ? q.question : ''
    const labels = (q.options ?? []).map(o => (typeof o.label === 'string' ? o.label.replace(/\s*\(Recommended\)/i, ', recommended') : '')).filter(Boolean)
    return question ? [`I have a question: ${question}`, ...(labels.length ? [`Options: ${labels.join('; ')}.`] : [])] : []
  })
}
