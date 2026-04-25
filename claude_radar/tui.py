"""Curses-based dashboard for claude-radar.

The TUI is intentionally tiny: it wakes up every ``refresh_seconds`` seconds,
re-reads every state file (cheap — one tiny JSON per session), and asks
``claude_radar.render.render_board`` to format the board. All formatting,
sorting, and width handling lives in ``render``; this module only deals with
drawing and key bindings.

Key bindings:
    q, Q, Esc      quit
    r, R           refresh immediately
    c, C           cleanup state files older than 24h
"""

from __future__ import annotations

import curses
import select
import sys
from datetime import datetime, timezone
from typing import Optional

from . import render, state

REFRESH_SECONDS = 2.0
KEY_ESC = 27


def _safe_addstr(win: "curses._CursesWindow", y: int, x: int, text: str, attr: int = 0) -> None:
    """``win.addstr`` that swallows curses errors at the bottom-right corner."""
    try:
        win.addstr(y, x, text, attr)
    except curses.error:
        # Drawing into the last column of the last row raises; ignore.
        pass


def _color_for(status: str) -> int:
    """Return a curses color-pair index for a status, or 0 if colors disabled."""
    if not curses.has_colors():
        return 0
    if status == render.STATUS_WAITING:
        return curses.color_pair(1) | curses.A_BOLD
    if status == render.STATUS_WORKING:
        return curses.color_pair(2)
    return curses.color_pair(3)


def _init_colors() -> None:
    if not curses.has_colors():
        return
    curses.start_color()
    try:
        curses.use_default_colors()
        bg = -1
    except curses.error:
        bg = curses.COLOR_BLACK
    curses.init_pair(1, curses.COLOR_RED, bg)      # waiting
    curses.init_pair(2, curses.COLOR_YELLOW, bg)   # working
    curses.init_pair(3, curses.COLOR_WHITE, bg)    # idle / chrome


def _draw(stdscr: "curses._CursesWindow", *, status_msg: str = "") -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    raw_states = state.list_states()
    now = datetime.now(timezone.utc).astimezone()
    rows = render.render_board(raw_states, width=width, height=height, now=now)

    # First row: header (chrome).
    _safe_addstr(stdscr, 0, 0, rows[0], _color_for(render.STATUS_IDLE))
    # Body rows: each starts with an emoji whose color we want to match the status.
    views = render.derive_views(raw_states, now=now)
    body_rows = rows[2 : -1]  # skip header + blank, exclude footer
    for i, row in enumerate(body_rows):
        attr = 0
        if i < len(views):
            attr = _color_for(views[i].status)
        _safe_addstr(stdscr, 2 + i, 0, row, attr)
    # Blank separator (already empty).
    _safe_addstr(stdscr, 1, 0, rows[1])
    # Footer.
    footer_attr = curses.A_DIM if curses.has_colors() else 0
    _safe_addstr(stdscr, height - 1, 0, rows[-1], footer_attr)
    if status_msg:
        # Overlay status message on the footer line.
        _safe_addstr(stdscr, height - 1, 0, status_msg.ljust(width)[:width], curses.A_REVERSE)
    stdscr.refresh()


def _read_key(stdscr: "curses._CursesWindow", timeout_ms: int) -> int:
    """Wait up to ``timeout_ms`` for a keypress; return -1 on timeout."""
    stdscr.timeout(timeout_ms)
    try:
        return stdscr.getch()
    except KeyboardInterrupt:
        return ord("q")


def _loop(stdscr: "curses._CursesWindow", refresh_seconds: float) -> None:
    curses.curs_set(0)
    _init_colors()
    stdscr.nodelay(False)

    status_msg = ""
    timeout_ms = int(refresh_seconds * 1000)
    while True:
        _draw(stdscr, status_msg=status_msg)
        status_msg = ""
        ch = _read_key(stdscr, timeout_ms)
        if ch in (ord("q"), ord("Q"), KEY_ESC):
            return
        if ch in (ord("r"), ord("R")):
            continue  # falls through to redraw
        if ch in (ord("c"), ord("C")):
            removed = state.cleanup_idle(max_age_seconds=24 * 3600)
            status_msg = f" cleaned up {removed} idle session(s) "
            continue
        if ch == curses.KEY_RESIZE:
            continue
        # any other key: just refresh on next tick


def run(refresh_seconds: float = REFRESH_SECONDS) -> int:
    """Entrypoint: blocks until the user quits. Returns process exit code."""
    if not sys.stdout.isatty():
        print("claude-radar requires an interactive terminal", file=sys.stderr)
        return 2
    try:
        curses.wrapper(_loop, refresh_seconds)
    except KeyboardInterrupt:
        pass
    return 0


# Re-export so ``select`` is not flagged unused; some platforms need it imported
# for curses input on slow ttys.
_ = select
