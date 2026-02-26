#!/usr/bin/env bash
# Parse a Claude Code JSONL transcript into a concise text summary
# suitable for AI summarization.
#
# Usage: bash parse-transcript.sh <transcript_path>
#
# Truncation rules:
#   - Only process the last MAX_LINES lines (default 200)
#   - User/assistant text content > MAX_CHARS chars (default 500) is truncated to tail
#   - Tool calls: only output tool name + truncated input summary
#   - Tool results: only output a one-line truncated preview
#   - Skip file-history-snapshot entries entirely

set -euo pipefail

# On Windows, dispatch to native PowerShell hook.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${OS:-}" == "Windows_NT" ]] && command -v powershell &>/dev/null; then
  exec powershell -ExecutionPolicy Bypass -File "$SCRIPT_DIR/parse-transcript.ps1" "$@"
fi

# parse-transcript requires jq for JSON processing; gracefully degrade if missing
if ! command -v jq &>/dev/null; then
  echo "(transcript parsing skipped — jq not installed)"
  exit 0
fi

TRANSCRIPT_PATH="${1:-}"
MAX_LINES="${MEMSEARCH_MAX_LINES:-200}"
MAX_CHARS="${MEMSEARCH_MAX_CHARS:-500}"

if [ -z "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ]; then
  echo "ERROR: transcript not found: $TRANSCRIPT_PATH" >&2
  exit 1
fi

# Count total lines for context
TOTAL_LINES=$(wc -l < "$TRANSCRIPT_PATH")

if [ "$TOTAL_LINES" -eq 0 ]; then
  echo "(empty transcript)"
  exit 0
fi

# Helper: truncate text to last N chars
truncate_tail() {
  local text="$1"
  local max="$2"
  local len=${#text}
  if [ "$len" -le "$max" ]; then
    printf '%s' "$text"
  else
    printf '...%s' "${text: -$max}"
  fi
}

# Print header
if [ "$TOTAL_LINES" -gt "$MAX_LINES" ]; then
  echo "=== Transcript (last $MAX_LINES of $TOTAL_LINES lines) ==="
else
  echo "=== Transcript ($TOTAL_LINES lines) ==="
fi
echo ""

# Process JSONL — take the last MAX_LINES, parse with jq line by line
tail -n "$MAX_LINES" "$TRANSCRIPT_PATH" | while IFS= read -r line; do
  # Extract type
  entry_type=$(printf '%s' "$line" | jq -r '.type // empty' 2>/dev/null) || continue

  # Skip file snapshots
  [ "$entry_type" = "file-history-snapshot" ] && continue

  # Extract timestamp
  ts=$(printf '%s' "$line" | jq -r '.timestamp // empty' 2>/dev/null)
  ts_short=""
  if [ -n "$ts" ]; then
    # Extract HH:MM:SS from ISO timestamp
    ts_short=$(printf '%s' "$ts" | sed -n 's/.*T\([0-9][0-9]:[0-9][0-9]:[0-9][0-9]\).*/\1/p' 2>/dev/null || echo "")
  fi

  if [ "$entry_type" = "user" ]; then
    # Check if it's a tool_result or a normal user message
    content_type=$(printf '%s' "$line" | jq -r '.message.content | if type == "array" then .[0].type // "text" else "text" end' 2>/dev/null) || content_type="text"

    if [ "$content_type" = "tool_result" ]; then
      # Tool result — one-line preview
      result_text=$(printf '%s' "$line" | jq -r '.message.content[0].content // "" | if type == "array" then .[0].text // "" else . end' 2>/dev/null)
      result_short=$(truncate_tail "$result_text" "$MAX_CHARS")
      echo "[${ts_short}] TOOL RESULT: ${result_short}"
    else
      # Normal user message
      user_text=$(printf '%s' "$line" | jq -r '.message.content // "" | if type == "array" then map(select(.type == "text") | .text) | join("\n") else . end' 2>/dev/null)
      user_short=$(truncate_tail "$user_text" "$MAX_CHARS")
      echo ""
      echo "[${ts_short}] USER: ${user_short}"
    fi

  elif [ "$entry_type" = "assistant" ]; then
    # Process each content block
    num_blocks=$(printf '%s' "$line" | jq -r '.message.content | length' 2>/dev/null) || num_blocks=0

    for (( i=0; i<num_blocks; i++ )); do
      block_type=$(printf '%s' "$line" | jq -r ".message.content[$i].type // empty" 2>/dev/null)

      if [ "$block_type" = "text" ]; then
        text=$(printf '%s' "$line" | jq -r ".message.content[$i].text // empty" 2>/dev/null)
        [ -z "$text" ] && continue
        text_short=$(truncate_tail "$text" "$MAX_CHARS")
        echo "[${ts_short}] ASSISTANT: ${text_short}"

      elif [ "$block_type" = "tool_use" ]; then
        tool_name=$(printf '%s' "$line" | jq -r ".message.content[$i].name // \"unknown\"" 2>/dev/null)
        # One-line summary of tool input
        tool_input_summary=$(printf '%s' "$line" | jq -r ".message.content[$i].input | to_entries | map(\"\(.key)=\(.value | tostring | .[0:80])\") | join(\", \")" 2>/dev/null || echo "")
        tool_input_short=$(truncate_tail "$tool_input_summary" 200)
        echo "[${ts_short}] TOOL USE: ${tool_name}(${tool_input_short})"
      fi
    done
  fi
done

echo ""
echo "=== End of transcript ==="
