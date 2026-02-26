#!/usr/bin/env bash
# Stop hook: parse transcript, summarize with claude -p, and save to memory.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# On Windows, dispatch to native PowerShell hook.
if [[ "${OS:-}" == "Windows_NT" ]] && command -v powershell &>/dev/null; then
  exec powershell -ExecutionPolicy Bypass -File "$SCRIPT_DIR/stop.ps1" "$@"
fi
source "$SCRIPT_DIR/common.sh"

# Prevent infinite loop: if this Stop was triggered by a previous Stop hook, bail out
STOP_HOOK_ACTIVE=$(_json_val "$INPUT" "stop_hook_active" "false")
if [ "$STOP_HOOK_ACTIVE" = "true" ]; then
  echo '{}'
  exit 0
fi

# Skip summarization when the required API key is missing — embedding/search
# would fail, and the session likely only contains the "key not set" warning.
if [ -z "$MEMSEARCH_CMD" ]; then
  echo '{}'
  exit 0
fi

_required_env_var() {
  case "$1" in
    openai) echo "OPENAI_API_KEY" ;;
    google) echo "GOOGLE_API_KEY" ;;
    voyage) echo "VOYAGE_API_KEY" ;;
    *) echo "" ;;  # ollama, local — no API key needed
  esac
}
_PROVIDER=$($MEMSEARCH_CMD config get embedding.provider 2>/dev/null || echo "openai")
_REQ_KEY=$(_required_env_var "$_PROVIDER")
if [ -n "$_REQ_KEY" ] && [ -z "${!_REQ_KEY:-}" ]; then
  echo '{}'
  exit 0
fi

# Extract transcript path from hook input
TRANSCRIPT_PATH=$(_json_val "$INPUT" "transcript_path" "")

if [ -z "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ]; then
  echo '{}'
  exit 0
fi

# Check if transcript is empty (< 3 lines = no real content)
LINE_COUNT=$(wc -l < "$TRANSCRIPT_PATH" 2>/dev/null || echo "0")
if [ "$LINE_COUNT" -lt 3 ]; then
  echo '{}'
  exit 0
fi

ensure_memory_dir

# Parse transcript into concise text
PARSED=$("$SCRIPT_DIR/parse-transcript.sh" "$TRANSCRIPT_PATH" 2>/dev/null || true)

if [ -z "$PARSED" ] || [ "$PARSED" = "(empty transcript)" ]; then
  echo '{}'
  exit 0
fi

# Extract session ID and last user turn UUID for progressive disclosure anchors
SESSION_ID=$(basename "$TRANSCRIPT_PATH" .jsonl)
LAST_USER_TURN_UUID=$(python3 -c "
import json, sys
uuid = ''
with open(sys.argv[1]) as f:
    for line in f:
        try:
            obj = json.loads(line)
            if obj.get('type') == 'user' and isinstance(obj.get('message', {}).get('content'), str):
                uuid = obj.get('uuid', '')
        except: pass
print(uuid)
" "$TRANSCRIPT_PATH" 2>/dev/null || true)

# Use claude -p to summarize the parsed transcript into concise bullet points.
# --model haiku: cheap and fast model for summarization
# --no-session-persistence: don't save this throwaway session to disk
# --no-chrome: skip browser integration
# --system-prompt: separate role instructions from data (transcript via stdin)
SUMMARY=""
if command -v claude &>/dev/null; then
  SUMMARY=$(printf '%s' "$PARSED" | claude -p \
    --model haiku \
    --no-session-persistence \
    --no-chrome \
    --system-prompt "You are a session memory writer. Your ONLY job is to output bullet-point summaries. Output NOTHING else — no greetings, no questions, no offers to help, no preamble, no closing remarks.

Rules:
- Output 3-8 bullet points, each starting with '- '
- Focus on: decisions made, problems solved, code changes, key findings
- Be specific and factual — mention file names, function names, and concrete details
- Do NOT include timestamps, headers, or any formatting beyond bullet points
- Do NOT add any text before or after the bullet points" \
    2>/dev/null || true)
fi

# If claude is not available or returned empty, fall back to raw parsed output
if [ -z "$SUMMARY" ]; then
  SUMMARY="$PARSED"
fi

# Write through the new memory API with dedup + user isolation.
cmd=(memory write --stdin --source auto/stop-hook)
if [ -n "$SESSION_ID" ]; then
  cmd+=(--session-id "$SESSION_ID")
fi
if [ -n "$LAST_USER_TURN_UUID" ]; then
  cmd+=(--turn-id "$LAST_USER_TURN_UUID")
fi
if [ -n "$TRANSCRIPT_PATH" ]; then
  cmd+=(--transcript-path "$TRANSCRIPT_PATH")
fi
printf '%s' "$SUMMARY" | run_memsearch "${cmd[@]}"

echo '{}'
