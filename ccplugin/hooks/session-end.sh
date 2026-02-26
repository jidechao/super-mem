#!/usr/bin/env bash
# SessionEnd hook: stop the memsearch watch singleton.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# On Windows, dispatch to native PowerShell hook.
if [[ "${OS:-}" == "Windows_NT" ]] && command -v powershell &>/dev/null; then
  exec powershell -ExecutionPolicy Bypass -File "$SCRIPT_DIR/session-end.ps1" "$@"
fi
source "$SCRIPT_DIR/common.sh"

stop_watch

exit 0
