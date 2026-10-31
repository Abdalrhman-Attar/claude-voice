// Read Aloud for Claude Code (VS Code): a panel of recent replies from every agent (Claude Code, Codex,
// Gemini, Cursor), read aloud by the local Kokoro server, with a sentence-level player.
//
// The voice server records replies in replies.json and its progress in status.json, both in the
// voice's home folder; this extension watches those, and talks back through server/tts.py.
import { spawn } from 'child_process'
import * as fs from 'fs'
import * as os from 'os'
import * as path from 'path'
import * as vscode from 'vscode'

type Sentence = { text: string; para: number; line?: number }
type Reply = { id: string; session: string; label: string; cwd?: string; title?: string; agent: string; t: number; sentences: Sentence[] }
type Status = { session?: string; ref?: string | null; index?: number | null; state?: string }
type Settings = Record<string, unknown>

const PARAGRAPH_PAUSE = 0.5
const LINE_PAUSE = 0.25
const POLL_MS = 300
const PING_MS = 4 * 60 * 1000 // keeps the voice server (and its Claude Code session reader) running

function home(): string {
  const configured = vscode.workspace.getConfiguration('claudeVoice').get<string>('home')
  return configured || process.env.KOKORO_HOME || path.join(os.homedir(), '.claude', 'kokoro')
}

function python(): string {
  return process.platform === 'win32'
    ? path.join(home(), '.venv', 'Scripts', 'python.exe')
    : path.join(home(), '.venv', 'bin', 'python')
}

function onlyThisWorkspace(): boolean {
  return vscode.workspace.getConfiguration('claudeVoice').get<boolean>('onlyThisWorkspace', true)
}

// Whether the Claude Code plugin is installed and on (it speaks while Claude types).
function pluginInstalled(): boolean {
  const enabled = readJson<{ enabledPlugins?: Record<string, boolean> }>(path.join(os.homedir(), '.claude', 'settings.json'), {}).enabledPlugins ?? {}
  return Object.entries(enabled).some(([id, on]) => id.startsWith('speak-pane@') && on)
}

// The server code to run: the stable copy setup makes in the voice's home, else the bundled one.
function serverDir(bundled: string): string {
  const app = path.join(home(), 'app')
  return fs.existsSync(path.join(app, 'tts.py')) ? app : bundled
}

// Keeps the stable copy current after an extension update; true when a file changed.
function syncApp(bundled: string): boolean {
  const app = path.join(home(), 'app')
  if (!fs.existsSync(app)) return false
  let changed = false
  for (const f of fs.readdirSync(bundled)) {
    if (!/\.(py|json|txt)$/.test(f)) continue
    const from = path.join(bundled, f)
    const to = path.join(app, f)
    try {
      if (!fs.existsSync(to) || fs.readFileSync(to, 'utf8') !== fs.readFileSync(from, 'utf8')) {
        fs.copyFileSync(from, to)
        changed = true
      }
    } catch {
      // a file in use: the next start copies it
    }
  }
  return changed
}

type SetupState = { running: boolean; step: number; detail: string; log: string[]; ok?: boolean; error?: string }

function readJson<T>(file: string, fallback: T): T {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8')) as T
  } catch {
    return fallback
  }
}

// Seconds of quiet before each sentence from `from` on, as the Claude Code panel pauses.
function pausesFor(sentences: Sentence[], from: number): number[] {
  return sentences.slice(from).map((s, k) => {
    const prev = sentences[from + k - 1]
    if (k === 0 || !prev) return 0
    if (prev.para !== s.para) return PARAGRAPH_PAUSE
    return prev.line !== undefined && prev.line !== s.line ? LINE_PAUSE : 0
  })
}

class Voice {
  constructor(private readonly bundled: string) {}

  private get server() {
    return serverDir(this.bundled)
  }

  ready(): boolean {
    return fs.existsSync(python())
  }

  // One request to the voice server through tts.py, which starts the server when needed.
  send(request: Record<string, unknown>): Promise<void> {
    return new Promise(resolve => {
      if (!this.ready()) {
        void vscode.window
          .showWarningMessage('The Read Aloud voice is not set up yet.', 'Set up')
          .then(pick => pick && vscode.commands.executeCommand('claudeVoice.setup'))
        return resolve()
      }
      const child = spawn(python(), [path.join(this.server, 'tts.py'), '--json'], {
        env: { ...process.env, KOKORO_HOME: home() },
        windowsHide: true,
      })
      child.on('error', () => resolve())
      child.on('exit', () => resolve())
      child.stdin.end(JSON.stringify(request))
    })
  }

