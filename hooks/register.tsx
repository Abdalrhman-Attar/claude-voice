import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, RenderChildren } from 'claude-code'

import type { Panel, Playback, Reply, SpeakSettings } from '../types'
import {
  ago,
  completeSentences,
  DEFAULT_SETTINGS,
  fitting,
  IDLE,
  isLead,
  isMuted,
  isPlaceholder,
  parseMute,
  parseSettings,
  parseStatus,
  pausesFor,
  permissionText,
  progress,
  questionText,
  toSentences,
  untilText,
  windowStart,
  nextOutput,
  withSessionMuted,
} from './sentences'

const PANE = 'speak'
// Engine keybinding borrowed for a global Stop: it has no default key and acts only in the diff panel.
const STOP_ACTION = 'app:toggleDiffPreSession'
const MAX_REPLIES = 100
const SUMMARY_OVER = 1200 // chars of plain text; longer replies are summarized when summaries are on
const POLL_MS = 400
// Without work updates, a step's text starts speaking once it is this long (a short note before a
// tool call is never said); otherwise only when the step ends with no tool call.
const FINAL_HINT_CHARS = 400
const LONG_TURN_MS = 30000 // a turn this long ends with "All done." when the person is away
const REMOTE_ROWS = 3 // VS Code and the desktop app fit about this many rows per terminal row
const VOICES = ['af_heart', 'af_bella', 'af_nicole', 'af_sky', 'am_michael', 'am_adam', 'bf_emma', 'bm_george']

const replies = atom({ plugin: 'speak-pane', key: 'replies' } as const, [])
const view = atom({ plugin: 'speak-pane', key: 'view' } as const, 0)
// The first sentence the list shows when the person scrolled it.
const top = atom({ plugin: 'speak-pane', key: 'top' } as const, 0)
const playback = atom({ plugin: 'speak-pane', key: 'playback' } as const, IDLE)
const prefs = atom({ plugin: 'speak-pane', key: 'prefs' } as const, DEFAULT_SETTINGS)
// The list, the settings or the key help, below the header.
const panelAtom = atom({ plugin: 'speak-pane', key: 'panel' } as const, 'list' as Panel)

type Request = {
  mode: 'reply' | 'play' | 'stop' | 'pause' | 'skip' | 'record'
  reply?: Reply
  sentences?: string[]
  pauses?: number[]
  ref?: string | null
  offset?: number
  all?: boolean
  append?: boolean
  urgent?: boolean
}

// Turns being read aloud while they stream: whether to speak them, and what was sent.
type LiveTurn = { speak: Promise<boolean>; lastStep: number; sent: Promise<void>; workUpdates: boolean }
const liveTurns = new Map<string, LiveTurn>()
// Turns this process saw begin. Only these are spoken: opening or resuming a session can hand
// over turns that ended earlier, and those are listed in the pane but not read out.
const startedTurns = new Set<string>()
const STARTED_KEPT = 50

type Paths = { python: string; tts: string; history: string; settings: string; status: string }

// The server's code ships in this plugin (server/); its data (venv, models, settings, status)
// lives in KOKORO_HOME, ~/.claude/kokoro by default (%USERPROFILE%\.claude\kokoro on Windows).
async function paths($: EngineInterface): Promise<Paths> {
  const windows = (await $.env.get('OS')) === 'Windows_NT'
  const user = (await $.env.get('HOME')) ?? (await $.env.get('USERPROFILE')) ?? ''
  const home = (await $.env.get('KOKORO_HOME')) ?? `${user}/.claude/kokoro`
  const code = `${$.plugin.root}/server`

  return {
    python: windows ? `${home}/.venv/Scripts/python.exe` : `${home}/.venv/bin/python`,
    tts: `${code}/tts.py`,
    history: `${code}/history.py`,
    settings: `${home}/settings.json`,
    status: `${home}/status.json`,
  }
}

// Sends one request to the Kokoro server, tagged with this session and its project.
async function send($: EngineInterface, request: Request) {
  const p = await paths($)
  const cwd = await $.session.cwd()
  const body = {
    ...request,
    session: await $.session.id(),
    label: cwd.split('/').filter(Boolean).pop() ?? '',
    ...(request.mode === 'record' ? { cwd } : {}), // the VS Code panel lists the open folder's replies
  }
  await $.process.run([p.python, p.tts, '--json'], { stdin: JSON.stringify(body), timeoutMs: 30000 })
}

