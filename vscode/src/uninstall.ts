// The `vscode:uninstall` hook. VS Code runs it once the extension is removed for good, at the first
// start after uninstalling it, and it undoes what Set up installed so the voice stops: start at
// login, the Claude Code plugin, the ctrl+x s shortcut and the running server (install_helper.py
// uninstall). The data stays, as with install.sh --uninstall. Plain Node: no VS Code API here.
import { spawn } from 'child_process'
import * as fs from 'fs'
import * as os from 'os'
import * as path from 'path'

const ID = 'abdalrhman-attar.read-aloud-for-claude-code'
// The claudeVoice.home setting can't be read from here: a voice kept elsewhere is left alone.
const home = process.env.KOKORO_HOME || path.join(os.homedir(), '.claude', 'kokoro')
const python =
  process.platform === 'win32' ? path.join(home, '.venv', 'Scripts', 'python.exe') : path.join(home, '.venv', 'bin', 'python')
// Set up copies the server into the voice's home. An install from a clone of the repository runs the
// clone's own server/ and has no copy: that one isn't the extension's to remove.
const helper = path.join(home, 'app', 'install_helper.py')

const same = (a: string, b: string) =>
  process.platform === 'win32' ? path.resolve(a).toLowerCase() === path.resolve(b).toLowerCase() : path.resolve(a) === path.resolve(b)

// Whether another editor (VS Code Insiders, Cursor, Windsurf...) still has this extension: it uses
// the same voice.
function inAnotherEditor(): boolean {
  const ours = path.resolve(__dirname, '..', '..') // <extensions folder>/<this extension>/out
  return ['.vscode', '.vscode-insiders', '.vscode-oss', '.cursor', '.windsurf']
    .map(dir => path.join(os.homedir(), dir, 'extensions'))
    .filter(dir => !same(dir, ours))
    .some(dir => {
      try {
        return fs.readdirSync(dir).some(name => name.toLowerCase().startsWith(`${ID}-`))
      } catch {
        return false
      }
    })
}

if (fs.existsSync(python) && fs.existsSync(helper) && !inAnotherEditor()) {
  // Detached: VS Code kills this hook after 5 seconds, and the claude command alone can take longer.
  spawn(python, [helper, 'uninstall'], {
    env: { ...process.env, KOKORO_HOME: home },
    detached: true,
    stdio: 'ignore',
    windowsHide: true,
  }).unref()
}
