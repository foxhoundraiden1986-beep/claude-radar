#!/usr/bin/env bash
# claude-radar uninstaller.
#
# Removes the hooks from Claude Code's settings.json and (optionally) the
# install directory itself. State files under $CLAUDE_RADAR_HOME/state/ are
# kept by default — pass --purge-state to wipe them too.

set -eu

INSTALL_DIR="${CLAUDE_RADAR_HOME:-$HOME/.claude-radar}"
SETTINGS_FILE="$HOME/.claude/settings.json"
PURGE=0
PURGE_STATE=0

usage() {
    cat <<EOF
Usage: uninstall.sh [--settings PATH] [--install-dir PATH] [--purge] [--purge-state]

Options:
  --settings PATH      settings.json to clean (default: \$HOME/.claude/settings.json).
  --install-dir PATH   install directory to remove (default: \$HOME/.claude-radar).
  --purge              also delete the install directory itself.
  --purge-state        also delete \$CLAUDE_RADAR_HOME/state/ JSON files.
  -h, --help           show this help.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --settings)
            SETTINGS_FILE="$2"; shift 2 ;;
        --install-dir)
            INSTALL_DIR="$2"; shift 2 ;;
        --purge)
            PURGE=1; shift ;;
        --purge-state)
            PURGE_STATE=1; shift ;;
        -h|--help)
            usage; exit 0 ;;
        *)
            echo "unknown argument: $1" >&2
            usage >&2
            exit 64
            ;;
    esac
done

PYTHON_BIN="$(command -v python3 || command -v python || true)"
if [ -z "$PYTHON_BIN" ]; then
    echo "error: python3 not found on PATH" >&2
    exit 1
fi

if [ -f "$SETTINGS_FILE" ]; then
    BACKUP="${SETTINGS_FILE}.backup-$(date +%s)"
    cp "$SETTINGS_FILE" "$BACKUP"
    echo "→ backed up $SETTINGS_FILE → $BACKUP"
fi

INJECT_SCRIPT=""
if [ -f "$INSTALL_DIR/install/inject-hooks.py" ]; then
    INJECT_SCRIPT="$INSTALL_DIR/install/inject-hooks.py"
elif [ -f "$(dirname "$0")/install/inject-hooks.py" ]; then
    INJECT_SCRIPT="$(dirname "$0")/install/inject-hooks.py"
fi

if [ -n "$INJECT_SCRIPT" ] && [ -f "$SETTINGS_FILE" ]; then
    "$PYTHON_BIN" "$INJECT_SCRIPT" --settings "$SETTINGS_FILE" --remove
else
    echo "→ skipping hook removal (no inject-hooks.py / settings.json found)"
fi

if [ "$PURGE_STATE" -eq 1 ]; then
    STATE_DIR="${CLAUDE_RADAR_HOME:-$HOME/.claude-radar}/state"
    if [ -d "$STATE_DIR" ]; then
        echo "→ removing state files in $STATE_DIR"
        rm -f "$STATE_DIR"/*.json
    fi
fi

if [ "$PURGE" -eq 1 ]; then
    if [ -d "$INSTALL_DIR" ]; then
        echo "→ removing $INSTALL_DIR"
        rm -rf "$INSTALL_DIR"
    fi
fi

echo "✓ uninstall complete"
