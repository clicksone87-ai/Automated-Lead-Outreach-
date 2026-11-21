<#
.SYNOPSIS
  Windows equivalent of the Makefile.
.EXAMPLE
  .\make.ps1 up
  .\make.ps1 seed
#>
param(
  [Parameter(Position = 0)]
  [ValidateSet('up', 'down', 'build', 'logs', 'migrate', 'test', 'test-backend', 'test-dashboard',
               'lint', 'seed', 'run-cycle', 'backup', 'check-providers', 'purge-demo', 'clean-leads', 'shell',
               'help')]
  [string]$Target = 'help'
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

function Invoke-Api([string[]]$CommandArgs) {
  docker compose exec -T api @CommandArgs
  if ($LASTEXITCODE -ne 0) { throw "command failed: $($CommandArgs -join ' ')" }
}

function Invoke-Dashboard([string]$Script) {
  Push-Location dashboard
  try {
    npm run $Script --silent
    if ($LASTEXITCODE -ne 0) { throw "npm run $Script failed" }
  } finally { Pop-Location }
}

switch ($Target) {
  'up'             { docker compose up -d --build }
  'down'           { docker compose down }
  'build'          { docker compose build }
  'logs'           { docker compose logs -f --tail=100 }
  'migrate'        { Invoke-Api @('alembic', 'upgrade', 'head') }
  'test-backend'   { Invoke-Api @('pytest', '-q') }
  'test-dashboard' { Invoke-Dashboard 'test' }
  'test'           { Invoke-Api @('pytest', '-q'); Invoke-Dashboard 'test' }
  'lint'           {
    Invoke-Api @('ruff', 'check', 'app', 'tests')
    Invoke-Api @('mypy', 'app')
    Invoke-Dashboard 'lint'
  }
  'seed'           { Invoke-Api @('python', '-m', 'app.cli', 'seed') }
  'run-cycle'      { Invoke-Api @('python', '-m', 'app.cli', 'run-cycle') }
  'backup'         { Invoke-Api @('python', '-m', 'app.cli', 'backup') }
  'check-providers' { Invoke-Api @('python', '-m', 'app.cli', 'check-providers') }
  'purge-demo'     { Invoke-Api @('python', '-m', 'app.cli', 'purge-demo') }
  'clean-leads'    { Invoke-Api @('python', '-m', 'app.cli', 'clean-leads') }
  'shell'          { docker compose exec api bash }
  default          {
    Write-Host 'Targets: up, down, build, logs, migrate, test, test-backend, test-dashboard, lint, seed, run-cycle, backup, check-providers, purge-demo, clean-leads, shell'
  }
}
