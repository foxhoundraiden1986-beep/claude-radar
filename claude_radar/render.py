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

# Two idle thresholds — by status, not by a single number.
#
# WORKING: a working session sitting on the same status for hours is almost
# certainly stale (Claude crashed, terminal closed). Threshold is generous
# because long-running compiles and analyses routinely run tens of minutes
# and we don't want to lie about an actually-active task.
#
# WAITING: the session has already pinged the user. After a short while of
# no response it stops being a "look at me" alert and becomes background
# noise — keeping it red forever clutters the board. Older waiting sessions
# fade to idle so the user only sees fresh asks at full intensity.
DEFAULT_WORKING_IDLE_AFTER_SECONDS = 6 * 3600    # 6 h
DEFAULT_WAITING_IDLE_AFTER_SECONDS = 30 * 60     # 30 min
# Backwards-compat alias for callers that pass a single ``idle_after_seconds``.
# When supplied, it overrides the working threshold (the historical contract).
DEFAULT_IDLE_AFTER_SECONDS = DEFAULT_WORKING_IDLE_AFTER_SECONDS


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


# Sub-agents (Task tool) and Skills are invoked with a long boilerplate prompt
# of the form "You are a <role>. Your job is to ..." (English) or
# "你是一个<角色>。" (Chinese). Claude Code fires UserPromptSubmit with that
# whole prompt as the payload, so the hook records it verbatim. Showing it on
# the board wastes width and misleads — the user sees prose that looks like
# their own message but isn't. Collapse it to ``[sub-agent] <role>``.
# Sub-agent / Skill prompt suppression lives in the hook (state-tracker.sh).
# The render layer now trusts whatever current_task is on disk and shows it
# verbatim — if you see a sub-agent prompt leak through, the fix belongs in
# the hook's filter, not here. State files written before the hook fix can
# be cleaned with `claude-radar --reset`.


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
    tmux_session: Optional[str] = None  # for jump-to-session; None if non-tmux


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
    if raw.get("ignored"):
        # User explicitly muted this session via the dashboard. Honour that
        # until the next real status change resets the flag (state.set_state
        # handles the auto-clear).
        status = STATUS_IDLE
    elif raw_status == STATUS_WORKING and age >= idle_after_seconds:
        status = STATUS_IDLE
    elif raw_status == STATUS_WAITING and age >= DEFAULT_WAITING_IDLE_AFTER_SECONDS:
        status = STATUS_IDLE
    tmux = raw.get("tmux_session")
    tmux = str(tmux) if tmux else None
    return SessionView(
        session_id=sid,
        status=status,
        raw_status=raw_status,
        task=task,
        age_seconds=age,
        started_at=started,
        tmux_session=tmux,
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


def _emoji_cell(status: str, slot_width: int = 2) -> str:
    """Return the status emoji padded so every row's first column is aligned.

    Wide emojis (``💬``, ``⚡``) already take ``slot_width`` cells; narrow
    fallbacks (``○``) get right-padded with spaces so rows still line up.
    """
    e = EMOJI.get(status, "?")
    return pad_display(e, slot_width)


def _format_status_counts(views: Sequence[SessionView]) -> str:
    """Format ``views`` as ``💬N ⚡N ○N`` (only non-zero buckets)."""
    if not views:
        return ""
    counts = {STATUS_WAITING: 0, STATUS_WORKING: 0, STATUS_IDLE: 0}
    for v in views:
        counts[v.status] = counts.get(v.status, 0) + 1
    parts = []
    for st in (STATUS_WAITING, STATUS_WORKING, STATUS_IDLE):
        if counts.get(st):
            parts.append(f"{EMOJI[st]}{counts[st]}")
    return " ".join(parts)


def _wrap_to_width(text: str, width: int) -> List[str]:
    """Break text into chunks each at most ``width`` display cells (CJK-safe).

    Mirrors the per-character width logic of ``_display_width``: combining
    marks count as 0, East-Asian wide / fullwidth / ambiguous as 2, the rest
    as 1. Returns at least one element (the empty string for empty input)
    so callers can always treat it as a non-empty list.
    """
    if width <= 0 or not text:
        return [""]
    out: List[str] = []
    cur = ""
    cur_w = 0
    for ch in text:
        cat = unicodedata.category(ch)
        if cat.startswith("M"):
            cw = 0
        elif unicodedata.east_asian_width(ch) in ("W", "F", "A"):
            cw = 2
        else:
            cw = 1
        if cur_w + cw > width:
            out.append(cur)
            cur = ch
            cur_w = cw
        else:
            cur += ch
            cur_w += cw
    if cur:
        out.append(cur)
    return out or [""]


def board_column_widths(width: int) -> Tuple[int, int]:
    """Return (name_width, task_width) for a given total board width.

    Layout per body row: ``emoji(2)  name  task  age(6)`` — three 2-cell
    gaps, so the fixed overhead is 2 + 2*3 + 6 = 14 cells. The col header
    uses the same numbers so columns line up cell-for-cell.
    """
    emoji_slot = 2
    gap = 2
    age_slot = 6
    fixed = emoji_slot + 3 * gap + age_slot  # = 14
    avail = max(10, width) - fixed
    if avail < 10:
        avail = max(10, width - 6)
    name_width = max(6, min(18, avail // 3))
    task_width = max(6, avail - name_width)
    return name_width, task_width


def view_line_count(view: SessionView, task_width: int) -> int:
    """How many body rows ``view`` will occupy at the given ``task_width``."""
    if view.status == STATUS_IDLE:
        return 1
    text = (view.task or "").strip() or "-"
    return max(1, len(_wrap_to_width(text, task_width)))


def _board_view_lines(view: SessionView, name_width: int, task_width: int) -> List[str]:
    """Render ``view`` as a list of body rows; long tasks wrap onto extras."""
    emoji = _emoji_cell(view.status)
    name = pad_display(truncate_display(view.session_id, name_width), name_width)
    if view.status == STATUS_IDLE:
        chunks = ["-"]
        age = ""
    else:
        text = (view.task or "").strip() or "-"
        chunks = _wrap_to_width(text, task_width)
        age = format_duration(view.age_seconds)
    blank_emoji = pad_display("", 2)
    blank_name = pad_display("", name_width)
    blank_age = pad_display("", 6)
    lines: List[str] = []
    for i, chunk in enumerate(chunks):
        task_cell = pad_display(chunk, task_width)
        if i == 0:
            line = f"{emoji}  {name}  {task_cell}  {age}".rstrip()
        else:
            line = f"{blank_emoji}  {blank_name}  {task_cell}  {blank_age}".rstrip()
        lines.append(line)
    return lines


@dataclass
class BoardLayout:
    """Full board output plus metadata the TUI needs for selection."""

    rows: List[str]
    body_start: int  # row index in ``rows`` where the body begins
    body_owners: List[Optional[int]]  # for each body row, which view index


def render_board_layout(
    raw_states: Sequence[Dict[str, Any]],
    *,
    width: int,
    height: int,
    now: Optional[datetime] = None,
    idle_after_seconds: int = DEFAULT_IDLE_AFTER_SECONDS,
    title: str = "Claude Sessions",
) -> BoardLayout:
    """Render the board and return rows + body→view mapping.

    See :func:`render_board` for the simpler wrapper that drops the metadata.
    """
    width = max(20, int(width))
    height = max(3, int(height))

    now = _now(now)
    views = derive_views(raw_states, now=now, idle_after_seconds=idle_after_seconds)

    # Header layout (with status counts when there are sessions):
    #   ╭─ <title> ──── 💬N ⚡N ○N ──── HH:MM ─╮
    # When no sessions exist, the counts block is omitted:
    #   ╭─ <title> ─────────────────────── HH:MM ─╮
    clock = now.strftime("%H:%M")
    counts_text = _format_status_counts(views)
    inner_left = f" {title} "
    inner_right = f" {clock} "
    left_w = _display_width(inner_left)
    right_w = _display_width(inner_right)
    if counts_text:
        center = f" {counts_text} "
        center_w = _display_width(center)
        dashes_total = max(4, width - left_w - center_w - right_w - 4)
        dashes_left = dashes_total // 2
        dashes_right = dashes_total - dashes_left
        header = (
            f"╭{'─'}{inner_left}{'─' * dashes_left}"
            f"{center}{'─' * dashes_right}{inner_right}{'─'}╮"
        )
    else:
        dashes = max(2, width - left_w - right_w - 4)
        header = f"╭{'─'}{inner_left}{'─' * dashes}{inner_right}{'─'}╮"
    header = truncate_display(header, width)
    header = pad_display(header, width)

    # Column header — exact 4-space left margin (emoji slot + first gap),
    # then session/task/age aligned cell-for-cell with body rows.
    name_width, task_width = board_column_widths(width)
    emoji_slot = 2
    gap = 2
    age_slot = 6
    col_header = (
        " " * (emoji_slot + gap)
        + pad_display("session", name_width)
        + " " * gap
        + pad_display("task", task_width)
        + " " * gap
        + pad_display("age", age_slot)
    )
    col_header = pad_display(truncate_display(col_header, width), width)

    # Thin separator under the column header — gives the body a clear visual
    # baseline without stealing attention. Drawn dim by the TUI.
    separator = pad_display(truncate_display("─" * width, width), width)
    rows: List[str] = [header, col_header, separator]
    # Maps each body row index (0-based within body) to its view index, or
    # None for the "+N more" trailer. The TUI uses this for selection
    # highlighting that spans wrapped task lines.
    body_owners: List[Optional[int]] = []

    if not views:
        empty_msg = "No active Claude Code sessions yet."
        rows.append(pad_display(truncate_display(empty_msg, width - 2), width))
        rows.append("")
        rows.append(
            pad_display(truncate_display("Hooks haven't fired yet — try sending a prompt.", width - 2), width)
        )
        body_owners.extend([None, None, None])
    else:
        # 1 chrome + 1 col header + 1 blank + 1 footer = 4 fixed rows.
        max_body = max(1, height - 4)
        body_buf: List[str] = []
        owners_buf: List[Optional[int]] = []
        truncated_at = -1
        for i, v in enumerate(views):
            v_lines = _board_view_lines(v, name_width, task_width)
            # Reserve 1 row for the "+N more" trailer if we'll truncate.
            cap = max_body - 1 if i < len(views) - 1 else max_body
            if len(body_buf) + len(v_lines) > cap:
                truncated_at = i
                break
            for ln in v_lines:
                body_buf.append(pad_display(truncate_display(ln, width), width))
                owners_buf.append(i)
        if truncated_at >= 0:
            remaining = len(views) - truncated_at
            body_buf.append(pad_display(f"… +{remaining} more", width))
            owners_buf.append(None)
        rows.extend(body_buf)
        body_owners.extend(owners_buf)

    # Pad to height-1 then append footer.
    while len(rows) < height - 1:
        rows.append(pad_display("", width))
    footer = "q quit · r refresh · c cleanup · ↑↓ select · ⏎ jump · i mute"
    rows.append(pad_display(truncate_display(footer, width), width))

    # Final clamp / truncation to exactly `height` rows.
    if len(rows) > height:
        rows = rows[:height - 1] + [rows[-1]]
    return BoardLayout(rows=rows, body_start=3, body_owners=body_owners)


def render_board(
    raw_states: Sequence[Dict[str, Any]],
    *,
    width: int,
    height: int,
    now: Optional[datetime] = None,
    idle_after_seconds: int = DEFAULT_IDLE_AFTER_SECONDS,
    title: str = "Claude Sessions",
) -> List[str]:
    """Return board rows sized to the given window. See :func:`render_board_layout`."""
    return render_board_layout(
        raw_states,
        width=width,
        height=height,
        now=now,
        idle_after_seconds=idle_after_seconds,
        title=title,
    ).rows


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
