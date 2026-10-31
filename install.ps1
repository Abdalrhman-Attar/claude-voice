# Sets up the local Kokoro voice for the speak-pane Claude Code plugin on Windows (x64 and ARM64).
#   powershell -ExecutionPolicy Bypass -File install.ps1             install
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Agents codex,gemini,cursor,mcp
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Arabic     also the Arabic voice (about 170 MB)
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall
param([switch]$Uninstall, [string]$Agents = '', [switch]$Arabic)
$ErrorActionPreference = 'Stop'

$Repo = $PSScriptRoot
$HomeDir = if ($env:KOKORO_HOME) { $env:KOKORO_HOME } else { Join-Path $env:USERPROFILE '.claude\kokoro' }
$env:KOKORO_HOME = $HomeDir
$VenvPy = Join-Path $HomeDir '.venv\Scripts\python.exe'
$Helper = Join-Path $Repo 'server\install_helper.py'

if ($Uninstall) {
  if (Test-Path $VenvPy) { & $VenvPy $Helper uninstall } else { Write-Host "Nothing installed in $HomeDir." }
  exit 0
}

# Python 3.10 to 3.13, through the py launcher when it is there.
$Py = $null
foreach ($v in '3.12', '3.13', '3.11', '3.10') {
  if (Get-Command py -ErrorAction SilentlyContinue) {
    & py "-$v" -c "import sys" 2>$null
    if ($LASTEXITCODE -eq 0) { $Py = @('py', "-$v"); break }
  }
}
if (-not $Py -and (Get-Command python -ErrorAction SilentlyContinue)) {
  & python -c "import sys; sys.exit(not (3, 10) <= sys.version_info[:2] <= (3, 13))" 2>$null
  if ($LASTEXITCODE -eq 0) { $Py = @('python') }
}
New-Item -ItemType Directory -Force -Path $HomeDir | Out-Null
$Venv = Join-Path $HomeDir '.venv'
$Req = Join-Path $Repo 'server\requirements.txt'
if (-not $Py) {
  # No suitable Python: uv fetches a private Python 3.12 for the voice.
  $Uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
  if (-not $Uv) {
    Write-Host 'Python 3.10 to 3.13 not found: installing uv to fetch one (https://astral.sh/uv)'
    $env:UV_NO_MODIFY_PATH = '1'
    powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" | Out-Null
    $Uv = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
  }
  if (-not (Test-Path $VenvPy)) { & $Uv venv -q --python 3.12 $Venv }
  Write-Host "Python environment in $Venv"
  Write-Host "Installing the voice's Python packages"
  & $Uv pip install -q --python $VenvPy -r $Req
} else {
  Write-Host "Python environment in $Venv"
  if (-not (Test-Path $VenvPy)) {
    $exe = $Py[0]; $rest = @($Py | Select-Object -Skip 1)
    & $exe @rest -m venv $Venv
  }
  Write-Host "Installing the voice's Python packages"
  & $VenvPy -m pip install -q --upgrade pip
  & $VenvPy -m pip install -q -r $Req
}
if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }

$HelperArgs = @('install')
if ($Agents) { $HelperArgs += @('--agents', $Agents) }
if ($Arabic) { $HelperArgs += '--arabic' }
& $VenvPy $Helper @HelperArgs
