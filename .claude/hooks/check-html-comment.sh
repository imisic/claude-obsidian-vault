#!/bin/bash
# PostToolUse warning: flag HTML comments in vault files the agent just wrote.
# Never blocks (always exits 0). Reads the hook event JSON on stdin and handles
# both Claude edit tools (tool_input.file_path) and Codex apply_patch (patch
# text in tool_input.command).
set -uo pipefail

cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}" || exit 0

mapfile -t files < <(python3 -c '
import json, re, sys
try:
    event = json.load(sys.stdin)
except (json.JSONDecodeError, AttributeError):
    raise SystemExit(0)
tool_input = event.get("tool_input") or {}
if not isinstance(tool_input, dict):
    raise SystemExit(0)
candidates = []
file_path = tool_input.get("file_path")
if isinstance(file_path, str):
    candidates.append(file_path)
command = tool_input.get("command")
if isinstance(command, str):
    pattern = re.compile(r"^\*\*\* (?:Add|Update) File: (.+)$|^\*\*\* Move to: (.+)$")
    for line in command.splitlines():
        m = pattern.match(line)
        if m:
            candidates.append(m.group(1) or m.group(2))
seen = set()
for c in candidates:
    if c and c not in seen:
        seen.add(c)
        print(c)
')

for file in "${files[@]}"; do
  [ -f "$file" ] || continue
  if grep -q '<!--' "$file"; then
    echo "WARNING: HTML comment found in $file"
  fi
done
exit 0
