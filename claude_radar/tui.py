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
    ↑/k, ↓/j       move selection
    ⏎ / Enter      jump to selected tmux session (switch-client / attach hint)
"""

from __future__ import annotations

import curses
import os
import select
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from typing import List, Optional

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


def _draw(
    stdscr: "curses._CursesWindow",
    *,
    status_msg: str = "",
    selected_index: int = 0,
) -> List["render.SessionView"]:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    raw_states = state.list_states()
    now = datetime.now(timezone.utc).astimezone()
    rows = render.render_board(raw_states, width=width, height=height, now=now)
    views = render.derive_views(raw_states, now=now)

    # First row: header (chrome).
    _safe_addstr(stdscr, 0, 0, rows[0], _color_for(render.STATUS_IDLE))
    # Body rows: each starts with an emoji whose color we want to match the status.
    body_rows = rows[2 : -1]  # skip header + blank, exclude footer
    for i, row in enumerate(body_rows):
        attr = 0
        if i < len(views):
            attr = _color_for(views[i].status)
        if views and i == selected_index:
            attr |= curses.A_REVERSE
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
    return views


def _tmux_switch(target: str, *, client: Optional[str] = None) -> str:
    """Run ``tmux switch-client`` and return a status message."""
    args = ["tmux", "switch-client"]
    if client:
        args += ["-c", client]
    args += ["-t", target]
    try:
        subprocess.run(args, check=True, capture_output=True, text=True, timeout=3)
        if client:
            return f" switched {client} → {target} "
        return f" switched to {target} "
    except subprocess.CalledProcessError as e:
        err = (e.stderr or "").strip().splitlines()[-1:] or [""]
        return f" tmux: {err[0]} "
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return " tmux switch-client failed "


def _list_tmux_clients() -> List[str]:
    """Return names of currently attached tmux clients (e.g. ``/dev/ttys003``)."""
    try:
        r = subprocess.run(
            ["tmux", "list-clients", "-F", "#{client_name}"],
            check=True, capture_output=True, text=True, timeout=3,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return []
    return [line for line in r.stdout.splitlines() if line.strip()]


def _jump_to(view: "render.SessionView") -> str:
    """Jump the user to ``view``'s tmux session. Return a footer status message.

    Three cases:
    * Inside tmux (``$TMUX`` set): switch our own client.
    * Outside tmux but another tmux client is attached (typical sidebar setup
      — the dashboard runs in a standalone window while the user is in a
      separate Terminal/iTerm window attached to tmux): pick the first
      attached client and tell *it* to switch. The dashboard window stays put;
      the other window jumps.
    * Outside tmux with no client attached: surface ``tmux attach`` command.
    Non-tmux session ids (``tty-*`` / ``pid-*``): nothing actionable.
    """
    if shutil.which("tmux") is None:
        return " tmux not on PATH — can't jump "
    if not view.tmux_session:
        return f" '{view.session_id}' is not a tmux session — can't jump "
    target = view.tmux_session
    if os.environ.get("TMUX"):
        return _tmux_switch(target)
    clients = _list_tmux_clients()
    if not clients:
        return f" no tmux client attached — run: tmux attach -t {target} "
    # Prefer the first attached client. Most users have a single foreground
    # tmux window — picking #0 is fine and predictable.
    return _tmux_switch(target, client=clients[0])


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
    stdscr.keypad(True)  # decode arrow keys to curses.KEY_UP / KEY_DOWN
    stdscr.nodelay(False)

    status_msg = ""
    selected_index = 0
    timeout_ms = int(refresh_seconds * 1000)
    while True:
        views = _draw(stdscr, status_msg=status_msg, selected_index=selected_index)
        status_msg = ""
        # Clamp selection in case sessions appeared / disappeared since last tick.
        n = len(views)
        if n == 0:
            selected_index = 0
        else:
            selected_index = max(0, min(selected_index, n - 1))
        ch = _read_key(stdscr, timeout_ms)
        if ch in (ord("q"), ord("Q"), KEY_ESC):
            return
        if ch in (ord("r"), ord("R")):
            continue  # falls through to redraw
        if ch in (ord("c"), ord("C")):
            removed = state.cleanup_idle(max_age_seconds=24 * 3600)
            status_msg = f" cleaned up {removed} idle session(s) "
            continue
        if ch in (curses.KEY_UP, ord("k")):
            if n:
                selected_index = (selected_index - 1) % n
            continue
        if ch in (curses.KEY_DOWN, ord("j")):
            if n:
                selected_index = (selected_index + 1) % n
            continue
        if ch in (curses.KEY_ENTER, 10, 13):
            if n:
                status_msg = _jump_to(views[selected_index])
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
