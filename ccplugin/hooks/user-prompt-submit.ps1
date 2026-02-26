#!/usr/bin/env pwsh
# UserPromptSubmit hook: lightweight hint reminding Claude about memory.

. "$PSScriptRoot\common.ps1"

$prompt = _json_val $INPUT "prompt" ""
if ([string]::IsNullOrWhiteSpace($prompt) -or $prompt.Length -lt 10) {
    "{}"
    exit 0
}

if (-not $MEMSEARCH_CMD) {
    "{}"
    exit 0
}

@{ systemMessage = "[memsearch] Memory available" } | ConvertTo-Json -Compress -Depth 8
