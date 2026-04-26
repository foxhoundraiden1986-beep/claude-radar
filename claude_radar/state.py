"""State storage for claude-radar.

Each Claude Code session has one JSON file under
``$CLAUDE_RADAR_HOME/state/<session_id>.json``. State is written atomically
via ``os.replace`` so partial writes never leak to the renderer.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# ---------- paths -----------------------------------------------------------

VALID_STATUS = ("working", "waiting", "idle")


def radar_home() -> Path:
    """Return the radar home directory (honours ``CLAUDE_RADAR_HOME``)."""
    env = os.environ.get("CLAUDE_RADAR_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".claude-radar"


def state_dir() -> Path:
    """Return the directory holding per-session state files."""
    return radar_home() / "state"


def ensure_state_dir() -> Path:
    """Create the state directory if needed and return it."""
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------- session id sanitisation ----------------------------------------

_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_session_id(session_id: str) -> str:
    """Make a session id safe to use as a file name."""
    cleaned = _SAFE_ID.sub("_", session_id.strip())
    if not cleaned:
        return "unknown"
    return cleaned[:120]


def state_path(session_id: str) -> Path:
    """Path of the state file for ``session_id``."""
    return state_dir() / f"{sanitize_session_id(session_id)}.json"


# ---------- low-level IO ----------------------------------------------------


def now_iso() -> str:
    """Return current local time in ISO-8601 with timezone offset."""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    """Write ``payload`` to ``path`` atomically via tmp file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".radar-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        # Best-effort cleanup of the tmp file on failure.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_state(session_id: str) -> Optional[Dict[str, Any]]:
    """Read state for ``session_id``; return ``None`` if missing or corrupt."""
    path = state_path(session_id)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        return data
    except (OSError, ValueError):
        # Corrupt or unreadable file: treat as missing rather than crashing.
        return None


def list_states() -> List[Dict[str, Any]]:
    """Return state dicts for all sessions, skipping unreadable files."""
    out: List[Dict[str, Any]] = []
    d = state_dir()
    if not d.exists():
        return out
    for child in sorted(d.iterdir()):
        if not child.is_file() or not child.name.endswith(".json"):
            continue
        try:
            with child.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


# ---------- mutation --------------------------------------------------------


def set_state(
    session_id: str,
    status: str,
    *,
    task: Optional[str] = None,
    tmux_session: Optional[str] = None,
    tty: Optional[str] = None,
    cwd: Optional[str] = None,
    timestamp: Optional[str] = None,
    via_tool: bool = False,
) -> Dict[str, Any]:
    """Update state for ``session_id`` and return the new payload.

    Old fields are preserved across transitions. Notable rules:

    * ``task_started_at`` is set when status becomes ``working`` and the
      previous status was not ``working``; otherwise the existing value is
      kept.
    * ``status_changed_at`` is updated only when ``status`` actually changes.
    * ``last_user_prompt_at`` is bumped on each ``working`` write.
    * ``last_assistant_stop_at`` is bumped on each ``waiting`` write.
    * The user-set ``ignored`` flag clears only on a real user prompt
      (``status="working"`` with ``via_tool=False``). Stop /
      Notification / PreToolUse / mid-turn oscillation all preserve it,
      so a mute set during one turn survives until the user actually
      engages with the session again.

    ``via_tool=True`` flags a PreToolUse-driven flip back to working: keep
    ``task_started_at`` / ``last_user_prompt_at`` / ``current_task`` from
    the original prompt so the dashboard's age clock doesn't reset every
    time the agent oscillates between Stop and the next tool call.
    """
    if status not in VALID_STATUS:
        raise ValueError(f"invalid status {status!r}; must be one of {VALID_STATUS}")
    ts = timestamp or now_iso()
    ensure_state_dir()
    prev = read_state(session_id) or {}
    payload: Dict[str, Any] = dict(prev)
    payload["session_id"] = session_id
    if tmux_session is not None:
        payload["tmux_session"] = tmux_session
    if tty is not None:
        payload["tty"] = tty
    if cwd is not None:
        payload["cwd"] = cwd

    prev_status = prev.get("status")
    payload["status"] = status

    if prev_status != status:
        payload["status_changed_at"] = ts
    else:
        payload.setdefault("status_changed_at", ts)

    if status == "working":
        if via_tool:
            # PreToolUse flip: preserve the prompt's task_started_at so the
            # dashboard age clock keeps counting from the original prompt.
            payload.setdefault("task_started_at", ts)
        else:
            if prev_status != "working" or "task_started_at" not in payload:
                payload["task_started_at"] = ts
            payload["last_user_prompt_at"] = ts
            # Real user prompt — that's the user re-engaging with this
            # session, so clear any mute set during the previous turn.
            # Stop / Notification / PreToolUse all preserve it.
            payload.pop("ignored", None)
        if task is not None:
            payload["current_task"] = task
    elif status == "waiting":
        payload["last_assistant_stop_at"] = ts
        if task is not None:
            payload["current_task"] = task
    else:  # idle (rarely written from hook; mostly derived in render)
        if task is not None:
            payload["current_task"] = task

    _atomic_write_json(state_path(session_id), payload)
    return payload


