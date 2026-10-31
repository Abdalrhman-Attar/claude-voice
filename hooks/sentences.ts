import type { Playback, Sentence, SpeakSettings } from '../types'

export const DEFAULT_SETTINGS: SpeakSettings = {
  voice: 'af_heart',
  speed: 1.1,
  muted: false,
  awayOnly: false,
  summarize: true,
  live: true,
}
export const IDLE: Playback = { mine: false, ref: null, index: null, state: 'idle' }

// Turns a markdown reply into speakable sentences, each tagged with its paragraph and line.
export function toSentences(answer: string): Sentence[] {
  const text = answer
    .replace(/```[\s\S]*?```/g, '\n\n')
    .replace(/^\s*\|.*$/gm, '')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/[*#`_>|]/g, '')
    .replace(/^\s*[-+]\s+/gm, '')

  const lines = text
    .split(/\n\s*\n/)
    .flatMap((block, para) => block.split(/\n+/).map(l => ({ l: l.trim(), para })))
    .filter(x => /[a-zA-Z0-9]/.test(x.l))
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
      .split(/(?<=[.!?])(?<![“"'(]\d{1,3}\.)\s+/)
      .map(s => s.trim())
      .filter(s => /[a-zA-Z0-9]/.test(s))
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
export function completeSentences(text: string, isFinal: boolean): Sentence[] {
  const fences = text.split('```').length - 1
  const usable = fences % 2 === 1 ? text.slice(0, text.lastIndexOf('```')) : text
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

// The first sentence shown: `top`, unless the sentence being spoken is outside the window.
export function windowStart(sentences: Sentence[], top: number, live: number | null, rows: number, width: number): number {
  const start = Math.min(Math.max(0, top), Math.max(0, sentences.length - 1))
  if (live === null || (live >= start && live < start + fitting(sentences, start, rows, width))) {
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
  }
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
