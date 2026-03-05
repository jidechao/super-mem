# Shared setup for memsearch command hooks (PowerShell edition).
# Dot-source this file from hook scripts.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Read stdin JSON into $INPUT (same behavior as common.sh)
$INPUT = [Console]::In.ReadToEnd()

# Ensure common user bin paths are in PATH
$extraPaths = @(
    "$HOME\.local\bin",
    "$HOME\.cargo\bin",
    "$HOME\bin"
)
foreach ($p in $extraPaths) {
    if ((Test-Path $p) -and (-not ($env:PATH -split ";" | Where-Object { $_ -eq $p }))) {
        $env:PATH = "$p;$($env:PATH)"
    }
}

$projectDir = if ($env:CLAUDE_PROJECT_DIR) { $env:CLAUDE_PROJECT_DIR } else { (Get-Location).Path }
$MEMSEARCH_DIR = Join-Path $projectDir ".memsearch"
$WATCH_PIDFILE = Join-Path $MEMSEARCH_DIR ".watch.pid"

function _detect_memsearch {
    $script:MEMSEARCH_CMD = ""
    if (Get-Command memsearch -ErrorAction SilentlyContinue) {
        $script:MEMSEARCH_CMD = "memsearch"
    } elseif (Get-Command uvx -ErrorAction SilentlyContinue) {
        $script:MEMSEARCH_CMD = "uvx memsearch"
    }
}
_detect_memsearch

$MEMSEARCH_CMD_PREFIX = if ($MEMSEARCH_CMD) { $MEMSEARCH_CMD } else { "memsearch" }

function _sanitize_user([string]$raw) {
    $user = $raw.Trim().ToLowerInvariant()
    $user = [Regex]::Replace($user, "[^a-z0-9_-]", "_")
    $user = [Regex]::Replace($user, "_+", "_")
    $user = $user.Trim("_")
    if ([string]::IsNullOrWhiteSpace($user)) {
        return "default"
    }
    if ($user.Length -gt 128) {
        return $user.Substring(0, 128)
    }
    return $user
}

$rawUser = if ($env:MEMSEARCH_USER) { $env:MEMSEARCH_USER } elseif ($env:USERNAME) { $env:USERNAME } else { "default" }
$MEMSEARCH_USER = _sanitize_user $rawUser
$env:MEMSEARCH_USER = $MEMSEARCH_USER

function _derive_collection_name {
    $deriveScript = Join-Path (Join-Path $PSScriptRoot "..\scripts") "derive-collection.sh"
    if ((Get-Command bash -ErrorAction SilentlyContinue) -and (Test-Path $deriveScript)) {
        try {
            $out = & bash $deriveScript $projectDir 2>$null
            if ($LASTEXITCODE -eq 0 -and $out) {
                return ($out | Select-Object -First 1).Trim()
            }
        } catch {
            # fall through
        }
    }

    $base = Split-Path -Leaf $projectDir
    $sanitized = ([Regex]::Replace($base.ToLowerInvariant(), "[^a-z0-9]", "_")).Trim("_")
    if ([string]::IsNullOrWhiteSpace($sanitized)) {
        $sanitized = "project"
    }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $bytes = [Text.Encoding]::UTF8.GetBytes($projectDir)
    $hash = ($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString("x2") }) -join ""
    return "ms_${sanitized}_$($hash.Substring(0, 8))"
}
$COLLECTION_NAME = _derive_collection_name

function _resolve_memory_base {
    $base = ""
    if ($MEMSEARCH_CMD) {
        try {
            if ($MEMSEARCH_CMD -eq "memsearch") {
                $base = (& memsearch config get memory.base_dir 2>$null | Select-Object -First 1)
            } elseif ($MEMSEARCH_CMD -eq "uvx memsearch") {
                $base = (& uvx memsearch config get memory.base_dir 2>$null | Select-Object -First 1)
            }
            if ($base) {
                $base = $base.Trim()
            }
        } catch {
            $base = ""
        }
    }
    if ([string]::IsNullOrWhiteSpace($base)) {
        $base = "memory"
    }
    if ([System.IO.Path]::IsPathRooted($base)) {
        return $base
    }
    return (Join-Path $projectDir $base)
}

function _resolve_memory_dir_name {
    param(
        [string]$configKey,
        [string]$defaultName
    )
    $dirName = ""
    if ($MEMSEARCH_CMD) {
        try {
            if ($MEMSEARCH_CMD -eq "memsearch") {
                $dirName = (& memsearch config get $configKey 2>$null | Select-Object -First 1)
            } elseif ($MEMSEARCH_CMD -eq "uvx memsearch") {
                $dirName = (& uvx memsearch config get $configKey 2>$null | Select-Object -First 1)
            }
            if ($dirName) {
                $dirName = $dirName.Trim()
            }
        } catch {
            $dirName = ""
        }
    }
    if ([string]::IsNullOrWhiteSpace($dirName)) {
        $dirName = $defaultName
    }
    return $dirName
}

