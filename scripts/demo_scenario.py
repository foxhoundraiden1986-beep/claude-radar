#!/usr/bin/env python3
"""Simulated demo scenario for asciinema recording.

Creates 3 mock sessions and walks them through state transitions:
  waiting -> working -> reviewing (waiting)

Each transition pauses briefly so the recording is readable.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Ensure the package is importable from the repo root.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from claude_radar import render, state

# Use a temporary state dir so we don't pollute real data.
DEMO_HOME = os.environ.get("CLAUDE_RADAR_HOME", "/tmp/radar-demo")
os.environ["CLAUDE_RADAR_HOME"] = DEMO_HOME

BOARD_WIDTH = 80
BOARD_HEIGHT = 14
PAUSE = 1.5


def _print_board(label):
    """Print a labelled snapshot of the board."""
    print(f"\n\033[1;36m--- {label} ---\033[0m\n")
    rows = render.render_board(state.list_states(), width=BOARD_WIDTH, height=BOARD_HEIGHT)
    for row in rows:
        print(row)
    print()


def _print_status():
    compact = render.render_compact(state.list_states())
    verbose = render.render_compact(state.list_states(), verbose=True)
    print(f"  compact : {compact}")
    print(f"  verbose : {verbose}")


def main():
    # Clean start.
    state.reset_all()
    state.ensure_state_dir()

    _print_board("Empty board (no sessions yet)")
    time.sleep(PAUSE)

    # Phase 1: 3 sessions come online, all waiting.
    print("\033[1;33m>> Phase 1: Sessions come online\033[0m")
    state.set_state("data", "waiting", task="", tmux_session="data", cwd="/tuhu/data-analysis-kit")
    time.sleep(0.3)
    state.set_state("dev", "waiting", task="", tmux_session="dev", cwd="/tuhu/claude-radar")
    time.sleep(0.3)
    state.set_state("meta", "waiting", task="", tmux_session="meta", cwd="/tuhu")
    _print_board("3 sessions waiting for user input")
    _print_status()
    time.sleep(PAUSE)

    # Phase 2: User sends prompts; sessions start working.
    print("\033[1;33m>> Phase 2: Sessions start working\033[0m")
    state.set_state("data", "working", task="Q2 revenue attribution", tmux_session="data")
    time.sleep(0.5)
    state.set_state("dev", "working", task="Build TUI dashboard", tmux_session="dev")
    _print_board("data + dev working, meta still waiting")
    _print_status()
    time.sleep(PAUSE)

    # Phase 3: data finishes, dev still going, meta starts.
    print("\033[1;33m>> Phase 3: Tasks progress\033[0m")
    state.set_state("data", "waiting", task="Q2 revenue attribution", tmux_session="data")
    state.set_state("meta", "working", task="Update agent rules", tmux_session="meta")
    _print_board("data done (waiting), dev + meta working")
    _print_status()
    time.sleep(PAUSE)

    # Phase 4: All sessions back to waiting.
    print("\033[1;33m>> Phase 4: All sessions idle down\033[0m")
    state.set_state("dev", "waiting", task="Build TUI dashboard", tmux_session="dev")
    state.set_state("meta", "waiting", task="Update agent rules", tmux_session="meta")
    _print_board("All sessions waiting")
    _print_status()
    time.sleep(PAUSE)

    # Cleanup.
    print("\033[1;33m>> Cleanup\033[0m")
    n = state.reset_all()
    print(f"  Removed {n} state file(s). Demo complete.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
