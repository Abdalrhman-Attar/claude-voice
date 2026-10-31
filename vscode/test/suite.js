// Runs inside VS Code: drives the real extension against the real voice server and files.
const assert = require('assert')
const fs = require('fs')
const os = require('os')
const path = require('path')
const vscode = require('vscode')

const home = process.env.KOKORO_HOME || path.join(os.homedir(), '.claude', 'kokoro')
const status = () => JSON.parse(fs.readFileSync(path.join(home, 'status.json'), 'utf8'))
const settingsFile = path.join(home, 'settings.json')

async function until(what, check, ms = 15000) {
  const end = Date.now() + ms
  while (Date.now() < end) {
    try {
      if (await check()) return
    } catch {}
    await new Promise(r => setTimeout(r, 150))
  }
  throw new Error(`timed out waiting for: ${what}`)
}

exports.run = async function () {
  const results = []
  const step = async (name, fn) => {
    try {
      await fn()
      results.push(`PASS ${name}`)
    } catch (err) {
      results.push(`FAIL ${name}: ${err.message}`)
    }
  }
  const savedSettings = fs.readFileSync(settingsFile, 'utf8')

  const ext = vscode.extensions.getExtension('abdalrhman-attar.claude-voice')
  const api = await ext.activate()

  await step('reads the reply feed', () => until('replies loaded', () => api.replies() > 0))
  await step('registers its commands', async () => {
    const all = await vscode.commands.getCommands(true)
    for (const c of ['playLatest', 'pause', 'skip', 'stop', 'toggleMute', 'show', 'setup']) {
      assert.ok(all.includes(`claudeVoice.${c}`), `missing claudeVoice.${c}`)
    }
  })
  await step('shows the status bar item', () => assert.match(api.statusText(), /\$\((unmute|mute)\)/))
  await step('opens the Replies panel and its script runs', async () => {
    await vscode.commands.executeCommand('claudeVoice.show')
    await until('panel ready', () => api.panelReady())
  })
  await step('plays the latest reply', async () => {
    await vscode.commands.executeCommand('claudeVoice.playLatest')
    await until('server playing', () => status().state === 'playing', 20000)
    await until('status bar says speaking', () => /Speaking/.test(api.statusText()))
  })
  await step('pauses', async () => {
    await vscode.commands.executeCommand('claudeVoice.pause')
    await until('server paused', () => status().state === 'paused')
    await until('status bar says paused', () => /Paused/.test(api.statusText()))
  })
  await step('stops', async () => {
    await vscode.commands.executeCommand('claudeVoice.stop')
    await until('server idle', () => status().state === 'idle')
  })
  await step('clicking a sentence reads from it', async () => {
    const r = api.latest()
    await api.panelMessage({ type: 'play', id: r.id, session: r.session, index: 2 })
    await until('playing sentence 3', () => status().state === 'playing' && status().ref === r.id && status().index === 2, 20000)
    await until('status bar shows 3/', () => /Speaking 3\//.test(api.statusText()))
  })
  await step('the ▶ on a sentence reads only that one', async () => {
    const r = api.latest()
    await api.panelMessage({ type: 'playOne', id: r.id, session: r.session, index: 4 })
    await until('playing sentence 5', () => status().index === 4, 20000)
    await until('then idle (one sentence only)', () => status().state === 'idle', 30000)
  })
  await step('skip moves to the next sentence', async () => {
    const r = api.latest()
    await api.panelMessage({ type: 'play', id: r.id, session: r.session, index: 0 })
    await until('playing sentence 1', () => status().index === 0 && status().state === 'playing', 20000)
    await api.panelMessage({ type: 'skip' })
    await until('sentence 2', () => status().index === 1, 20000)
    await api.panelMessage({ type: 'stop' })
    await until('idle', () => status().state === 'idle')
  })
  await step('panel settings are written to the shared file', async () => {
    await api.panelMessage({ type: 'setting', key: 'speed', value: 1.3 })
    assert.strictEqual(JSON.parse(fs.readFileSync(settingsFile, 'utf8')).speed, 1.3)
    await api.panelMessage({ type: 'setting', key: 'workUpdates', value: false })
    assert.strictEqual(JSON.parse(fs.readFileSync(settingsFile, 'utf8')).workUpdates, false)
  })
  await step('mute toggles the shared settings', async () => {
    const before = JSON.parse(fs.readFileSync(settingsFile, 'utf8')).muted === true
    await vscode.commands.executeCommand('claudeVoice.toggleMute')
    assert.strictEqual(JSON.parse(fs.readFileSync(settingsFile, 'utf8')).muted === true, !before)
  })

  fs.writeFileSync(settingsFile, savedSettings)
  fs.writeFileSync(path.join(__dirname, 'results.txt'), results.join('\n') + '\n')
  console.log(results.join('\n'))
  if (results.some(r => r.startsWith('FAIL'))) throw new Error('some checks failed')
}