async function loadSettings($: EngineInterface): Promise<SpeakSettings> {
  const stored = parseSettings(await $.fs.read((await paths($)).settings).catch(() => undefined))
  await update($, prefs, () => stored)

  return stored
}

async function saveSettings($: EngineInterface, change: Partial<SpeakSettings>) {
  const file = (await paths($)).settings
  // Settings this pane does not show (the Arabic voice, for one) are kept as they are.
  let others: Record<string, unknown> = {}
  try {
    const raw: unknown = JSON.parse((await $.fs.read(file).catch(() => undefined)) ?? '{}')
    if (typeof raw === 'object' && raw !== null) others = raw as Record<string, unknown>
  } catch {
    // unreadable: rewritten from the pane's own settings
  }
  const next = { ...(await loadSettings($)), ...change }
  await $.fs.write(file, JSON.stringify({ ...others, ...next }, null, 2))
  await update($, prefs, () => next)
}

// One or two plain sentences for a long reply, or null when the model gives none.
async function summarize($: EngineInterface, answer: string): Promise<string | null> {
  const r = await $.model
    .complete({
      model: 'haiku',
      system:
        'Summarize the assistant reply below in one or two short sentences to be read aloud. ' +
        'Plain spoken words only: no markdown, code, file paths or links. Say what was done or found.',
      prompt: answer.slice(0, 12000),
      maxTokens: 150,
      timeoutMs: 15000,
    })
    .catch(() => null)

  return r?.isAnswered && r.text.trim() ? r.text.trim() : null
}

// Whether this session is muted on its own (the pane's "mute this session").
async function sessionMuted($: EngineInterface, conf: SpeakSettings): Promise<boolean> {
  return conf.mutedSessions.includes(await $.session.id())
}

// Whether auto-speak should talk now: not muted, and away from the terminal when that is asked.
async function shouldSpeak($: EngineInterface, conf: SpeakSettings): Promise<boolean> {
  if (isMuted(conf, await $.clock.now()) || (await sessionMuted($, conf))) {
    return false
  }

  return !conf.awayOnly || (await isAway($))
}

// Whether the terminal running this session is not the window in front. On macOS the terminal
// is named by its bundle id; elsewhere tts.py finds our own window among its parent processes.
async function isAway($: EngineInterface): Promise<boolean> {
  const terminal = (await $.env.get('__CFBundleIdentifier')) ?? ''
  const p = await paths($)

  return (await $.process.run([p.python, p.tts, '--check-away', terminal])).exitCode === 0
}

// Says what Claude is waiting on (a permission, a question, a plan), ahead of other speech,
// when not muted and the person is away from the terminal; at the terminal they can see it.
async function alert($: EngineInterface, sentences: string[]) {
  const conf = await loadSettings($)
  if (sentences.length === 0 || isMuted(conf, await $.clock.now()) || (await sessionMuted($, conf)) || !(await isAway($))) {
    return
  }
  await send($, { mode: 'reply', urgent: true, sentences, ref: null })
}

// Says a question ahead of other speech whenever replies would be spoken now.
async function ask($: EngineInterface, sentences: string[]) {
  if (sentences.length > 0 && (await shouldSpeak($, await loadSettings($)))) {
    await send($, { mode: 'reply', urgent: true, sentences, ref: null })
  }
}

// After a long turn, "All done." follows the reply, when the person is away.
async function sayDone($: EngineInterface, ref: string, after: number, durationMs: number) {
  if (durationMs < LONG_TURN_MS || !(await shouldSpeak($, await loadSettings($))) || !(await isAway($))) {
    return
  }
  await send($, { mode: 'reply', sentences: ['All done.'], pauses: [0.4], ref, offset: after, append: true })
}

// Reads a finished reply aloud, following the person's settings.
async function autoSpeak($: EngineInterface, reply: Reply, answer: string, durationMs: number) {
  const conf = await loadSettings($)
  if (!(await shouldSpeak($, conf))) {
    return
  }
  const texts = reply.sentences.map(s => s.text)
  if (conf.summarize && texts.join(' ').length > SUMMARY_OVER) {
    const summary = await summarize($, answer)
    const firstPara = reply.sentences.filter(s => s.para === reply.sentences[0]?.para).map(s => s.text)
    await send($, { mode: 'reply', sentences: summary ? [summary] : firstPara, ref: null })
    return
  }
  await send($, { mode: 'reply', sentences: texts, pauses: pausesFor(reply.sentences), ref: reply.id, offset: 0 })
  await sayDone($, reply.id, texts.length, durationMs)
}

