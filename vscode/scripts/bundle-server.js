// Copies the voice server and installers into the extension, so a .vsix can set the voice up
// without the repository: server/ (Python), install.sh, install.ps1, LICENSE.
const fs = require('fs')
const path = require('path')

const repo = path.join(__dirname, '..', '..')
const out = path.join(__dirname, '..', 'bundle')
fs.rmSync(out, { recursive: true, force: true })
fs.mkdirSync(path.join(out, 'server'), { recursive: true })
for (const f of fs.readdirSync(path.join(repo, 'server'))) {
  if (/\.(py|json|txt)$/.test(f)) fs.copyFileSync(path.join(repo, 'server', f), path.join(out, 'server', f))
}
for (const f of ['install.sh', 'install.ps1']) fs.copyFileSync(path.join(repo, f), path.join(out, f))
fs.chmodSync(path.join(out, 'install.sh'), 0o755)
fs.copyFileSync(path.join(repo, 'LICENSE'), path.join(__dirname, '..', 'LICENSE'))
