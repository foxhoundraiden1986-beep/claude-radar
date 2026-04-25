#!/usr/bin/env bash
# claude-radar state-tracker hook.
#
# Wired into Claude Code's UserPromptSubmit / Stop / Notification hooks.
# Reads the hook payload from stdin (Claude Code hook protocol), derives a
# stable session id (tmux session name when available, ttyname otherwise),
# and delegates to ``python -m claude_radar.state set`` to update the JSON
# under $CLAUDE_RADAR_HOME/state.
#
# macOS bash 3.2 compatible: no associative arrays, no mapfile/readarray,
# no process substitution into arrays.

set -u  # don't die on individual errors — hooks must be best-effort.

HOOK_TYPE="${1:-}"
if [ -z "$HOOK_TYPE" ]; then
    echo "usage: state-tracker.sh <UserPromptSubmit|Stop|Notification>" >&2
    exit 64
fi

# Resolve the repo root: this file lives in <root>/hooks/.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Pick a python interpreter. Prefer python3; fall back to python.
if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
else
    # No python available — silently bail. We don't want hooks to break
    # Claude Code if the host has no python.
    exit 0
fi

# stdin payload (JSON). Capture once; may be empty for some hooks.
STDIN_JSON=""
if [ ! -t 0 ]; then
    STDIN_JSON="$(cat)"
fi

# --- session id derivation -------------------------------------------------
# Priority order:
#   1. Explicit override via $CLAUDE_RADAR_SESSION_ID (escape hatch).
#   2. Tmux session name (stable across pane reattaches).
#   3. Our own controlling tty via `ps -o tty= -p $$`. Unlike `tty`, this
#      reflects the *process's* controlling terminal, not stdin's, so it
#      survives Claude Code piping JSON into our stdin.
#   4. The controlling tty of our parent process — same value in practice
#      (Claude Code is the parent; we inherit its tty), but worth a second
#      look in case of a weird wrapper that breaks `ps`.
#   5. Falling all the way through, group by parent PID. We deliberately do
#      NOT use $$ here: $$ is the PID of the bash hook script and changes
#      on every invocation, which would create a fresh state file per
#      prompt and shatter the dashboard. $PPID is Claude Code itself,
#      which lives for the whole session.
SESSION_ID=""

if [ -n "${CLAUDE_RADAR_SESSION_ID:-}" ]; then
    SESSION_ID="$CLAUDE_RADAR_SESSION_ID"
fi

if [ -z "$SESSION_ID" ] && [ "${TMUX:-}" != "" ]; then
    SESSION_ID="$(tmux display-message -p '#S' 2>/dev/null || true)"
fi

# Helper: read the controlling tty of pid $1; print non-empty tty name or
# nothing. Filters macOS/BSD's "?"/"??" sentinels for "no controlling tty".
_radar_pid_tty() {
    _t="$(ps -o tty= -p "$1" 2>/dev/null | tr -d '[:space:]' || true)"
    if [ -n "$_t" ] && [ "$_t" != "?" ] && [ "$_t" != "??" ]; then
        printf '%s' "$_t"
    fi
}

if [ -z "$SESSION_ID" ]; then
    SELF_TTY="$(_radar_pid_tty "$$")"
    if [ -n "$SELF_TTY" ]; then
        SESSION_ID="tty-$SELF_TTY"
    fi
fi

if [ -z "$SESSION_ID" ] && [ -n "${PPID:-}" ]; then
    PARENT_TTY="$(_radar_pid_tty "$PPID")"
    if [ -n "$PARENT_TTY" ]; then
        SESSION_ID="tty-$PARENT_TTY"
    fi
fi

if [ -z "$SESSION_ID" ]; then
    # Last-resort stable id: $PPID is Claude Code's PID, which lives as
    # long as the session does, so all hooks of that session land in one
    # file. Critically, NOT $$ — that is per-invocation.
    SESSION_ID="pid-${PPID:-0}"
fi

TMUX_SESSION="${SESSION_ID}"
if [ "${TMUX:-}" = "" ]; then
    TMUX_SESSION=""
fi

# --- payload extraction (best-effort, via python helper) -------------------
# We use python here so we don't depend on jq. On extraction failure we
# still write the status update — task text is optional.
extract_field() {
    # $1 = JSON string, $2 = dotted key (only top-level supported)
    local key="$2"
    if [ -z "$1" ]; then
        return 0
    fi
    "$PYTHON_BIN" - "$key" <<'PY' "$1" 2>/dev/null || true
import json, sys
key = sys.argv[1]
try:
    data = json.loads(sys.argv[2])
except Exception:
    sys.exit(0)
val = data.get(key) if isinstance(data, dict) else None
if val is None:
    sys.exit(0)
if not isinstance(val, str):
    val = str(val)
# Trim to a sensible length for the dashboard.
print(val.strip()[:160])
PY
}

# extract_field reads JSON from $3 (here-doc trick: "$1" goes after PY EOF).
# Bash 3.2 accepts heredoc + extra positional args via this pattern; the
# subshell sees the heredoc on stdin and the keys + json as argv[1:].
USER_PROMPT=""
case "$HOOK_TYPE" in
    UserPromptSubmit)
        # The hook payload field is "prompt" per spec §6.1.
        USER_PROMPT="$(extract_field "$STDIN_JSON" prompt)"
        ;;
esac

CWD_NOW="$(pwd 2>/dev/null || true)"

# --- map hook type -> status -----------------------------------------------
case "$HOOK_TYPE" in
    UserPromptSubmit)
        STATUS="working"
        ;;
    Stop|Notification)
        STATUS="waiting"
        ;;
    *)
        # Unknown hook — log nothing, exit success so Claude Code is happy.
        exit 0
        ;;
esac

# --- write state ------------------------------------------------------------
ARGS=( -m claude_radar.state set
       --session "$SESSION_ID"
       --status "$STATUS"
       --cwd "$CWD_NOW" )

if [ -n "$TMUX_SESSION" ]; then
    ARGS=( "${ARGS[@]}" --tmux-session "$TMUX_SESSION" )
fi

if [ "$STATUS" = "working" ] && [ -n "$USER_PROMPT" ]; then
    ARGS=( "${ARGS[@]}" --task "$USER_PROMPT" )
fi

# Add the repo root to PYTHONPATH so ``python -m claude_radar.state`` works
# even when the package is not installed (the install layout has the package
# next to this script).
PYTHONPATH="${ROOT_DIR}${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON_BIN" "${ARGS[@]}" >/dev/null 2>&1 || true

exit 0
