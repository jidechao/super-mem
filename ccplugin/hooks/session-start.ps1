#!/usr/bin/env pwsh
# SessionStart hook: start watch singleton + inject recent memory context.

. "$PSScriptRoot\common.ps1"

# Bootstrap: if memsearch not available, try warming uvx cache when uvx exists.
if (-not $MEMSEARCH_CMD) {
    if (Get-Command uvx -ErrorAction SilentlyContinue) {
        try {
            & uvx --upgrade memsearch --version 2>$null | Out-Null
        } catch {
            # ignore
        }
        _detect_memsearch
    }
}

$provider = "openai"
$model = ""
$milvusUri = ""
$version = ""
if ($MEMSEARCH_CMD -eq "memsearch") {
    $provider = (& memsearch config get embedding.provider 2>$null | Select-Object -First 1)
    $model = (& memsearch config get embedding.model 2>$null | Select-Object -First 1)
    $milvusUri = (& memsearch config get milvus.uri 2>$null | Select-Object -First 1)
    $versionRaw = (& memsearch --version 2>$null | Select-Object -First 1)
    if ($versionRaw) { $version = $versionRaw -replace ".*version\s+", "" }
} elseif ($MEMSEARCH_CMD -eq "uvx memsearch") {
    $provider = (& uvx memsearch config get embedding.provider 2>$null | Select-Object -First 1)
    $model = (& uvx memsearch config get embedding.model 2>$null | Select-Object -First 1)
    $milvusUri = (& uvx memsearch config get milvus.uri 2>$null | Select-Object -First 1)
    $versionRaw = (& uvx memsearch --version 2>$null | Select-Object -First 1)
    if ($versionRaw) { $version = $versionRaw -replace ".*version\s+", "" }
}

function Get-RequiredKey([string]$p) {
    switch ($p) {
        "openai" { return "OPENAI_API_KEY" }
        "google" { return "GOOGLE_API_KEY" }
        "voyage" { return "VOYAGE_API_KEY" }
        default { return "" }
    }
}

$requiredKey = Get-RequiredKey $provider
$keyMissing = $false
if ($requiredKey -and -not [Environment]::GetEnvironmentVariable($requiredKey)) {
    $keyMissing = $true
}

$versionTag = if ($version) { " v$version" } else { "" }
$collectionHint = if ($COLLECTION_NAME) { " | collection: $COLLECTION_NAME" } else { "" }
$status = "[memsearch$versionTag] embedding: $provider/$model | milvus: $milvusUri$collectionHint"
if ($keyMissing) {
    $status += " | ERROR: $requiredKey not set — memory search disabled"
}

ensure_memory_dir
if ($keyMissing) {
    @{ systemMessage = $status } | ConvertTo-Json -Compress -Depth 8
    exit 0
}

start_watch

if (-not (Test-Path $MEMORY_DIR)) {
    @{ systemMessage = $status } | ConvertTo-Json -Compress -Depth 8
    exit 0
}

$recentFiles = Get-ChildItem -Path $MEMORY_DIR -Filter *.md -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending |
    Select-Object -First 2

if (-not $recentFiles) {
    @{ systemMessage = $status } | ConvertTo-Json -Compress -Depth 8
    exit 0
}

$context = "# Recent Memory`n`n"
foreach ($f in $recentFiles) {
    $tail = (Get-Content -Path $f.FullName -Tail 30 -ErrorAction SilentlyContinue) -join "`n"
    if (-not [string]::IsNullOrWhiteSpace($tail)) {
        $context += "## $($f.Name)`n$tail`n`n"
    }
}

if ([string]::IsNullOrWhiteSpace($context.Trim())) {
    @{ systemMessage = $status } | ConvertTo-Json -Compress -Depth 8
    exit 0
}

@{
    systemMessage = $status
    hookSpecificOutput = @{
        hookEventName = "SessionStart"
        additionalContext = $context
    }
} | ConvertTo-Json -Compress -Depth 8
