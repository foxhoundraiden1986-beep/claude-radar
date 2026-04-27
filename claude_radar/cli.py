"""Command-line entry points for claude-radar.

Two scripts are exposed:

* ``claude-radar`` (``main``)         -- launches the curses TUI.
* ``claude-radar-status`` (``status_main``) -- one-shot stdout, for
  tmux ``status-right`` and similar non-interactive users.
"""

from __future__ import annotations

import argparse
import sys
from typing import Iterable, Optional

from . import __version__, render, state, tui


# ---------- claude-radar (TUI) ---------------------------------------------


def _build_radar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-radar",
        description="Real-time dashboard for multiple Claude Code sessions.",
    )
    parser.add_argument(
        "--refresh",
        type=float,
        default=tui.REFRESH_SECONDS,
        help="Refresh interval in seconds (default 2.0).",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete every state file and exit (use to recover from stuck sessions).",
    )
    parser.add_argument(
        "--forget",
        metavar="SESSION",
        default=None,
        help="Delete the state file for a single session and exit.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Print one snapshot of the board to stdout and exit (no curses).",
    )
    parser.add_argument(
        "--version", action="version", version=f"claude-radar {__version__}"
    )
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = _build_radar_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.reset:
        n = state.reset_all()
        print(f"removed {n} state file(s)")
        return 0
    if args.forget:
        path = state.state_path(args.forget)
        if not path.exists():
            print(f"no state for session {args.forget!r}", file=sys.stderr)
            return 1
        path.unlink()
        print(f"forgot {args.forget}")
        return 0
    if args.once:
        rows = render.render_board(state.list_states(), width=80, height=20)
        sys.stdout.write("\n".join(rows) + "\n")
        return 0
    if args.refresh <= 0:
        parser.error("--refresh must be positive")
    return tui.run(refresh_seconds=args.refresh)


# ---------- claude-radar-status (one-shot) ---------------------------------


def _build_status_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-radar-status",
        description="One-shot status string for tmux statusline.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="List each session with its task and age (default: counts only).",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=6,
        help="Verbose mode only: show at most N sessions (default 6).",
    )
    return parser


def status_main(argv: Optional[Iterable[str]] = None) -> int:
    parser = _build_status_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    line = render.render_compact(
        state.list_states(),
        verbose=args.verbose,
        max_items=max(1, args.max_items),
    )
    sys.stdout.write(line + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
