#!/usr/bin/env pwsh
# Parse a Claude Code JSONL transcript into concise text summary.

param(
    [Parameter(Mandatory = $true)]
    [string]$TranscriptPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path $TranscriptPath)) {
    Write-Error "ERROR: transcript not found: $TranscriptPath"
    exit 1
}

$maxLines = if ($env:MEMSEARCH_MAX_LINES) { [int]$env:MEMSEARCH_MAX_LINES } else { 200 }
$maxChars = if ($env:MEMSEARCH_MAX_CHARS) { [int]$env:MEMSEARCH_MAX_CHARS } else { 500 }

function Truncate-Tail([string]$text, [int]$max) {
    if ([string]::IsNullOrEmpty($text)) { return "" }
    if ($text.Length -le $max) { return $text }
    return "..." + $text.Substring($text.Length - $max)
}

function Time-FromIso([string]$ts) {
    if ([string]::IsNullOrWhiteSpace($ts)) { return "" }
    if ($ts -match "T(\d{2}:\d{2}:\d{2})") { return $Matches[1] }
    if ($ts.Length -ge 8) { return $ts.Substring(0, 8) }
    return $ts
}

$totalLines = (Get-Content -Path $TranscriptPath -ErrorAction SilentlyContinue | Measure-Object -Line).Lines
if (-not $totalLines) {
    "(empty transcript)"
    exit 0
}

if ($totalLines -gt $maxLines) {
    "=== Transcript (last $maxLines of $totalLines lines) ==="
} else {
    "=== Transcript ($totalLines lines) ==="
}
""

$tailLines = Get-Content -Path $TranscriptPath -Tail $maxLines
foreach ($line in $tailLines) {
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    try {
        $entry = $line | ConvertFrom-Json -Depth 100
    } catch {
        continue
    }

    $entryType = [string]$entry.type
    if ($entryType -eq "file-history-snapshot") { continue }

    $ts = Time-FromIso ([string]$entry.timestamp)

    if ($entryType -eq "user") {
        $msg = $entry.message
        $content = $msg.content
        if ($content -is [array]) {
            if ($content.Count -gt 0 -and $content[0].type -eq "tool_result") {
                $raw = ""
                if ($content[0].content -is [array]) {
                    if ($content[0].content.Count -gt 0) {
                        $raw = [string]$content[0].content[0].text
                    }
                } else {
                    $raw = [string]$content[0].content
                }
                "[$ts] TOOL RESULT: $(Truncate-Tail $raw $maxChars)"
            }
        } else {
            $userText = [string]$content
            if (-not [string]::IsNullOrWhiteSpace($userText)) {
                ""
                "[$ts] USER: $(Truncate-Tail $userText $maxChars)"
            }
        }
        continue
    }

    if ($entryType -eq "assistant") {
        $blocks = $entry.message.content
        if (-not ($blocks -is [array])) { continue }
        foreach ($b in $blocks) {
            if ($b.type -eq "text") {
                $text = [string]$b.text
                if (-not [string]::IsNullOrWhiteSpace($text)) {
                    "[$ts] ASSISTANT: $(Truncate-Tail $text $maxChars)"
                }
            } elseif ($b.type -eq "tool_use") {
                $name = [string]$b.name
                "[$ts] TOOL USE: $name"
            }
        }
    }
}

""
"=== End of transcript ==="
