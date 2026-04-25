#!/usr/bin/env bash
# claude-radar installer.
#
# Usage:
#   bash install.sh                    # install into $HOME/.claude-radar
#   CLAUDE_RADAR_HOME=/opt/cr install.sh
#   bash install.sh --settings /tmp/settings.json   # dev / CI
#
# Idempotent: re-running pulls latest, re-injects hooks (no duplicates).

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${CLAUDE_RADAR_HOME:-$HOME/.claude-radar}"
SETTINGS_FILE="$HOME/.claude/settings.json"

usage() {
    cat <<EOF
Usage: install.sh [--settings PATH] [--install-dir PATH] [--no-hooks]

Options:
  --settings PATH       Claude Code settings file to inject hooks into
                        (default: \$HOME/.claude/settings.json).
  --install-dir PATH    Where to install claude-radar
                        (default: \$CLAUDE_RADAR_HOME or \$HOME/.claude-radar).
  --no-hooks            Install files only; don't touch any settings.json.
  -h, --help            Show this help.
EOF
}

NO_HOOKS=0
while [ $# -gt 0 ]; do
    case "$1" in
        --settings)
            SETTINGS_FILE="$2"; shift 2 ;;
        --install-dir)
            INSTALL_DIR="$2"; shift 2 ;;
        --no-hooks)
            NO_HOOKS=1; shift ;;
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

mkdir -p "$INSTALL_DIR"

# Copy this checkout into the install dir if they differ. We don't use git
# clone here so the installer works from a tarball / curl-piped script too.
if [ "$SCRIPT_DIR" != "$INSTALL_DIR" ]; then
    echo "→ syncing files to $INSTALL_DIR"
    # Use cp -R; rsync is not always available on macOS without Homebrew.
    cp -R "$SCRIPT_DIR/bin" "$INSTALL_DIR/"
    cp -R "$SCRIPT_DIR/hooks" "$INSTALL_DIR/"
    cp -R "$SCRIPT_DIR/claude_radar" "$INSTALL_DIR/"
    cp -R "$SCRIPT_DIR/install" "$INSTALL_DIR/"
    cp "$SCRIPT_DIR/install.sh"   "$INSTALL_DIR/" 2>/dev/null || true
    cp "$SCRIPT_DIR/uninstall.sh" "$INSTALL_DIR/" 2>/dev/null || true
    cp "$SCRIPT_DIR/LICENSE" "$INSTALL_DIR/" 2>/dev/null || true
    cp "$SCRIPT_DIR/README.md" "$INSTALL_DIR/" 2>/dev/null || true
    cp "$SCRIPT_DIR/README.zh-CN.md" "$INSTALL_DIR/" 2>/dev/null || true
    cp "$SCRIPT_DIR/pyproject.toml" "$INSTALL_DIR/" 2>/dev/null || true
fi

chmod +x "$INSTALL_DIR/bin/claude-radar" "$INSTALL_DIR/bin/claude-radar-status" \
         "$INSTALL_DIR/hooks/state-tracker.sh" \
         "$INSTALL_DIR/install.sh" "$INSTALL_DIR/uninstall.sh" 2>/dev/null || true

if [ "$NO_HOOKS" -eq 0 ]; then
    if [ -f "$SETTINGS_FILE" ]; then
        BACKUP="${SETTINGS_FILE}.backup-$(date +%s)"
        cp "$SETTINGS_FILE" "$BACKUP"
        echo "→ backed up $SETTINGS_FILE → $BACKUP"
    fi
    echo "→ injecting hooks into $SETTINGS_FILE"
    "$PYTHON_BIN" "$INSTALL_DIR/install/inject-hooks.py" \
        --settings "$SETTINGS_FILE" \
        --install-dir "$INSTALL_DIR"
fi

cat <<EOF

✓ claude-radar installed to $INSTALL_DIR

Next steps:

  1. Add the bin dir to your PATH:

       export PATH="$INSTALL_DIR/bin:\$PATH"

     (Append this line to ~/.zshrc or ~/.bashrc.)

  2. Restart any running Claude Code sessions so the new hooks load.

  3. In a new terminal, run:

       claude-radar

EOF