  play(reply: Reply, index: number, onlyThis: boolean) {
    const sentences = onlyThis ? reply.sentences.slice(index, index + 1) : reply.sentences.slice(index)
    return this.send({
      mode: 'play',
      session: reply.session,
      label: reply.label,
      sentences: sentences.map(s => s.text),
      pauses: onlyThis ? [0] : pausesFor(reply.sentences, index),
      ref: reply.id,
      offset: index,
    })
  }
}

// What the panel and the status bar show, read from the voice's files.
class Store {
  replies: Reply[] = []
  status: Status = {}
  settings: Settings = {}
  private mtimes = new Map<string, number>()
  private listeners: (() => void)[] = []

  onChange(fn: () => void) {
    this.listeners.push(fn)
  }

  // Re-reads whichever file changed since the last look.
  poll() {
    let changed = false
    for (const [name, apply] of [
      ['replies.json', (v: unknown) => (this.replies = Array.isArray(v) ? (v as Reply[]) : [])],
      ['status.json', (v: unknown) => (this.status = (v as Status) ?? {})],
      ['settings.json', (v: unknown) => (this.settings = (v as Settings) ?? {})],
    ] as const) {
      const file = path.join(home(), name)
      let mtime = 0
      try {
        mtime = fs.statSync(file).mtimeMs
      } catch {
        // not there yet
      }
      if (this.mtimes.get(name) !== mtime) {
        this.mtimes.set(name, mtime)
        apply(readJson<unknown>(file, null))
        changed = true
      }
    }
    if (changed) this.listeners.forEach(fn => fn())
  }

  // Replies from sessions working in one of this window's folders (or a folder inside one), unless
  // every session is asked for. Replies recorded before folders were kept match by project name.
  visibleReplies(): Reply[] {
    const folders = vscode.workspace.workspaceFolders ?? []
    if (!onlyThisWorkspace() || folders.length === 0) return this.replies
    const inside = (cwd: string, root: string) => cwd === root || cwd.startsWith(root.endsWith(path.sep) ? root : root + path.sep)
    return this.replies.filter(r =>
      r.cwd
        ? folders.some(f => inside(path.normalize(r.cwd ?? ''), path.normalize(f.uri.fsPath)))
        : folders.some(f => r.label === f.name || r.label.endsWith(` in ${f.name}`)),
    )
  }

  saveSettings(change: Settings) {
    const file = path.join(home(), 'settings.json')
    const next = { ...readJson<Settings>(file, {}), ...change }
    fs.mkdirSync(home(), { recursive: true })
    fs.writeFileSync(file, JSON.stringify(next, null, 2))
    this.poll()
  }

  muted(): boolean {
    return this.settings.muted === true || (typeof this.settings.mutedUntil === 'number' && this.settings.mutedUntil > Date.now())
  }
}

