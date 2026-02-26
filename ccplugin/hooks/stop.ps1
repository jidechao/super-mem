#!/usr/bin/env pwsh
# Stop hook: parse transcript, summarize, and write via memsearch memory API.

. "$PSScriptRoot\common.ps1"

$stopHookActive = _json_val $INPUT "stop_hook_active" "false"
if ($stopHookActive -eq "true") {
    "{}"
    exit 0
}

if (-not $MEMSEARCH_CMD) {
    "{}"
    exit 0
}

function Get-RequiredKey([string]$provider) {
    switch ($provider) {
        "openai" { return "OPENAI_API_KEY" }
        "google" { return "GOOGLE_API_KEY" }
        "voyage" { return "VOYAGE_API_KEY" }
        default { return "" }
    }
}

$provider = "openai"
if ($MEMSEARCH_CMD -eq "memsearch") {
    $provider = (& memsearch config get embedding.provider 2>$null | Select-Object -First 1)
} elseif ($MEMSEARCH_CMD -eq "uvx memsearch") {
    $provider = (& uvx memsearch config get embedding.provider 2>$null | Select-Object -First 1)
}
$requiredKey = Get-RequiredKey $provider
if ($requiredKey -and -not [Environment]::GetEnvironmentVariable($requiredKey)) {
    "{}"
    exit 0
}

$transcriptPath = _json_val $INPUT "transcript_path" ""
if ([string]::IsNullOrWhiteSpace($transcriptPath) -or -not (Test-Path $transcriptPath)) {
    "{}"
    exit 0
}

$lineCount = (Get-Content -Path $transcriptPath -ErrorAction SilentlyContinue | Measure-Object -Line).Lines
if ($lineCount -lt 3) {
    "{}"
    exit 0
}

ensure_memory_dir

$parsed = (& "$PSScriptRoot\parse-transcript.ps1" $transcriptPath 2>$null) -join "`n"
if ([string]::IsNullOrWhiteSpace($parsed) -or $parsed -eq "(empty transcript)") {
    "{}"
    exit 0
}

$sessionId = [System.IO.Path]::GetFileNameWithoutExtension($transcriptPath)
$lastUserTurn = ""
Get-Content -Path $transcriptPath -ErrorAction SilentlyContinue | ForEach-Object {
    try {
        $obj = $_ | ConvertFrom-Json -Depth 100
        if ($obj.type -eq "user" -and ($obj.message.content -is [string])) {
            $lastUserTurn = [string]$obj.uuid
        }
    } catch {
        # ignore
    }
}

$summary = $parsed
if (Get-Command claude -ErrorAction SilentlyContinue) {
    $systemPrompt = @"
You are a session memory writer. Your ONLY job is to output bullet-point summaries. Output NOTHING else — no greetings, no questions, no offers to help, no preamble, no closing remarks.

Rules:
- Output 3-8 bullet points, each starting with '- '
- Focus on: decisions made, problems solved, code changes, key findings
- Be specific and factual — mention file names, function names, and concrete details
- Do NOT include timestamps, headers, or any formatting beyond bullet points
- Do NOT add any text before or after the bullet points
"@
    try {
        $generated = $parsed | claude -p --model haiku --no-session-persistence --no-chrome --system-prompt $systemPrompt 2>$null
        if (-not [string]::IsNullOrWhiteSpace($generated)) {
            $summary = ($generated | Out-String).Trim()
        }
    } catch {
        # keep parsed fallback
    }
}

$args = @("memory", "write", "--stdin", "--source", "auto/stop-hook")
if ($sessionId) { $args += @("--session-id", $sessionId) }
if ($lastUserTurn) { $args += @("--turn-id", $lastUserTurn) }
if ($transcriptPath) { $args += @("--transcript-path", $transcriptPath) }
$args += @("--user", $MEMSEARCH_USER)

if ($MEMSEARCH_CMD -eq "memsearch") {
    $summary | memsearch @args 2>$null | Out-Null
} elseif ($MEMSEARCH_CMD -eq "uvx memsearch") {
    $summary | uvx memsearch @args 2>$null | Out-Null
}

"{}"
