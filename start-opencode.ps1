Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

Write-Host 'Facai Cloud: handing off to OpenCode with DeepSeek Flash.'
Write-Host 'Repository:' $PSScriptRoot
Write-Host 'Handoff:' (Join-Path $PSScriptRoot 'docs/OPENCODE_HANDOFF.md')

opencode run --model deepseek/deepseek-flash --file 'docs/OPENCODE_HANDOFF.md' --title 'Facai Cloud implementation' 'Read the attached handoff and docs/ARCHITECTURE.md, inspect the current repository and tests, then implement the next complete verifiable increment. Make small changes, test, commit, and continue toward the specified acceptance bar. Keep the API key out of files and logs. Report factual progress and blockers.'

Write-Host 'OpenCode run ended. To continue in the same project, run: opencode'