class RepliesView implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView
  panelReady = false // the panel's script ran and asked for state
  setup: SetupState = { running: false, step: 0, detail: '', log: [] }
  onSetup?: () => void
  onPlugin?: () => void

  constructor(
    private readonly extension: vscode.Uri,
    private readonly store: Store,
    private readonly voice: Voice,
  ) {
    store.onChange(() => this.push())
  }

  resolveWebviewView(view: vscode.WebviewView) {
    this.view = view
    const media = vscode.Uri.joinPath(this.extension, 'media')
    view.webview.options = { enableScripts: true, localResourceRoots: [media] }
    const nonce = Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2)
    const uri = (f: string) => view.webview.asWebviewUri(vscode.Uri.joinPath(media, f))
    view.webview.html = fs
      .readFileSync(path.join(media.fsPath, 'panel.html'), 'utf8')
      .replace(/\{\{csp\}\}/g, view.webview.cspSource)
      .replace(/\{\{nonce\}\}/g, nonce)
      .replace('{{codicons}}', uri('codicons/codicon.css').toString())
      .replace('{{css}}', uri('panel.css').toString())
      .replace('{{js}}', uri('panel.js').toString())
    view.webview.onDidReceiveMessage(msg => this.receive(msg))
    view.onDidChangeVisibility(() => this.push())
  }

  push() {
    void this.view?.webview.postMessage({
      type: 'state',
      replies: this.store.visibleReplies().slice(-100),
      status: this.store.status,
      settings: this.store.settings,
      muted: this.store.muted(),
      allSessions: !onlyThisWorkspace(),
      folder: (vscode.workspace.workspaceFolders ?? []).map(f => f.name).join(', '),
      ready: this.voice.ready(),
      plugin: pluginInstalled(),
      setup: this.setup,
    })
  }

  receive(msg: { type: string; id?: string; session?: string; index?: number; key?: string; value?: unknown }) {
    const reply = this.store.replies.find(r => r.id === msg.id && r.session === msg.session)
    switch (msg.type) {
      case 'ready':
        this.panelReady = true
        return this.push()
      case 'play':
        return reply && this.voice.play(reply, msg.index ?? 0, false)
      case 'playOne':
        return reply && this.voice.play(reply, msg.index ?? 0, true)
      case 'pause':
      case 'skip':
        return this.voice.send({ mode: msg.type })
      case 'stop':
        return this.voice.send({ mode: 'stop', all: true })
      case 'setting':
        if (msg.key === 'muted') {
          return this.store.saveSettings({ muted: msg.value === true, mutedUntil: 0 })
        }
        return msg.key && this.store.saveSettings({ [msg.key]: msg.value })
      case 'allSessions':
        return vscode.workspace
          .getConfiguration('claudeVoice')
          .update('onlyThisWorkspace', msg.value !== true, vscode.ConfigurationTarget.Global)
      case 'setup':
        return this.onSetup?.()
      case 'installPlugin':
        return this.onPlugin?.()
      case 'test':
        return this.voice.send({ mode: 'play', session: 'read-aloud-test', label: '', sentences: ['Read Aloud is ready. Replies from Claude Code will sound like this.'] })
      case 'terminalSetup':
        return vscode.commands.executeCommand('claudeVoice.setupInTerminal')
    }
  }
}

function statusText(store: Store): { text: string; tooltip: string } {
  const s = store.status
  const reply = store.replies.find(r => r.id === s.ref && r.session === s.session)
  const where = reply ? ` · ${reply.label}` : ''
  const of = reply && typeof s.index === 'number' ? ` ${s.index + 1}/${reply.sentences.length}` : ''
  if (s.state === 'playing') return { text: `$(unmute) Speaking${of}`, tooltip: `Read Aloud: speaking${where}. Click to stop.` }
  if (s.state === 'paused') return { text: `$(debug-pause) Paused${of}`, tooltip: `Read Aloud: paused${where}. Click to stop.` }
  if (store.muted()) return { text: '$(mute)', tooltip: 'Read Aloud: auto-speak is muted. Click to show replies.' }
  return { text: '$(unmute)', tooltip: 'Read Aloud: click to show replies.' }
}

// What the extension exposes to its integration tests.
export type Api = {
  panelReady: () => boolean
  statusText: () => string
  replies: () => number
  visible: () => number
  latest: (minSentences?: number) => Reply | undefined
  panelMessage: (msg: { type: string; id?: string; session?: string; index?: number; key?: string; value?: unknown }) => unknown
}

