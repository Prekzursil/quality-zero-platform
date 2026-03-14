[CmdletBinding()]
param(
  [Parameter(Position = 0)]
  [ValidateSet('list', 'bootstrap', 'open', 'inspect', 'help', 'install-browsers')]
  [string]$Command = 'help',

  [string]$Provider,
  [string]$Repo,
  [string]$Owner = 'Prekzursil',
  [string]$StateRoot = $env:QUALITY_ZERO_PROVIDER_UI_HOME,
  [string]$ProfileDir,
  [switch]$Headless,
  [switch]$Headed,
  [switch]$KeepOpen,
  [int]$TimeoutMs = 90000,
  [int]$SlowMoMs = 0,
  [switch]$RefreshDeps,
  [switch]$SkipDependencyInstall
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$scriptPath = Join-Path $PSScriptRoot 'provider_admin_bootstrap.mjs'

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  throw 'Node.js is required. Install Node.js so that node.exe is on PATH.'
}

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
  throw 'npm is required. Install Node.js/npm so that npm.exe is on PATH.'
}

function Get-StateRoot {
  if ($StateRoot) {
    return [System.IO.Path]::GetFullPath($StateRoot)
  }

  if ($env:LOCALAPPDATA) {
    return (Join-Path $env:LOCALAPPDATA 'quality-zero-platform\provider-ui')
  }

  return (Join-Path $HOME '.quality-zero-platform\provider-ui')
}

$resolvedStateRoot = Get-StateRoot
$runnerDir = Join-Path $resolvedStateRoot 'pw-runner'
$packageJsonPath = Join-Path $runnerDir 'package.json'
$nodeModulesPlaywright = Join-Path $runnerDir 'node_modules\playwright\package.json'

New-Item -ItemType Directory -Force -Path $runnerDir | Out-Null
if (-not (Test-Path $packageJsonPath)) {
  '{"name":"quality-zero-platform-provider-ui-runner","private":true}' | Set-Content -Path $packageJsonPath -NoNewline
}

if ($RefreshDeps -and (Test-Path $nodeModulesPlaywright)) {
  Remove-Item -Recurse -Force (Join-Path $runnerDir 'node_modules')
}

if (-not $SkipDependencyInstall -and -not (Test-Path $nodeModulesPlaywright)) {
  Write-Host "Installing Playwright into external runner dir: $runnerDir"
  & npm install --prefix $runnerDir --no-audit --no-fund --silent playwright | Out-Host
}

$env:QUALITY_ZERO_PROVIDER_UI_RUNNER_DIR = $runnerDir
if ($Command -eq 'install-browsers') {
  & node (Join-Path $runnerDir 'node_modules\playwright\cli.js') install chromium
  exit $LASTEXITCODE
}

$nodeArgs = [System.Collections.Generic.List[string]]::new()
$nodeArgs.Add($scriptPath)
$nodeArgs.Add($Command)
if ($Provider) { $nodeArgs.Add('--provider'); $nodeArgs.Add($Provider) }
if ($Repo) { $nodeArgs.Add('--repo'); $nodeArgs.Add($Repo) }
if ($Owner) { $nodeArgs.Add('--owner'); $nodeArgs.Add($Owner) }
if ($resolvedStateRoot) { $nodeArgs.Add('--state-root'); $nodeArgs.Add($resolvedStateRoot) }
if ($ProfileDir) { $nodeArgs.Add('--profile-dir'); $nodeArgs.Add([System.IO.Path]::GetFullPath($ProfileDir)) }
if ($Headless) { $nodeArgs.Add('--headless') }
if ($Headed) { $nodeArgs.Add('--headed') }
if ($KeepOpen) { $nodeArgs.Add('--keep-open') }
$nodeArgs.Add('--timeout-ms'); $nodeArgs.Add($TimeoutMs.ToString())
$nodeArgs.Add('--slow-mo-ms'); $nodeArgs.Add($SlowMoMs.ToString())

Push-Location $repoRoot
try {
  & node @nodeArgs
  exit $LASTEXITCODE
}
finally {
  Pop-Location
}
