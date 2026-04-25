"""Pure rendering helpers for claude-radar.

This module turns a list of session-state dicts (as produced by
``claude_radar.state``) into:

* A compact one-line ``statusline`` string for tmux ``status-right``.
* A multi-line ``board`` ready to be drawn by the curses TUI.

Everything here is pure: no I/O, no curses, no time.sleep. The TUI passes
``now`` in so tests can pin the clock.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------- constants ------------------------------------------------------

STATUS_WAITING = "waiting"
STATUS_WORKING = "working"
STATUS_IDLE = "idle"

# Emojis used in the board / statusline. Kept as module constants so callers
# (and tests) can refer to them without re-typing literals.
EMOJI = {
    STATUS_WAITING: "💬",
    STATUS_WORKING: "⚡",
    STATUS_IDLE: "○",
}

# Status sort order: waiting first (most actionable), then working, then idle.
_STATUS_RANK = {STATUS_WAITING: 0, STATUS_WORKING: 1, STATUS_IDLE: 2}

# A working session whose status_changed_at is older than this is escalated
# to ``idle`` at render time — the hook never fired ``Stop`` and the task is
# almost certainly stale (Claude crashed, user closed the terminal, etc.).
# Long-running but still active tasks (compiles, big analyses) routinely run
# tens of minutes; the threshold here is intentionally generous so the
# dashboard does not lie about an actually-running task. The user story in
# the spec shows a 41-minute ``working`` session that should still render
# as working.
DEFAULT_IDLE_AFTER_SECONDS = 6 * 3600


# ---------- helpers --------------------------------------------------------


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp; return ``None`` on failure."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _now(now: Optional[datetime]) -> datetime:
    if now is None:
        return datetime.now(timezone.utc).astimezone()
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now


def _display_width(s: str) -> int:
    """Return the visual width of ``s`` in terminal cells.

    East-Asian wide and full-width characters count as 2; combining marks
    count as 0; everything else as 1. This is a deliberately small subset of
    ``wcwidth``; it is good enough for the board and keeps us stdlib-only.
    """
    width = 0
    for ch in s:
        if unicodedata.combining(ch):
            continue
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            width += 2
        else:
            width += 1
    return width


def truncate_display(s: str, max_width: int) -> str:
    """Truncate ``s`` so that its display width fits in ``max_width`` cells.

    A single-cell ellipsis (``…``) replaces the trimmed tail. If
    ``max_width <= 0`` returns an empty string.
    """
    if max_width <= 0:
        return ""
    if _display_width(s) <= max_width:
        return s
    if max_width == 1:
        return "…"
    out: List[str] = []
    used = 0
    for ch in s:
        w = 0 if unicodedata.combining(ch) else (
            2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        )
        if used + w > max_width - 1:  # leave room for the ellipsis
            break
        out.append(ch)
        used += w
    return "".join(out) + "…"


def pad_display(s: str, width: int) -> str:
    """Right-pad ``s`` (in display cells) to ``width``."""
    deficit = width - _display_width(s)
    if deficit <= 0:
        return s
    return s + (" " * deficit)


def format_duration(seconds: float) -> str:
    """Format a non-negative duration as ``Ns`` / ``Nm`` / ``Nh`` / ``Nh Nm``.

    Negative durations are clamped to 0. Examples:

        format_duration(7)        -> "7s"
        format_duration(95)       -> "1m"
        format_duration(13 * 60)  -> "13m"
        format_duration(2 * 3600) -> "2h"
        format_duration(2 * 3600 + 5 * 60) -> "2h5m"
    """
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    hours, rem = divmod(s, 3600)
    minutes = rem // 60
    if minutes == 0:
        return f"{hours}h"
    return f"{hours}h{minutes}m"


# ---------- normalised view ------------------------------------------------


@dataclass(frozen=True)
class SessionView:
    """A render-friendly snapshot derived from a raw state dict."""

    session_id: str
    status: str  # waiting | working | idle (after idle escalation)
    raw_status: str  # the value that was on disk before idle escalation
    task: str
    age_seconds: int  # how long status has been at this value
    started_at: Optional[datetime]


def derive_view(
    raw: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
    idle_after_seconds: int = DEFAULT_IDLE_AFTER_SECONDS,
) -> SessionView:
    """Build a ``SessionView`` from one raw state dict.

    The on-disk status is escalated to ``idle`` when ``status_changed_at`` is
    older than ``idle_after_seconds``. ``waiting`` sessions are *not*
    escalated — the user has explicitly been pinged and should stay flagged.
    """
    now = _now(now)
    raw_status = str(raw.get("status") or STATUS_IDLE)
    if raw_status not in _STATUS_RANK:
        raw_status = STATUS_IDLE
    sid = str(raw.get("session_id") or "?")
    task = str(raw.get("current_task") or "")

    started = _parse_iso(raw.get("status_changed_at"))
    age = 0 if started is None else max(0, int((now - started).total_seconds()))

    status = raw_status
    if raw_status == STATUS_WORKING and age >= idle_after_seconds:
        status = STATUS_IDLE
    return SessionView(
        session_id=sid,
        status=status,
        raw_status=raw_status,
        task=task,
        age_seconds=age,
        started_at=started,
    )


def derive_views(
    raw_states: Iterable[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    idle_after_seconds: int = DEFAULT_IDLE_AFTER_SECONDS,
) -> List[SessionView]:
    """Vectorised ``derive_view`` that also sorts the result.

    Sort order: ``_STATUS_RANK`` (waiting > working > idle), then descending
    age, then session id (stable tiebreak).
    """
    views = [
        derive_view(r, now=now, idle_after_seconds=idle_after_seconds) for r in raw_states
    ]
    views.sort(key=lambda v: (_STATUS_RANK.get(v.status, 99), -v.age_seconds, v.session_id))
    return views


# ---------- compact (one-line) ---------------------------------------------


def render_compact(
    raw_states: Sequence[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    verbose: bool = False,
    max_items: int = 6,
    idle_after_seconds: int = DEFAULT_IDLE_AFTER_SECONDS,
) -> str:
    """Return a one-line summary suitable for a tmux statusline.

    Default (compact) form, e.g. ``💬2 ⚡1 ○1``. ``verbose=True`` lists each
    session with its task and age, e.g. ``💬 data:归因 13m | ⚡ dev:重构 41m``.
    """
    views = derive_views(raw_states, now=now, idle_after_seconds=idle_after_seconds)
    if not views:
        return "○0"

    if not verbose:
        counts = {STATUS_WAITING: 0, STATUS_WORKING: 0, STATUS_IDLE: 0}
        for v in views:
            counts[v.status] = counts.get(v.status, 0) + 1
        parts = []
        for st in (STATUS_WAITING, STATUS_WORKING, STATUS_IDLE):
            if counts.get(st):
                parts.append(f"{EMOJI[st]}{counts[st]}")
        return " ".join(parts) if parts else "○0"

    parts: List[str] = []
    for v in views[:max_items]:
        emoji = EMOJI.get(v.status, "?")
        if v.status == STATUS_IDLE:
            chunk = f"{emoji} {v.session_id}"
        else:
            label = v.task.strip() or "-"
            label = truncate_display(label, 24)
            chunk = f"{emoji} {v.session_id}:{label} {format_duration(v.age_seconds)}"
        parts.append(chunk)
    if len(views) > max_items:
        parts.append(f"+{len(views) - max_items}")
    return " | ".join(parts)


# ---------- board (multi-line, for TUI) ------------------------------------


def _board_row(view: SessionView, name_width: int, task_width: int) -> str:
    emoji = EMOJI.get(view.status, "?")
    name = pad_display(truncate_display(view.session_id, name_width), name_width)
    if view.status == STATUS_IDLE:
        task = pad_display("-", task_width)
        age = ""
    else:
        task = pad_display(
            truncate_display(view.task.strip() or "-", task_width), task_width
        )
        age = format_duration(view.age_seconds)
    line = f"{emoji}  {name}  {task}  {age}".rstrip()
    return line


def render_board(
    raw_states: Sequence[Dict[str, Any]],
    *,
    width: int,
    height: int,
    now: Optional[datetime] = None,
    idle_after_seconds: int = DEFAULT_IDLE_AFTER_SECONDS,
    title: str = "Claude Sessions",
) -> List[str]:
    """Return a list of strings (one per row) sized for a curses window.

    The output is exactly ``height`` rows tall (padded with empty strings)
    and every row's display width is at most ``width``. The caller is
    responsible for actually drawing them.
    """
    width = max(20, int(width))
    height = max(3, int(height))

    now = _now(now)
    views = derive_views(raw_states, now=now, idle_after_seconds=idle_after_seconds)

    # Header: "─ <title> ─ HH:MM ─"
    clock = now.strftime("%H:%M")
    header_inner = f" {title} "
    # Build header that fits exactly `width` cells:
    pad = width - _display_width(header_inner) - _display_width(clock) - 2
    pad = max(2, pad)
    header = f"─{header_inner}{'─' * (pad - 1)} {clock} ─"
    header = truncate_display(header, width)
    header = pad_display(header, width)

    rows: List[str] = [header, ""]

    if not views:
        empty_msg = "No active Claude Code sessions yet."
        rows.append(pad_display(truncate_display(empty_msg, width - 2), width))
        rows.append("")
        rows.append(
            pad_display(truncate_display("Hooks haven't fired yet — try sending a prompt.", width - 2), width)
        )
    else:
        # Column widths: emoji(1 cell vis but 2 wide) + 2sp + name + 2sp + task + 2sp + age
        # Reserve: emoji slot = 2 cells (emoji + 1 space pad). Spaces between cols = 2.
        emoji_slot = 3
        age_slot = 6
        gap = 2
        avail = width - emoji_slot - age_slot - 2 * gap
        if avail < 10:
            avail = max(10, width - 6)
        name_width = max(6, min(18, avail // 3))
        task_width = max(6, avail - name_width)

        max_rows = height - 4  # 1 header + 1 blank + footer area (2 rows)
        for v in views[:max_rows]:
            line = _board_row(v, name_width, task_width)
            rows.append(pad_display(truncate_display(line, width), width))
        if len(views) > max_rows:
            rows.append(pad_display(f"… +{len(views) - max_rows} more", width))

    # Pad to height-1 then append footer.
    while len(rows) < height - 1:
        rows.append(pad_display("", width))
    footer = "q quit · r refresh · c cleanup"
    rows.append(pad_display(truncate_display(footer, width), width))

    # Final clamp / truncation to exactly `height` rows.
    if len(rows) > height:
        rows = rows[:height - 1] + [rows[-1]]
    return rows


# ---------- public exports -------------------------------------------------

__all__ = [
    "EMOJI",
    "DEFAULT_IDLE_AFTER_SECONDS",
    "SessionView",
    "derive_view",
    "derive_views",
    "format_duration",
    "pad_display",
    "render_board",
    "render_compact",
    "truncate_display",
]