// Sends the newly complete sentences of a streaming step, after what was sent before.
async function streamStep($: EngineInterface, live: LiveTurn, ref: string, text: string, isFinal: boolean, from: number) {
  const ready = completeSentences(text, isFinal)
  if (ready.length <= from || !(await live.speak)) {
    return from
  }
  const fresh = ready.slice(from).map(s => s.text)
  live.sent = live.sent.then(() =>
    send($, { mode: 'reply', sentences: fresh, pauses: pausesFor(ready, from), ref, offset: from, append: true }).catch(
      () => undefined,
    ),
  )

  return ready.length
}

// Mirrors the server's progress into state and the status line, only when it changes.
async function poll($: EngineInterface) {
  // The id is asked each time: compacting a conversation gives the session a new one.
  const next = parseStatus(await $.fs.read((await paths($)).status).catch(() => undefined), await $.session.id())
  const prev = await read($, playback)
  if (JSON.stringify(prev) !== JSON.stringify(next)) {
    await update($, playback, () => next)
    await $.ui.status(statusText(next))
  }
}

function statusText(p: Playback): string | undefined {
  if (p.state === 'idle') {
    return undefined
  }
  if (!p.mine) {
    return '♪ another session'
  }
  return p.state === 'paused' ? '❚❚ paused' : '♪ speaking'
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'speak',
      description: 'Show replies as sentences with play buttons',
    })
    await $.command.register({ name: 'hush', description: 'Stop the voice reading replies aloud' })
    await $.command.register({
      name: 'mute',
      description: 'Mute auto-speak: /mute, /mute 30m, /mute 2h, /mute until 3pm, /mute off',
      argumentHint: '[30m | 2h | until 3pm | off]',
    })
    void $.ui.open({ id: PANE, title: 'Speak' })
    void loadSettings($)
    $.clock.every(POLL_MS, () => void poll($))

    return next(e)
  })

  // Stops every session's speech from the prompt, whether or not the pane is open.
  on('command.run', { command: 'hush' }, async $ => {
    await send($, { mode: 'stop', all: true })

    return { text: 'Stopped speaking.' }
  })

  on('command.run', { command: 'mute' }, async ($, e) => {
    const now = await $.clock.now()
    const until = parseMute(e.args, now)
    if (until === null) {
      return { text: 'Try /mute, /mute 30m, /mute 2h, /mute until 3pm or /mute off.' }
    }
    if (until === 0) {
      await saveSettings($, { muted: false, mutedUntil: 0 })
      return { text: 'Auto-speak is on again.' }
    }
    await send($, { mode: 'stop', all: true })
    if (until === Infinity) {
      await saveSettings($, { muted: true, mutedUntil: 0 })
      return { text: 'Muted until you run /mute off.' }
    }
    await saveSettings($, { muted: false, mutedUntil: until })

    return { text: `Muted until ${untilText(until, now)}.` }
  })

  // Permission prompts, questions and plans are said aloud when the person is away.
  on('classic.PermissionRequest', async ($, e, next) => {
    if (e.tool_name !== 'AskUserQuestion' && e.tool_name !== 'ExitPlanMode') {
      void alert($, [permissionText(e.tool_name, e.tool_input)]).catch(() => undefined)
    }

    return next(e)
  })

  // A question is said like a reply (not only when away): it is what Claude is waiting on.
  on('tool.call', { tool: 'AskUserQuestion' }, async ($, e, next) => {
    if (!e.agentId) {
      void ask($, questionText(e)).catch(() => undefined)
    }

    return next(e)
  })

  on('tool.call', { tool: 'ExitPlanMode' }, async ($, e, next) => {
    if (!e.agentId) {
      void alert($, ['My plan is ready for your review.']).catch(() => undefined)
    }

    return next(e)
  })

  on('command.run', { command: 'speak' }, async $ => {
    await $.ui.open({ id: PANE, title: 'Speak' })

    return { text: 'Speak pane opened.' }
  })

  // Sending a message silences this session's speech, streaming turns included; the prompt
  // does not wait for it. Prompts the engine raises itself (notifications, triggers) leave it playing.
  on('prompt.submit', async ($, e, next) => {
    if (e.origin.kind !== 'task-notification' && e.origin.kind !== 'scheduled-trigger') {
      for (const live of liveTurns.values()) {
        live.speak = Promise.resolve(false)
      }
      void send($, { mode: 'stop' })
    }

    return next(e)
  })

  // Speaks the reply while it is typed: each finished sentence goes to the server as it arrives.
  on('turn.start', async ($, e, next) => {
    startedTurns.add(e.turnId)
    for (const old of [...startedTurns].slice(0, -STARTED_KEPT)) {
      startedTurns.delete(old)
    }

    return next(e)
  })

  // Leaving the session (/exit, ctrl+c, /clear, another resumed in its place) stops what it is
  // saying and drops its replies still waiting; other sessions go on.
  on('session.end', async ($, e, next) => {
    for (const live of liveTurns.values()) {
      live.speak = Promise.resolve(false)
    }
    await send($, { mode: 'stop' }).catch(() => undefined)

    return next(e)
  })

  on('turn.step', async function* ($, e, next) {
    if (e.agentId || !startedTurns.has(e.turnId)) {
      return yield* next(e)
    }
    let live = liveTurns.get(e.turnId)
    if (!live) {
      const conf = await loadSettings($)
      live = {
        speak: conf.live ? shouldSpeak($, conf) : Promise.resolve(false),
        lastStep: e.index,
        sent: Promise.resolve(),
        workUpdates: conf.workUpdates,
      }
      liveTurns.set(e.turnId, live)
      if (!conf.live) {
        return yield* next(e)
      }
    }
    live.lastStep = e.index
    const ref = `${e.turnId}:${e.index}`
    let text = ''
    let block = -1
    let sent = 0
    let usesTools = false
    for await (const chunk of next(e)) {
      yield chunk
      if (chunk.kind === 'tool') {
        usesTools = true // this step is work in progress, not the final reply
      } else if (chunk.kind === 'text') {
        text += (block !== -1 && chunk.index !== block ? '\n\n' : '') + chunk.text
        block = chunk.index
        if (live.workUpdates || (!usesTools && text.length >= FINAL_HINT_CHARS)) {
          sent = await streamStep($, live, ref, text, false, sent)
        }
      }
    }
    if (live.workUpdates || !usesTools) {
      await streamStep($, live, ref, text, true, sent)
    }
  })

  on('turn.complete', async ($, e, next) => {
    if (!e.agentId && e.answer) {
      const live = liveTurns.get(e.turnId)
      liveTurns.delete(e.turnId)
      const isLive = live !== undefined && (await loadSettings($)).live
      // A streamed reply's id names its step, so the server's progress lines up with it.
      const reply: Reply = {
        id: isLive ? `${e.turnId}:${live.lastStep}` : e.turnId,
        sentences: toSentences(e.answer),
        t: await $.clock.now(),
      }
      const all = await update($, replies, list => [...list, reply].slice(-MAX_REPLIES))
      void send($, { mode: 'record', reply }).catch(() => undefined) // for the VS Code extension's panel
      await update($, view, () => all.length - 1)
      await update($, top, () => 0)
      if (!startedTurns.has(e.turnId)) {
        // an earlier turn handed over as the session opened: listed, not spoken
      } else if (!isLive) {
        void autoSpeak($, reply, e.answer, e.durationMs)
      } else if (await live.speak) {
        void sayDone($, reply.id, reply.sentences.length, e.durationMs).catch(() => undefined)
      }
    } else {
      liveTurns.delete(e.turnId)
    }

    return next(e)
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button } = $.ui.resolve(e)
    // VS Code, the desktop app and mobile draw every Button as a native button: there the
    // sentence itself is plain text beside small buttons, and the pane scrolls on its own.
    const remote = e.surface !== 'terminal'
    // Replies stored in an older shape have no sentence list: skip them.
    const list: Reply[] = (await read($, replies)).filter(r => typeof r === 'object' && r !== null && Array.isArray(r.sentences))
    const at = Math.min(Math.max(0, await read($, view)), Math.max(0, list.length - 1))
    const reply = list[at]
    const sentences = reply?.sentences ?? []
    const now = await read($, playback)
    const conf = await read($, prefs)
    const width = Math.max(10, e.props.bodyColumns - 2) // inside the pane's one-column padding each side
    const rule = <Text dimColor>{'─'.repeat(width)}</Text>
    const live = reply && now.mine && now.ref === reply.id ? now.index : null

    const show = (i: number) => async () => {
      await update($, view, () => i)
      await update($, top, () => 0)
    }
    const play = (i: number) => async () => {
      await send($, { mode: 'play', sentences: [sentences[i]?.text ?? ''], ref: reply?.id ?? null, offset: i })
    }
    const playFrom = (i: number) => async () => {
      await send($, {
        mode: 'play',
        sentences: sentences.slice(i).map(s => s.text),
        pauses: [0, ...pausesFor(sentences, i + 1)],
        ref: reply?.id ?? null,
        offset: i,
      })
    }
    const skip = async () => {
      await send($, { mode: 'skip' })
    }
    const playAll = async () => {
      await send($, {
        mode: 'play',
        sentences: sentences.map(s => s.text),
        pauses: pausesFor(sentences),
        ref: reply?.id ?? null,
        offset: 0,
      })
    }
    const pause = async () => {
      await send($, { mode: 'pause' })
    }
    const stop = async () => {
      await send($, { mode: 'stop', all: true })
    }
    const rebuild = async () => {
      const p = await paths($)
      const { exitCode, stdout } = await $.process.run([p.python, p.history, await $.session.id()])
      if (exitCode !== 0) {
        return
      }
      const answers: unknown = JSON.parse(stdout)
      if (!Array.isArray(answers)) {
        return
      }
      // A reply already listed keeps its id, so the one being read stays highlighted and in view.
      const before = await read($, replies)
      const known = new Map(before.map(r => [r.sentences.map(s => s.text).join('\n'), r]))
      const rebuilt = answers
        .filter((a): a is string => typeof a === 'string')
        .map((a, i) => {
          const sentences = toSentences(a)
          const same = known.get(sentences.map(s => s.text).join('\n'))
          return { id: same?.id ?? `history-${i}`, sentences, ...(same?.t ? { t: same.t } : {}) }
        })
        .filter(r => r.sentences.length > 0)
        .slice(-MAX_REPLIES)
      const playing = await read($, playback)
      const reading = playing.mine ? before.find(r => r.id === playing.ref) : undefined
      if (reading && !rebuilt.some(r => r.id === reading.id)) {
        rebuilt.push(reading) // its text differs from the saved chat's: it stays, as the last
      }
      const live = reading ? rebuilt.findIndex(r => r.id === reading.id) : -1
      await update($, replies, () => rebuilt)
      await update($, view, () => (live >= 0 ? live : Math.max(0, rebuilt.length - 1)))
      await update($, top, () => 0)
    }
    const nextVoice = async () => {
      await saveSettings($, { voice: VOICES[(VOICES.indexOf(conf.voice) + 1) % VOICES.length] ?? 'af_heart' })
    }
    // The devices are listed each press: headphones come and go.
    const nextDevice = async () => {
      const p = await paths($)
      const { exitCode, stdout } = await $.process.run([p.python, p.tts, '--devices'], { timeoutMs: 15000 })
      let devices: string[] = []
      try {
        const parsed: unknown = JSON.parse(stdout)
        devices = Array.isArray(parsed) ? parsed.filter((d): d is string => typeof d === 'string') : []
      } catch {
        // none listed: only the default is offered
      }
      if (exitCode === 0) {
        await saveSettings($, { outputDevice: nextOutput(devices, conf.outputDevice) })
      }
    }
    const speed = (delta: number) => async () => {
      await saveSettings($, { speed: Math.round(Math.min(1.6, Math.max(0.7, conf.speed + delta)) * 10) / 10 })
    }
    const toggle = (key: 'awayOnly' | 'summarize' | 'live' | 'projectVoices' | 'workUpdates') => async () => {
      await saveSettings($, { [key]: !conf[key] })
    }
    const clock = await $.clock.now()
    const muted = isMuted(conf, clock)
    const toggleMute = async () => {
      await saveSettings($, muted ? { muted: false, mutedUntil: 0 } : { muted: true })
    }
    const muteLabel = !muted ? '♪ auto-speak' : conf.muted ? '✕ muted' : `✕ muted until ${untilText(conf.mutedUntil, clock)}`
    const me = await $.session.id()
    const thisMuted = conf.mutedSessions.includes(me)
    const toggleSessionMute = async () => {
      await saveSettings($, { mutedSessions: withSessionMuted(conf.mutedSessions, me, !thisMuted) })
      if (!thisMuted) {
        for (const live of liveTurns.values()) {
          live.speak = Promise.resolve(false)
        }
        await send($, { mode: 'stop' })
      }
    }

    const panel = await read($, panelAtom)
    const showPanel = (p: Panel) => async () => {
      await update($, panelAtom, current => (current === p ? 'list' : p))
    }

    // Everything the settings hold, shown when ⚙ is open, one labelled row per kind; folded, one
    // summary row stands in. No hotkeys here: a hotkey draws its letter beside the control.
    const check = (checked: boolean, label: string) => (
      <Text dimColor={!checked}>
        <Text color={checked ? 'green' : undefined}>{checked ? '●' : '○'}</Text> {label}
      </Text>
    )
    const settingRow = (label: string, children: RenderChildren) => (
      <Box key={`set${label}`} flexDirection="row">
        <Box width={9} flexShrink={0}>
          <Text dimColor>{label}</Text>
        </Box>
        <Box flexDirection="row" flexWrap="wrap" columnGap={3} flexShrink={1}>
          {children}
        </Box>
      </Box>
    )
    const settingsRows = (
      <Box flexDirection="column" marginBottom={1}>
        <Text bold>Settings</Text>
        {settingRow('Voice', [
          <Button key="voice" plain onPress={nextVoice}>
            <Text color="cyan">{conf.projectVoices ? 'per project' : conf.voice}</Text>
            <Text dimColor> ›</Text>
          </Button>,
          <Box key="speedBox" flexDirection="row">
            <Text dimColor>speed </Text>
            <Button key="slower" plain onPress={speed(-0.1)}>−</Button>
            <Text color="cyan"> {conf.speed.toFixed(1)}× </Text>
            <Button key="faster" plain onPress={speed(0.1)}>+</Button>
          </Box>,
        ])}
        {settingRow('Output', [
          <Button key="output" plain onPress={nextDevice}>
            <Text color="cyan">{conf.outputDevice || 'system default'}</Text>
            <Text dimColor> ›</Text>
          </Button>,
        ])}
        {settingRow('Speak', [
          <Button key="live" plain onPress={toggle('live')}>{check(conf.live, 'while typing')}</Button>,
          <Button key="workUpdates" plain onPress={toggle('workUpdates')}>{check(conf.workUpdates, 'work updates')}</Button>,
          <Button key="away" plain onPress={toggle('awayOnly')}>{check(conf.awayOnly, 'only when away')}</Button>,
          <Button key="summary" plain onPress={toggle('summarize')}>
            {check(conf.summarize, conf.live ? 'summarize long (when not live)' : 'summarize long')}
          </Button>,
          <Button key="projectVoices" plain onPress={toggle('projectVoices')}>{check(conf.projectVoices, 'own voice per project')}</Button>,
        ])}
        {settingRow('Mute', [
          <Button key="mute" plain onPress={toggleMute}>
            <Text color={muted ? 'red' : 'green'}>{muteLabel}</Text>
          </Button>,
          <Button key="muteSession" plain onPress={toggleSessionMute}>{check(thisMuted, 'this session only')}</Button>,
        ])}
        {reply &&
          settingRow('History', [
            <Button key="rebuild" plain onPress={rebuild}>
              <Text color="cyan">⟳ load this chat's replies</Text>
            </Button>,
          ])}
      </Box>
    )
    const summaryText = [
      conf.projectVoices ? 'voice per project' : conf.voice,
      `${conf.speed.toFixed(1)}×`,
      conf.outputDevice || 'system default',
      muted ? 'muted' : thisMuted ? 'session muted' : conf.awayOnly ? 'when away' : 'auto-speak',
    ].join(' · ')
    const settingsButton = (
      <Button key="settings" hotkey="c" onPress={showPanel('settings')}>{panel === 'settings' ? '⚙ done' : '⚙'}</Button>
    )
    const helpButton = <Button key="help" hotkey="h" onPress={showPanel('help')}>{panel === 'help' ? '? done' : '?'}</Button>
    const footer = (
      <Box flexDirection="column" flexShrink={0}>
        {panel === 'settings' && settingsRows}
        <Box flexDirection="row" columnGap={1} justifyContent="space-between">
          <Box flexDirection="row" columnGap={1} flexShrink={1}>
            {settingsButton}
            {panel !== 'settings' && <Text dimColor wrap="truncate-end">{summaryText}</Text>}
          </Box>
          {helpButton}
        </Box>
      </Box>
    )
    // Key help in the settings' shape: a dim label column, then each key and what it does.
    const key = (k: string, what: string) => (
      <Text key={`key${k}${what}`}>
        <Text color="cyan" bold>{k}</Text>
        <Text dimColor> {what}</Text>
      </Text>
    )
    const helpList = (
      <Box flexDirection="column">
        <Text bold>Keys</Text>
        {settingRow('Play', [key('p', 'play all'), key('a', 'pause'), key('x', 'skip'), key('s', 'stop'), key('click', 'read from a line')])}
        {settingRow('Move', [key('b', 'previous reply'), key('n', 'next reply'), key('k', 'scroll up'), key('j', 'scroll down')])}
        {settingRow('Anywhere', [key('ctrl+x s', 'stop'), key('/hush', 'stop every session'), key('/mute 30m', 'mute a while'), key('/mute off', 'unmute')])}
        {settingRow('Pane', [key('c', 'settings'), key('h', 'this help')])}
        <Box marginTop={1}>
          <Text dimColor>Sending a message stops the reply. Voice typing pauses it.</Text>
        </Box>
      </Box>
    )

    const height = Math.max(8, e.props.scroll.bodyRows)

    if (!reply) {
      return (
        <Box flexDirection="column" paddingX={1} height={height}>
          <Box flexDirection="column" flexGrow={1} flexShrink={1} overflow="hidden">
            {panel === 'help' ? helpList : (
              <Box flexDirection="column">
                <Text bold>Nothing to play yet</Text>
                <Text dimColor>Claude's replies show up here as they arrive.</Text>
                <Box marginTop={1}>
                  <Button key="rebuild" hotkey="r" onPress={rebuild}>⟳ Load chat history</Button>
                </Box>
              </Box>
            )}
          </Box>
          {rule}
          {footer}
        </Box>
      )
    }

    const isLatest = at === list.length - 1
    const playing = now.state !== 'idle' && now.mine
    const status = !now.mine && now.state !== 'idle'
      ? { text: '♪ another session', color: 'yellow' }
      : playing
        ? { text: `${now.state === 'paused' ? '❚❚' : '♪'} ${live !== null ? `${live + 1}/${sentences.length}` : ''}`.trim(), color: 'green' }
        : muted
          ? { text: conf.muted ? '✕ muted' : `✕ muted until ${untilText(conf.mutedUntil, clock)}`, color: 'red' }
          : thisMuted
            ? { text: '✕ session muted', color: 'red' }
            : null
    const details = [`Reply ${at + 1}/${list.length}`, reply.t ? ago(reply.t, clock) : null, `${sentences.length} lines`]
      .filter(Boolean)
      .join(' · ')
    const bar = live !== null ? progress(live, sentences.length, width) : null
    const barLine = bar ? (
      <Text>
        <Text color="green">{bar[0]}</Text>
        <Text dimColor>{bar[1]}</Text>
      </Text>
    ) : (
      rule
    )
    // How a line reads: the one being said highlighted, those already said dim, headings bold,
    // the stand-ins for code and tables dim and slanted.
    const lineStyle = (i: number, text: string) => ({
      color: live === i ? 'green' : undefined,
      bold: live === i || isLead(sentences, i),
      dimColor: live !== i && ((live !== null && i < live) || isPlaceholder(text)),
      italic: isPlaceholder(text),
    })

    const header = (
      <Box flexDirection="column" flexShrink={0}>
        <Box flexDirection="row" justifyContent="space-between">
          <Box flexDirection="row" columnGap={1} flexShrink={1}>
            <Button key="prev" hotkey="b" dimColor={at === 0} onPress={show(Math.max(0, at - 1))}>◀</Button>
            <Text bold wrap="truncate-end">{details}</Text>
            <Button key="next" hotkey="n" dimColor={isLatest} onPress={show(Math.min(list.length - 1, at + 1))}>▶</Button>
            {!isLatest && <Button key="latest" plain dimColor onPress={show(list.length - 1)}>latest</Button>}
          </Box>
          {status && <Text color={status.color}>{status.text}</Text>}
        </Box>
        <Box flexDirection="row" flexWrap="wrap" columnGap={remote ? 1 : 2} marginTop={remote ? 1 : 0}>
          <Button key="all" variant="primary" hotkey="p" onPress={playAll}>▶ Play all</Button>
          <Button key="pause" hotkey="a" onPress={pause}>{now.state === 'paused' ? '▶ Resume' : '❚❚ Pause'}</Button>
          <Button key="skip" hotkey="x" onPress={skip}>⏭ Skip</Button>
          <Button key="stop" hotkey="s" action={STOP_ACTION} onPress={stop}>■ Stop</Button>
        </Box>
        {barLine}
      </Box>
    )

    // Header and footer keep their rows; the line list takes what is left and scrolls itself.
    const listWidth = width - 2 // the ♪ column
    const rowsFor = (chars: number) => Math.ceil(chars / Math.max(1, width))
    const headerRows = 2 + rowsFor(50)
    const footerRows = 2 + (panel === 'settings' ? 6 + rowsFor(110) + (reply ? 1 : 0) + 1 : 0)
    const listRows = Math.max(1, height - headerRows - footerRows)
    const start = windowStart(sentences, await read($, top), live, listRows, listWidth)
    const shown = fitting(sentences, start, listRows, listWidth)
    const scrollTo = (i: number) => async () => {
      await update($, top, () => Math.min(Math.max(0, i), Math.max(0, sentences.length - 1)))
    }
    const page = Math.max(1, shown - 1)

    if (remote) {
      // Native buttons here: small ▶ (this line) and » (from here) beside plain text. The row
      // estimate is in terminal rows, and these surfaces fit far more, so several times as many
      // are drawn and the list's box clips what does not fit.
      const remotePage = Math.max(1, shown * 2 - 2) // about a screenful there, never past what was seen
      const drawn = sentences.slice(start, start + shown * REMOTE_ROWS + 4)
      const rows = drawn.map((s, k) => {
        const i = start + k
        return (
          <Box key={`line${i}`} flexDirection="row" columnGap={1} marginTop={k > 0 && sentences[i - 1]?.para !== s.para ? 1 : 0}>
            <Box flexDirection="row" columnGap={1} flexShrink={0}>
              <Button key={`p${i}`} onPress={play(i)}>{live === i ? '♪' : '▶'}</Button>
              <Button key={`f${i}`} onPress={playFrom(i)}>»</Button>
            </Box>
            <Text {...lineStyle(i, s.text)}>{s.text}</Text>
          </Box>
        )
      })

      return (
        <Box flexDirection="column" paddingX={1} height={height}>
          {header}
          <Box flexDirection="column" flexGrow={1} flexShrink={1} overflow="hidden" marginY={1}>
            {panel === 'help' ? helpList : rows}
          </Box>
          <Box flexDirection="column" flexShrink={0} rowGap={1}>
            <Box flexDirection="row" columnGap={1} alignItems="center">
              <Button key="up" onPress={scrollTo(start - remotePage)}>▲</Button>
              <Button key="down" onPress={scrollTo(start + remotePage)}>▼</Button>
              <Text dimColor>from {start + 1} of {sentences.length}</Text>
            </Box>
            {footer}
          </Box>
        </Box>
      )
    }

    const lines = sentences.slice(start, start + shown + 2).map((s, k) => {
      // What fits by estimate, plus a little more that the clip hides when the estimate is low.
      const i = start + k
      return (
        <Box key={`row${i}`} flexDirection="row" marginTop={k > 0 && sentences[i - 1]?.para !== s.para ? 1 : 0} flexShrink={0}>
          <Box width={2} flexShrink={0}>
            <Text color="green">{live === i ? '♪' : ' '}</Text>
          </Box>
          {/* Directly in the keyed row, as the pointer's own highlight expects. */}
          <Box width={listWidth} flexShrink={0}>
            <Button key={`s${i}`} plain onPress={playFrom(i)}>
              <Text {...lineStyle(i, s.text)} wrap="wrap" hover={{ scope: `row${i}`, inverse: true }}>
                {s.text}
              </Text>
            </Button>
          </Box>
        </Box>
      )
    })

    return (
      <Box flexDirection="column" paddingX={1} height={height}>
        {header}
        <Box flexDirection="column" flexGrow={1} flexShrink={1} overflow="hidden">
          {panel === 'help' ? helpList : lines}
        </Box>
        <Box flexDirection="column" flexShrink={0}>
          {panel !== 'help' && (
            <Box flexDirection="row" columnGap={1}>
              <Button key="up" hotkey="k" dimColor={start === 0} onPress={scrollTo(start - page)}>▲</Button>
              <Button key="down" hotkey="j" dimColor={start + shown >= sentences.length} onPress={scrollTo(start + page)}>▼</Button>
              <Text dimColor>
                {start + 1}–{Math.min(sentences.length, start + shown)} of {sentences.length}
              </Text>
            </Box>
          )}
          {footer}
        </Box>
      </Box>
    )
  })
}
