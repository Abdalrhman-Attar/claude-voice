// Launches a fresh VS Code with this extension loaded and runs test/suite.js inside it.
const path = require('path')
const { runTests } = require('@vscode/test-electron')

runTests({
  // A fresh profile and no other extensions, on the VS Code downloaded for tests.
  vscodeExecutablePath: require('fs').readdirSync(path.join(__dirname, '..', '.vscode-test'))
    .filter(d => d.startsWith('vscode-')).map(d => path.join(__dirname, '..', '.vscode-test', d, 'Visual Studio Code.app', 'Contents', 'MacOS', 'Code'))[0],
  extensionDevelopmentPath: path.join(__dirname, '..'),
  extensionTestsPath: path.join(__dirname, 'suite.js'),
  // An empty folder is open, so the folder filter has something to filter by.
  launchArgs: [
    require('fs').mkdtempSync(require('path').join(require('os').tmpdir(), 'voice-folder-')),
    // A fresh profile each run: a reused one reopens its last window and runs the tests twice.
    `--user-data-dir=${require('fs').mkdtempSync(require('path').join(require('os').tmpdir(), 'voice-profile-'))}`,
    '--disable-extensions',
    '--skip-welcome',
    '--skip-release-notes',
  ],
}).catch(err => {
  console.error('Tests failed:', err)
  process.exit(1)
})