def reset_all() -> int:
    """Delete every state file. Return number of files removed."""
    d = state_dir()
    if not d.exists():
        return 0
    removed = 0
    for child in d.iterdir():
        if child.is_file() and child.name.endswith(".json"):
            try:
                child.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def set_ignored(session_id: str, ignored: bool = True) -> Optional[Dict[str, Any]]:
    """Toggle the ``ignored`` flag on an existing state file.

    Returns the updated payload, or ``None`` if the session has no state
    yet. The flag is consumed automatically the next time the hook detects
    a real status change (see ``set_state``).
    """
    prev = read_state(session_id)
    if not prev:
        return None
    payload = dict(prev)
    if ignored:
        payload["ignored"] = True
    else:
        payload.pop("ignored", None)
    _atomic_write_json(state_path(session_id), payload)
    return payload


def cleanup_idle(max_age_seconds: int = 24 * 3600, *, now: Optional[datetime] = None) -> int:
    """Delete state files whose ``status_changed_at`` is older than the cutoff.

    Returns the number of files removed. Files that are unparseable or that
    lack a usable timestamp are left untouched.
    """
    d = state_dir()
    if not d.exists():
        return 0
    if now is None:
        now = datetime.now(timezone.utc).astimezone()
    removed = 0
    for child in d.iterdir():
        if not (child.is_file() and child.name.endswith(".json")):
            continue
        try:
            with child.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        ts = data.get("status_changed_at") if isinstance(data, dict) else None
        if not ts:
            continue
        try:
            then = datetime.fromisoformat(ts)
        except ValueError:
            continue
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        age = (now - then).total_seconds()
        if age >= max_age_seconds:
            try:
                child.unlink()
                removed += 1
            except OSError:
                pass
    return removed


# ---------- CLI -------------------------------------------------------------


def _emit(obj: Any) -> None:
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude_radar.state",
        description="Manage claude-radar session state files.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_set = sub.add_parser("set", help="Set state for a session.")
    p_set.add_argument("--session", required=True)
    p_set.add_argument("--status", required=True, choices=VALID_STATUS)
    p_set.add_argument("--task", default=None)
    p_set.add_argument("--tmux-session", default=None)
    p_set.add_argument("--tty", default=None)
    p_set.add_argument("--cwd", default=None)
    p_set.add_argument("--timestamp", default=None, help="Override timestamp (ISO-8601).")
    p_set.add_argument(
        "--via-tool",
        action="store_true",
        help="Flip to working from a PreToolUse hook; preserve task fields.",
    )

    p_get = sub.add_parser("get", help="Print state for a session.")
    p_get.add_argument("--session", required=True)

    sub.add_parser("list", help="Print state for every known session.")

    p_reset = sub.add_parser("reset", help="Delete all state files.")
    p_reset.add_argument("--yes", action="store_true", help="Skip confirmation.")

    p_clean = sub.add_parser("cleanup", help="Delete idle state files.")
    p_clean.add_argument(
        "--max-age-hours", type=float, default=24.0, help="Cutoff in hours (default 24)."
    )

    p_mute = sub.add_parser("mute", help="Mark a session as ignored (renders as idle).")
    p_mute.add_argument("--session", required=True)
    p_mute.add_argument("--unmute", action="store_true", help="Clear the ignored flag instead.")
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.cmd == "set":
        payload = set_state(
            args.session,
            args.status,
            task=args.task,
            tmux_session=args.tmux_session,
            tty=args.tty,
            cwd=args.cwd,
            timestamp=args.timestamp,
            via_tool=args.via_tool,
        )
        _emit(payload)
        return 0

    if args.cmd == "get":
        data = read_state(args.session)
        if data is None:
            print(f"no state for session {args.session!r}", file=sys.stderr)
            return 1
        _emit(data)
        return 0

    if args.cmd == "list":
        _emit(list_states())
        return 0

    if args.cmd == "reset":
        if not args.yes:
            print("refusing to reset without --yes", file=sys.stderr)
            return 2
        n = reset_all()
        print(f"removed {n} state file(s)")
        return 0

    if args.cmd == "mute":
        result = set_ignored(args.session, ignored=not args.unmute)
        if result is None:
            print(f"no state for session {args.session!r}", file=sys.stderr)
            return 1
        _emit(result)
        return 0

    if args.cmd == "cleanup":
        n = cleanup_idle(int(args.max_age_hours * 3600))
        print(f"removed {n} idle state file(s)")
        return 0

    parser.error(f"unknown command {args.cmd!r}")
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
