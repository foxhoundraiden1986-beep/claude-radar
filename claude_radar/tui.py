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


# Color pair slots
_PAIR_WAITING = 1
_PAIR_WORKING = 2
_PAIR_IDLE = 3
_PAIR_CHROME = 4   # rounded box, separator
_PAIR_HEADER = 5   # column header (session/task/age)
_PAIR_SELECTED = 6  # selection background


def _color_for(status: str) -> int:
    """Return a curses attr for a status, or 0 if colors disabled."""
    if not curses.has_colors():
        if status == render.STATUS_WAITING:
            return curses.A_BOLD
        if status == render.STATUS_IDLE:
            return curses.A_DIM
        return 0
    if status == render.STATUS_WAITING:
        return curses.color_pair(_PAIR_WAITING) | curses.A_BOLD
    if status == render.STATUS_WORKING:
        return curses.color_pair(_PAIR_WORKING)
    return curses.color_pair(_PAIR_IDLE) | curses.A_DIM


def _init_colors() -> None:
    if not curses.has_colors():
        return
    curses.start_color()
    try:
        curses.use_default_colors()
        bg = -1
    except curses.error:
        bg = curses.COLOR_BLACK

    # Prefer 256-color codes — they bypass the user's 8-color palette which
    # most modern themes (Catppuccin, Rose Pine, Dracula, etc.) remap into a
    # tight tonal range, making COLOR_RED / YELLOW / WHITE indistinguishable.
    # Falls back to the basic palette for limited terminals (TERM=xterm,
    # tmux without -T xterm-256color, etc.).
    if curses.COLORS >= 256:
        c_waiting = 215   # warm amber — actionable but not alarming
        c_working = 81    # cool cyan — calmly progressing
        c_idle = 244      # mid grey — fades into the background
        c_chrome = 67     # muted blue — chrome / separator lines
        c_header = 248    # light grey — column header text
        c_sel_bg = 24     # deep teal background — selection
        c_sel_fg = 231    # off-white foreground — selection
    else:
        c_waiting = curses.COLOR_YELLOW
        c_working = curses.COLOR_CYAN
        c_idle = curses.COLOR_WHITE
        c_chrome = curses.COLOR_BLUE
        c_header = curses.COLOR_WHITE
        c_sel_bg = curses.COLOR_BLUE
        c_sel_fg = curses.COLOR_WHITE

    curses.init_pair(_PAIR_WAITING, c_waiting, bg)
    curses.init_pair(_PAIR_WORKING, c_working, bg)
    curses.init_pair(_PAIR_IDLE, c_idle, bg)
    curses.init_pair(_PAIR_CHROME, c_chrome, bg)
    curses.init_pair(_PAIR_HEADER, c_header, bg)
    curses.init_pair(_PAIR_SELECTED, c_sel_fg, c_sel_bg)


def _selection_attr(base_attr: int) -> int:
    """Compose a row attr with the selection background (foreground kept readable)."""
    if curses.has_colors():
        # Replace the color pair with selection's background; keep BOLD/DIM bits.
        no_color = base_attr & ~(curses.A_COLOR if hasattr(curses, "A_COLOR") else 0xFF00)
        return curses.color_pair(_PAIR_SELECTED) | curses.A_BOLD
    return base_attr | curses.A_REVERSE


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
    layout = render.render_board_layout(
        raw_states, width=width, height=height, now=now
    )
    rows = layout.rows
    views = render.derive_views(raw_states, now=now)

    # rows[0]=chrome, rows[1]=column header, rows[2]=separator, body, footer.
    chrome_attr = (
        curses.color_pair(_PAIR_CHROME) if curses.has_colors() else curses.A_DIM
    )
    header_attr = (
        curses.color_pair(_PAIR_HEADER) | curses.A_DIM
        if curses.has_colors()
        else curses.A_DIM
    )
    _safe_addstr(stdscr, 0, 0, rows[0], chrome_attr)
    _safe_addstr(stdscr, 1, 0, rows[1], header_attr)
    _safe_addstr(stdscr, 2, 0, rows[2], chrome_attr | curses.A_DIM)
    body_start = layout.body_start
    body_rows = rows[body_start : -1]
    for i, row in enumerate(body_rows):
        owner = layout.body_owners[i] if i < len(layout.body_owners) else None
        if owner is not None and owner < len(views):
            base = _color_for(views[owner].status)
            attr = _selection_attr(base) if owner == selected_index else base
        else:
            attr = 0
        _safe_addstr(stdscr, body_start + i, 0, row, attr)
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


def _spawn_attach_macos(target: str) -> Optional[str]:
    """On macOS, open a new Terminal/iTerm window running ``tmux attach -t``.

    Returns a status message on success, ``None`` if osascript is unavailable
    or fails (caller should fall back to a hint).
    """
    if sys.platform != "darwin" or shutil.which("osascript") is None:
        return None
    cmd = f"tmux attach -t {target}"
    if os.environ.get("TERM_PROGRAM") == "iTerm.app":
        script = (
            f'tell application "iTerm"\n'
            f'  create window with default profile command "{cmd}"\n'
            f'end tell'
        )
    else:
        script = (
            f'tell application "Terminal"\n'
            f'  activate\n'
            f'  do script "{cmd}"\n'
            f'end tell'
        )
    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=True, capture_output=True, text=True, timeout=5,
        )
        return f" opened new window → {target} "
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _jump_to(view: "render.SessionView") -> str:
    """Jump the user to ``view``'s tmux session. Return a footer status message.

    Resolution order:
    * Inside tmux (``$TMUX`` set): switch our own client.
    * Outside tmux but another tmux client is attached (typical sidebar
      setup): pick the first attached client and tell *it* to switch. The
      dashboard window stays put; the other window jumps.
    * Outside tmux with no client attached, on macOS: open a new
      Terminal/iTerm window running ``tmux attach -t <target>``.
    * Otherwise: surface the ``tmux attach`` command for the user.
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
    if clients:
        # Prefer the first attached client. Most users have a single foreground
        # tmux window — picking #0 is fine and predictable.
        return _tmux_switch(target, client=clients[0])
    spawned = _spawn_attach_macos(target)
    if spawned is not None:
        return spawned
    return f" no tmux client attached — run: tmux attach -t {target} "


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
