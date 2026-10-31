import { expect, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { On } from 'claude-code'

import {
  completeSentences,
  fitting,
  parseMute,
  parseStatus,
  pausesFor,
  permissionText,
  questionText,
  toSentences,
  windowStart,
} from '../hooks/sentences'

const texts = (answer: string) => toSentences(answer).map(s => s.text)

const PANE = {
  title: 'Speak',
  isFocused: false,
  bodyColumns: 60,
  placement: 'dock',
  scroll: { offset: 0, bodyRows: 20 },
  view: {},
} as const

type Ui = { press: (t: { key: string }) => Promise<unknown> }

// Presses a control by its terminal key. Remote surfaces (desktop, VS Code) draw the sentence
// as plain text, so its play goes through the ▶ button beside it.
function tap(ui: Ui, surface: string, key: string, _reply = 0) {
  return ui.press({ key: surface !== 'terminal' && /^s\d+$/.test(key) ? `p${key.slice(1)}` : key })
}

const NOW = new Date(2026, 9, 31, 14, 0).getTime()
const OK = { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false }

type Host = {
  sent: Record<string, unknown>[]
  files: Record<string, string>
  inFront: boolean
  summaries: number
}

// Stands in for the host: files in memory, Kokoro requests recorded, history.py answered.
function fakeHost(on: On, { history = [] as string[], settings = {}, inFront = false } = {}): Host {
  const host: Host = {
    sent: [],
    files: { '/home/me/.claude/kokoro/settings.json': JSON.stringify(settings) },
    inFront,
    summaries: 0,
  }
  on('turn.complete', () => ({ text: '' }))
  on('telemetry.mark', () => ({ value: undefined }))
  on('session.id', () => ({ value: 'sess' }))
  on('clock.now', () => ({ value: NOW }))
  on('session.cwd', () => ({ value: '/work/My Project' }))
  on('env.get', (_$, e) => ({ value: ({ HOME: '/home/me', __CFBundleIdentifier: 'com.example.term' } as Record<string, string>)[e.name] }))
  on('fs.read', (_$, e) => {
    const text = host.files[e.path]
    if (text === undefined) {
      throw new Error('ENOENT')
    }
    return { value: text }
  })
  on('fs.write', (_$, e) => {
    host.files[e.path] = e.text
    return { value: undefined }
  })
  on('model.complete', () => {
    host.summaries += 1
    return { value: { isAnswered: true, text: 'Short summary.', usage: { input_tokens: 1, output_tokens: 1, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 } } }
  })
  on('process.run', async (_$, e) => {
    if (e.argv.some(a => a.endsWith('history.py'))) {
      return { value: { ...OK, stdout: JSON.stringify(history) } }
    }
    if (e.argv.includes('--check-away')) {
      return { value: { ...OK, exitCode: host.inFront ? 1 : 0 } }
    }
    host.sent.push(JSON.parse(e.init?.stdin ?? '{}'))
    return { value: OK }
  })

  return host
}

const reply = ($: Engine, answer: string, turnId = 't1', agentId?: string) =>
  $.turn.complete({ answer, durationMs: 1, isAborted: false, turnId, reason: 'answer', agentId })

// Auto-speak runs after the turn without holding it; let its engine calls settle.
async function settle($: Engine) {
  for (let i = 0; i < 100; i++) {
    await $.telemetry.mark({ feature: 'settle', kind: 'ok' })
  }
}

test('splits markdown into sentences', () => {
  expect(texts('**Hi** there. Run `x`!\n- item one\n```\ncode\n```')).toEqual(['Hi there.', 'Run x!', 'item one', 'Code block, one line.'])
})

test('says what a code block or table is instead of reading it', () => {
  expect(texts('See:\n```python\na = 1\nb = 2\n```\nThen.')).toEqual(['See: Python code, 2 lines.', 'Then.'])
  expect(texts('```bash\nnpm test\n```')).toEqual(['Shell command.'])
  expect(texts('![a diagram](x.png) and [the docs](https://x.dev).')).toEqual(['a diagram and the docs.'])
})

test('keeps snake_case and abbreviations whole', () => {
  expect(texts('The speak_pane plugin, _really_. Use e.g. this vs. that. Next.')).toEqual([
    'The speak_pane plugin, really.',
    'Use e.g. this vs. that.',
    'Next.',
  ])
})

test('parses mute durations', () => {
  expect(parseMute('', NOW)).toBe(Infinity)
  expect(parseMute('off', NOW)).toBe(0)
  expect(parseMute('30m', NOW)).toBe(NOW + 30 * 60_000)
  expect(parseMute('for 2 hours', NOW)).toBe(NOW + 2 * 3_600_000)
  expect(parseMute('until 3pm', NOW)).toBe(new Date(2026, 9, 31, 15, 0).getTime())
  expect(parseMute('until 9:30', NOW)).toBe(new Date(2026, 10, 1, 9, 30).getTime())
  expect(parseMute('soon', NOW)).toBe(null)
})

test('alert wording', () => {
  expect(permissionText('Bash', { command: 'rm -rf build', description: 'Remove the build folder' })).toBe(
    'I need permission to run: Remove the build folder.',
  )
  expect(permissionText('Edit', { file_path: '/a/b/server.py' })).toBe('I need permission to edit server.py.')
  expect(permissionText('mcp__github__create_issue', {})).toBe('I need permission to use github create issue.')
  expect(questionText({ questions: [{ question: 'Which one?', options: [{ label: 'A (Recommended)' }, { label: 'B' }] }] })).toEqual([
    'I have a question: Which one?',
    'Options: A, recommended; B.',
  ])
})

test('a label with a colon stays with its text', () => {
  expect(texts('**Cause:** the hook ran early. Fix: wait for it.')).toEqual(['Cause: the hook ran early.', 'Fix: wait for it.'])
  expect(texts('What changed:\n- one thing')).toEqual(['What changed: one thing'])
  expect(texts('A longer label than three words:\n- item')).toEqual(['A longer label than three words:', 'item'])
  expect(texts('It is version 2. Next one.')).toEqual(['It is version 2.', 'Next one.'])
  expect(texts('Then "1. Does it play?" becomes one. Done.')).toEqual(['Then "1. Does it play?" becomes one.', 'Done.'])
  expect(
    texts('**Splitting:** one split was wrong. `"Steps to check:" followed by "1. Does it play?"` was cut after `"1.`, and it now stays.'),
  ).toEqual(['Splitting: one split was wrong.', '"Steps to check:" followed by "1. Does it play?" was cut after "1., and it now stays.'])
})

test('a table is said as its size', () => {
  expect(texts('Before.\n| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\nAfter.')).toEqual(['Before.', 'Table, 2 rows.', 'After.'])
  // Still being written: held back until it ends.
  expect(completeSentences('Before.\n| a | b |\n|---|---|\n| 1 | 2 |\n', false).map(s => s.text)).toEqual(['Before.'])
})

test('tags sentences with their paragraph', () => {
  expect(toSentences('One. Two.\n\nThree.')).toEqual([
    { text: 'One.', para: 0, line: 0 },
    { text: 'Two.', para: 0, line: 0 },
    { text: 'Three.', para: 1, line: 1 },
  ])
})

test('pauses longer between paragraphs than between list items', () => {
  const list = toSentences('One. Two.\n- item a\n- item b\n\nNew paragraph.')
  expect(pausesFor(list)).toEqual([0, 0, 0.25, 0.25, 0.5])
  expect(pausesFor(list, 3)).toEqual([0.25, 0.5])
})

test('keeps list numbers with their item', () => {
  expect(texts('Steps to check:\n\n1. Does it play? It should.\n2) Stop works.')).toEqual([
    'Steps to check: 1. Does it play?',
    'It should.',
    '2) Stop works.',
  ])
})

test('holds back the unfinished sentence and open code', () => {
  const texts = (t: string, f: boolean) => completeSentences(t, f).map(s => s.text)
  expect(texts('One done. Two is still go', false)).toEqual(['One done.'])
  expect(texts('One done. Two is still go', true)).toEqual(['One done.', 'Two is still go'])
  expect(texts('Intro.\n```\nhalf code', false)).toEqual(['Intro.'])
})

test('windows the list and follows the spoken sentence', () => {
  const list = toSentences(Array.from({ length: 30 }, (_, i) => `Sentence number ${i}.`).join(' '))
  expect(fitting(list, 0, 5, 40)).toBe(5)
  expect(windowStart(list, 0, null, 5, 40)).toBe(0)
  expect(windowStart(list, 0, 3, 5, 40)).toBe(0)
  expect(windowStart(list, 0, 12, 5, 40)).toBe(11)
})

test('reads server status for this session only', () => {
  const raw = JSON.stringify({ session: 'sess', ref: 't1', index: 2, state: 'playing' })
  expect(parseStatus(raw, 'sess')).toEqual({ mine: true, ref: 't1', index: 2, state: 'playing' })
  expect(parseStatus(raw, 'other')).toEqual({ mine: false, ref: null, index: null, state: 'playing' })
  expect(parseStatus('garbage', 'sess').state).toBe('idle')
})

test('auto-speaks a finished reply with its sentences', async ($, on) => {
  const host = fakeHost(on)
  await reply($, 'First. Second.')
  await settle($)

  expect(host.sent).toEqual([
    {
      mode: 'reply',
      sentences: ['First.', 'Second.'],
      pauses: [0, 0],
      ref: 't1',
      offset: 0,
      session: 'sess',
      label: 'My Project',
    },
  ])
})

test('muted means no auto-speak', async ($, on) => {
  const host = fakeHost(on, { settings: { muted: true } })
  await reply($, 'First.')
  await settle($)

  expect(host.sent).toEqual([])
})

test('only-when-away stays quiet while the terminal is in front', async ($, on) => {
  const host = fakeHost(on, { settings: { awayOnly: true }, inFront: true })
  await reply($, 'First.')
  await settle($)
  expect(host.sent).toEqual([])

  host.inFront = false
  await reply($, 'Second.', 't2')
  await settle($)
  expect(host.sent.map(r => r.sentences)).toEqual([['Second.']])
})

test('long replies are summarized when summaries are on', async ($, on) => {
  const host = fakeHost(on)
  await reply($, 'This sentence is long enough to count. '.repeat(40))
  await settle($)

  expect(host.summaries).toBe(1)
  expect(host.sent.map(r => r.sentences)).toEqual([['Short summary.']])
})

for (const surface of ['terminal', 'desktop', 'vscode'] as const) {
  const mount = ($: Engine) => $.ui.mount({ plugin: 'speak-pane', surface, component: 'Pane', props: PANE, requestId: 'speak' })

  test(`playback buttons on ${surface}`, async ($, on) => {
    const host = fakeHost(on, { settings: { muted: true } })
    await reply($, 'First one. Second one!')
    const ui = await mount($)

    await tap(ui, surface, 's1')
    await tap(ui, surface, 'p1')
    await tap(ui, surface, 'f0')
    await tap(ui, surface, 'all')
    await tap(ui, surface, 'pause')
    await tap(ui, surface, 'skip')
    await tap(ui, surface, 'stop')
    expect(host.sent.map(({ session, label, ...r }) => r)).toEqual([
      { mode: 'play', sentences: ['Second one!'], ref: 't1', offset: 1 },
      { mode: 'play', sentences: ['Second one!'], ref: 't1', offset: 1 },
      { mode: 'play', sentences: ['First one.', 'Second one!'], pauses: [0, 0], ref: 't1', offset: 0 },
      { mode: 'play', sentences: ['First one.', 'Second one!'], pauses: [0, 0], ref: 't1', offset: 0 },
      { mode: 'pause' },
      { mode: 'skip' },
      { mode: 'stop', all: true },
    ])
  })

  test(`settings buttons save settings on ${surface}`, async ($, on) => {
    const host = fakeHost(on, { settings: { muted: true } })
    await reply($, 'Hello.')
    const ui = await mount($)

    await tap(ui, surface, 'voice')
    await tap(ui, surface, 'faster')
    await tap(ui, surface, 'mute')
    await tap(ui, surface, 'away')
    expect(JSON.parse(host.files['/home/me/.claude/kokoro/settings.json'] ?? '{}')).toEqual({
      voice: 'af_bella',
      speed: 1.2,
      muted: false,
      awayOnly: true,
      summarize: true,
      live: true,
      projectVoices: false,
      mutedUntil: 0,
    })
  })

  test(`switches between replies on ${surface}`, async ($, on) => {
    const host = fakeHost(on, { settings: { muted: true } })
    await reply($, 'Old reply.', 't1')
    await reply($, 'New reply.', 't2')
    const ui = await mount($)

    expect((await ui.find({ type: 'Text', text: /Reply \d+ of \d+/ }))?.text).toBe('Reply 2 of 2')
    await tap(ui, surface, 'prev', 0)
    expect((await ui.find({ type: 'Text', text: /Reply \d+ of \d+/ }))?.text).toBe('Reply 1 of 2')
    await tap(ui, surface, 's0')
    expect(host.sent.map(r => r.sentences)).toEqual([['Old reply.']])
  })

  test(`loads chat history on ${surface}`, async ($, on) => {
    const host = fakeHost(on, { history: ['Earlier one.', 'Earlier two. More.'] })
    const ui = await mount($)

    await tap(ui, surface, 'rebuild')
    expect((await ui.find({ type: 'Text', text: /Reply \d+ of \d+/ }))?.text).toBe('Reply 2 of 2')
    await tap(ui, surface, 's1')
    expect(host.sent.map(r => r.sentences)).toEqual([['More.']])
  })

  test(`subagent replies are ignored on ${surface}`, async ($, on) => {
    const host = fakeHost(on)
    await reply($, 'Sub.', 't1', 'a1')
    await settle($)
    const ui = await mount($)

    expect(await ui.findAll({ type: 'Button', key: 's0' })).toHaveLength(0)
    expect(host.sent).toEqual([])
  })
}

test('sending a message stops this session', async ($, on) => {
  const host = fakeHost(on)
  on('prompt.submit', async (_$, e) => ({ text: e.text }))

  await $.prompt.submit({ text: 'a notice', wait: false, origin: { kind: 'task-notification' } })
  await settle($)
  expect(host.sent).toEqual([])
  await $.prompt.submit({ text: 'next question', wait: false, origin: { kind: 'composer' } })
  await settle($)
  expect(host.sent).toEqual([{ mode: 'stop', session: 'sess', label: 'My Project' }])
})

// The model's stream for one step, as the engine would raise it beneath the plugin.
function fakeStream(on: On, pieces: string[], stopReason: 'end_turn' | 'tool_use' = 'end_turn') {
  on('turn.step', async function* (_$, e) {
    for (const text of pieces) {
      yield { kind: 'text', index: 0, text }
    }
    return { turnId: e.turnId, index: e.index, answer: pieces.join(''), toolUses: [], stopReason, usage: null }
  })
}

const step = async ($: Engine, turnId: string, index = 0) => {
  for await (const _ of $.turn.step({ turnId, index, model: 'm', messageCount: 1 })) {
    // drain
  }
}

test('speaks sentences while the reply streams, then not again at the end', async ($, on) => {
  const host = fakeHost(on)
  fakeStream(on, ['First sen', 'tence. Second', ' one! Third'])
  await step($, 'tz')
  await settle($)
  await reply($, 'First sentence. Second one! Third', 'tz')
  await settle($)

  expect(host.sent.map(({ session, label, ...r }) => r)).toEqual([
    { mode: 'reply', sentences: ['First sentence.'], pauses: [0], ref: 'tz:0', offset: 0, append: true },
    { mode: 'reply', sentences: ['Second one!'], pauses: [0], ref: 'tz:0', offset: 1, append: true },
    { mode: 'reply', sentences: ['Third'], pauses: [0], ref: 'tz:0', offset: 2, append: true },
  ])
})

test('live off speaks only when the reply is done', async ($, on) => {
  const host = fakeHost(on, { settings: { live: false } })
  fakeStream(on, ['Only. ', 'At the end.'])
  await step($, 'tz')
  await settle($)
  expect(host.sent).toEqual([])
  await reply($, 'Only. At the end.', 'tz')
  await settle($)

  expect(host.sent.map(r => r.sentences)).toEqual([['Only.', 'At the end.']])
})

test('muted streams nothing', async ($, on) => {
  const host = fakeHost(on, { settings: { muted: true } })
  fakeStream(on, ['Quiet. Please.'])
  await step($, 'tz')
  await reply($, 'Quiet. Please.', 'tz')
  await settle($)

  expect(host.sent).toEqual([])
})

for (const surface of ['terminal', 'desktop', 'vscode'] as const) {
  test(`list scrolls while controls stay drawn on ${surface}`, async ($, on) => {
    fakeHost(on, { settings: { muted: true } })
    await reply($, Array.from({ length: 40 }, (_, i) => `Line ${i} here.`).join(' '))
    const short = { ...PANE, scroll: { offset: 0, bodyRows: 24 } }
    const ui = await $.ui.mount({ plugin: 'speak-pane', surface, component: 'Pane', props: short, requestId: 'speak' })

    if (surface === 'terminal') {
      expect(await ui.find({ key: 's0' })).toBeDefined()
      expect(await ui.find({ key: 's39' })).toBeUndefined()
      await ui.press({ key: 'down' })
      expect(await ui.find({ key: 's0' })).toBeUndefined()
      for (const key of ['all', 'pause', 'skip', 'stop', 'up', 'down', 'voice', 'mute', 'prev']) {
        expect(await ui.find({ key })).toBeDefined()
      }
      return
    }
    // More rows than the terminal estimate are drawn (the list clips them), not all forty.
    expect(await ui.find({ key: 'p0' })).toBeDefined()
    expect(await ui.find({ key: 'p39' })).toBeUndefined()
    await ui.press({ key: 'down' })
    expect(await ui.find({ key: 'p0' })).toBeUndefined()
    for (const key of ['all', 'pause', 'skip', 'stop', 'up', 'down', 'voice', 'mute', 'prev']) {
      expect(await ui.find({ key })).toBeDefined()
    }
  })
}

test('/hush stops every session', async ($, on) => {
  const host = fakeHost(on)
  await $.command.run({
    command: 'hush',
    args: '',
    origin: { kind: 'composer' },
    presentation: { isFullscreen: false, columns: 80 },
  })

  expect(host.sent).toEqual([{ mode: 'stop', all: true, session: 'sess', label: 'My Project' }])
})

test('/mute for a while stops speech and mutes until then', async ($, on) => {
  const host = fakeHost(on)
  const run = (args: string) =>
    $.command.run({ command: 'mute', args, origin: { kind: 'composer' }, presentation: { isFullscreen: false, columns: 80 } })
  const settings = () => JSON.parse(host.files['/home/me/.claude/kokoro/settings.json'] ?? '{}')

  await run('30m')
  expect(host.sent.map(r => r.mode)).toEqual(['stop'])
  expect(settings().mutedUntil).toBe(NOW + 30 * 60_000)
  await reply($, 'Quiet now.')
  await settle($)
  expect(host.sent).toHaveLength(1)

  await run('off')
  expect(settings()).toMatchObject({ muted: false, mutedUntil: 0 })
})

test('a permission prompt is said ahead of other speech when away', async ($, on) => {
  const host = fakeHost(on)
  on('classic.PermissionRequest', () => ({}))
  const ask = () =>
    $.classic.PermissionRequest({ permission_mode: 'default', tool_name: 'Bash', tool_input: { command: 'ls', description: 'List files' } })

  await ask()
  await settle($)
  expect(host.sent.map(({ session, label, ...r }) => r)).toEqual([
    { mode: 'reply', urgent: true, sentences: ['I need permission to run: List files.'], ref: null },
  ])

  host.inFront = true
  await ask()
  await settle($)
  expect(host.sent).toHaveLength(1)
})

test('a long turn ends with All done when away', async ($, on) => {
  const host = fakeHost(on)
  await $.turn.complete({ answer: 'Fixed it.', durationMs: 45000, isAborted: false, turnId: 't9', reason: 'answer' })
  await settle($)

  expect(host.sent.map(r => r.sentences)).toEqual([['Fixed it.'], ['All done.']])
})
