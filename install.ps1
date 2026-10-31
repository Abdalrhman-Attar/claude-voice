# Sets up the local Kokoro voice for the speak-pane Claude Code plugin on Windows (x64 and ARM64).
#   powershell -ExecutionPolicy Bypass -File install.ps1             install
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall
param([switch]$Uninstall)
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
if (-not $Py) {
  Write-Host 'Python 3.10 to 3.13 is needed: winget install Python.Python.3.12'
  exit 1
}

New-Item -ItemType Directory -Force -Path $HomeDir | Out-Null
Write-Host "Python environment in $HomeDir\.venv"
if (-not (Test-Path $VenvPy)) {
  $exe = $Py[0]; $rest = @($Py | Select-Object -Skip 1)
  & $exe @rest -m venv (Join-Path $HomeDir '.venv')
}
& $VenvPy -m pip install -q --upgrade pip
& $VenvPy -m pip install -q -r (Join-Path $Repo 'server\requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }

& $VenvPy $Helper install