$MEMORY_BASE = _resolve_memory_base
$USER_MEMORY_ROOT = Join-Path $MEMORY_BASE $MEMSEARCH_USER
$SHORT_MEMORY_DIR_NAME = _resolve_memory_dir_name "memory.short_memory_dir" "short-memory"
$LONG_MEMORY_DIR_NAME = _resolve_memory_dir_name "memory.long_memory_dir" "long-memory"
$MEMORY_DIR = Join-Path $USER_MEMORY_ROOT $SHORT_MEMORY_DIR_NAME
$LONG_MEMORY_DIR = Join-Path $USER_MEMORY_ROOT $LONG_MEMORY_DIR_NAME
$WATCH_DIR = $USER_MEMORY_ROOT

function _json_val {
    param(
        [string]$json,
        [string]$key,
        [string]$default = ""
    )
    if ([string]::IsNullOrWhiteSpace($json)) {
        return $default
    }
    try {
        $obj = $json | ConvertFrom-Json -Depth 100
        $current = $obj
        foreach ($part in $key.Split(".")) {
            if ($null -eq $current) { return $default }
            if ($current -is [System.Collections.IDictionary]) {
                if (-not $current.Contains($part)) { return $default }
                $current = $current[$part]
            } else {
                $prop = $current.PSObject.Properties[$part]
                if ($null -eq $prop) { return $default }
                $current = $prop.Value
            }
        }
        if ($null -eq $current) { return $default }
        if ($current -is [bool]) { return $current.ToString().ToLowerInvariant() }
        return [string]$current
    } catch {
        return $default
    }
}

function _json_encode_str {
    param([string]$str)
    return ($str | ConvertTo-Json -Compress)
}

function ensure_memory_dir {
    New-Item -ItemType Directory -Path $MEMORY_DIR -Force | Out-Null
    New-Item -ItemType Directory -Path $LONG_MEMORY_DIR -Force | Out-Null
}

function run_memsearch {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$args)
    if (-not $MEMSEARCH_CMD) {
        return
    }

    $finalArgs = @($args)
    $withCollection = $true
    if ($args.Count -gt 0) {
        $cmd = $args[0]
        if ($cmd -in @("memory", "config", "transcript")) {
            $withCollection = $false
        }
    }

    if ($withCollection -and $COLLECTION_NAME) {
        $finalArgs += @("--collection", $COLLECTION_NAME)
    }
    $finalArgs += @("--user", $MEMSEARCH_USER)

    if ($MEMSEARCH_CMD -eq "memsearch") {
        & memsearch @finalArgs 2>$null | Out-Null
    } elseif ($MEMSEARCH_CMD -eq "uvx memsearch") {
        & uvx memsearch @finalArgs 2>$null | Out-Null
    }
}

function stop_watch {
    if (Test-Path $WATCH_PIDFILE) {
        $pidText = (Get-Content $WATCH_PIDFILE -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($pidText -and ($pidText -as [int])) {
            Stop-Process -Id ([int]$pidText) -Force -ErrorAction SilentlyContinue
        }
        Remove-Item $WATCH_PIDFILE -Force -ErrorAction SilentlyContinue
    }

    # Sweep possible orphan watch processes for this memory root.
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -like "*memsearch watch*" -and
            $_.CommandLine -like "*$WATCH_DIR*"
        } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
}

function start_watch {
    if (-not $MEMSEARCH_CMD) {
        return
    }
    ensure_memory_dir
    stop_watch

    $watchArgs = @("watch", $WATCH_DIR)
    if ($COLLECTION_NAME) {
        $watchArgs += @("--collection", $COLLECTION_NAME)
    }
    $watchArgs += @("--user", $MEMSEARCH_USER)

    New-Item -ItemType Directory -Path $MEMSEARCH_DIR -Force | Out-Null
    if ($MEMSEARCH_CMD -eq "memsearch") {
        $proc = Start-Process -FilePath "memsearch" -ArgumentList $watchArgs -WindowStyle Hidden -PassThru
    } else {
        $proc = Start-Process -FilePath "uvx" -ArgumentList @("memsearch") + $watchArgs -WindowStyle Hidden -PassThru
    }
    Set-Content -Path $WATCH_PIDFILE -Value $proc.Id
}