export function activate(context: vscode.ExtensionContext): Api {
  const server = path.join(context.extensionPath, 'bundle', 'server')
  const voice = new Voice(server)
  const store = new Store()
  const view = new RepliesView(context.extensionUri, store, voice)

  const bar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 90)
  const paint = () => {
    const { text, tooltip } = statusText(store)
    bar.text = text
    bar.tooltip = tooltip
    bar.command = store.status.state === 'playing' || store.status.state === 'paused' ? 'claudeVoice.stop' : 'claudeVoice.show'
    bar.show()
  }
  store.onChange(paint)

  const timer = setInterval(() => store.poll(), POLL_MS)
  store.poll()
  paint()

  // An updated server takes over at once: the old one quits and the first ping starts the new one.
  const updated = syncApp(server)
  // Setup with progress in the panel: the bundled installer, its output read for the step it is on.
  const STEPS: [RegExp, number][] = [
    [/Python|uv/i, 1],
    [/Installing the voice's Python packages/, 2],
    [/Downloading|% of/, 3],
    [/Login:|Shortcut:|No systemd/, 4],
    [/Claude Code plugin/, 5],
  ]
  const runSetup = () => {
    if (view.setup.running) return
    const bundle = path.join(context.extensionPath, 'bundle')
    const [cmd, args] =
      process.platform === 'win32'
        ? ['powershell', ['-ExecutionPolicy', 'Bypass', '-File', path.join(bundle, 'install.ps1')]]
        : ['bash', [path.join(bundle, 'install.sh')]]
    view.setup = { running: true, step: 1, detail: 'Starting', log: [] }
    view.push()
    const child = spawn(cmd, args, { cwd: bundle, env: { ...process.env, KOKORO_HOME: home() }, windowsHide: true })
    const take = (chunk: Buffer) => {
      for (const raw of chunk.toString().split(/[\r\n]+/)) {
        const line = raw.trim()
        if (!line) continue
        view.setup.log = [...view.setup.log, line].slice(-200)
        view.setup.detail = line
        for (const [pattern, step] of STEPS) if (pattern.test(line)) view.setup.step = Math.max(view.setup.step, step)
      }
      view.push()
    }
    child.stdout.on('data', take)
    child.stderr.on('data', take)
    child.on('error', err => {
      view.setup = { ...view.setup, running: false, ok: false, error: String(err) }
      view.push()
    })
    child.on('exit', code => {
      view.setup = { ...view.setup, running: false, ok: code === 0, step: code === 0 ? 6 : view.setup.step, error: code === 0 ? undefined : `Setup stopped (exit ${code}).` }
      store.poll()
      view.push()
      if (code === 0) void voice.send({ mode: 'ping' })
    })
  }
  view.onSetup = runSetup
  view.onPlugin = () => {
    const helper = path.join(serverDir(server), 'install_helper.py')
    const child = spawn(python(), [helper, 'plugin'], { env: { ...process.env, KOKORO_HOME: home() }, windowsHide: true })
    let out = ''
    child.stdout.on('data', d => (out += d))
    child.stderr.on('data', d => (out += d))
    child.on('exit', code => {
      void vscode.window.showInformationMessage(code === 0 ? 'Claude Code plugin installed. Run /reload-plugins in open Claude Code sessions.' : out.trim().split('\n').slice(-2).join(' '))
      view.push()
    })
  }

  // Keeps the server running while VS Code is open, so finished replies are read even without the plugin.
  const ping = () => voice.ready() && void voice.send({ mode: 'ping' })
  if (updated && voice.ready()) void voice.send({ mode: 'quit' }).then(() => setTimeout(ping, 1500))
  else ping()
  const pinger = setInterval(ping, PING_MS)

  const latest = () => store.visibleReplies().at(-1)
  context.subscriptions.push(
    bar,
    { dispose: () => clearInterval(timer) },
    { dispose: () => clearInterval(pinger) },
    vscode.window.registerWebviewViewProvider('claudeVoice.replies', view, { webviewOptions: { retainContextWhenHidden: true } }),
    vscode.workspace.onDidChangeConfiguration(e => e.affectsConfiguration('claudeVoice') && view.push()),
    vscode.commands.registerCommand('claudeVoice.show', () => vscode.commands.executeCommand('claudeVoice.replies.focus')),
    vscode.commands.registerCommand('claudeVoice.playLatest', () => {
      const reply = latest()
      return reply && voice.play(reply, 0, false)
    }),
    vscode.commands.registerCommand('claudeVoice.pause', () => voice.send({ mode: 'pause' })),
    vscode.commands.registerCommand('claudeVoice.skip', () => voice.send({ mode: 'skip' })),
    vscode.commands.registerCommand('claudeVoice.stop', () => voice.send({ mode: 'stop', all: true })),
    vscode.commands.registerCommand('claudeVoice.toggleMute', () =>
      store.saveSettings(store.muted() ? { muted: false, mutedUntil: 0 } : { muted: true }),
    ),
    vscode.commands.registerCommand('claudeVoice.setup', async () => {
      await vscode.commands.executeCommand('claudeVoice.replies.focus')
      runSetup()
    }),
    vscode.commands.registerCommand('claudeVoice.setupInTerminal', () => {
      const bundle = path.join(context.extensionPath, 'bundle')
      const terminal = vscode.window.createTerminal({ name: 'Read Aloud setup', cwd: bundle })
      terminal.show()
      terminal.sendText(
        process.platform === 'win32' ? 'powershell -ExecutionPolicy Bypass -File .\\install.ps1' : 'bash ./install.sh',
      )
    }),
  )

  return {
    panelReady: () => view.panelReady,
    statusText: () => bar.text,
    replies: () => store.replies.length,
    visible: () => store.visibleReplies().length,
    latest: (min = 0) => store.visibleReplies().filter(r => r.sentences.length >= min).at(-1),
    panelMessage: msg => view.receive(msg),
  }
}

export function deactivate() {}
