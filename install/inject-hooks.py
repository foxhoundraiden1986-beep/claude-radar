"""Idempotently inject claude-radar hooks into a Claude Code settings file.

Claude Code reads its hook configuration from ``~/.claude/settings.json``.
This script reads that JSON, adds the three claude-radar hooks (if not
already present), and writes it back atomically.

Hooks structure (Claude Code v0.x — see README "Configuration"):

    {
      "hooks": {
        "UserPromptSubmit": [
          {"hooks": [{"type": "command", "command": "..."}]},
          ...
        ],
        "Stop": [...],
        "Notification": [...]
      }
    }

Each hook entry is keyed by the command string; running the script twice in
a row leaves the file unchanged.

Usage:

    python3 inject-hooks.py [--settings ~/.claude/settings.json] [--remove]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

HOOK_EVENTS = ("UserPromptSubmit", "Stop", "Notification")
DEFAULT_SETTINGS = Path.home() / ".claude" / "settings.json"
DEFAULT_INSTALL_DIR = Path.home() / ".claude-radar"

# Substring used to identify hook entries we own. Matches the canonical
# script path regardless of where the user installs ``claude-radar`` —
# important for idempotency when ``--install-dir`` is non-default.
MARKER = "/hooks/state-tracker.sh"


def _command_for(event: str, install_dir: Path) -> str:
    """Build the command string Claude Code will run for ``event``."""
    script = install_dir / "hooks" / "state-tracker.sh"
    return f"{script} {event}"


def _is_radar_hook(cmd: str) -> bool:
    return MARKER in cmd


def _ensure_event_block(hooks: Dict[str, Any], event: str) -> List[Dict[str, Any]]:
    """Return the list of hook entries for ``event``, creating it if needed."""
    block = hooks.setdefault(event, [])
    if not isinstance(block, list):
        # Legacy / unexpected shape — replace it. Be conservative: keep the
        # original under a sibling key so the user can recover.
        hooks[f"_unrecognised_{event}"] = block
        block = []
        hooks[event] = block
    return block


def _entry_contains_radar(entry: Dict[str, Any]) -> bool:
    if not isinstance(entry, dict):
        return False
    inner = entry.get("hooks") or []
    if not isinstance(inner, list):
        return False
    for h in inner:
        if isinstance(h, dict) and _is_radar_hook(str(h.get("command", ""))):
            return True
    return False


def _strip_radar_hooks(block: List[Any]) -> List[Any]:
    """Return ``block`` with any claude-radar hook entries removed."""
    out: List[Any] = []
    for entry in block:
        if isinstance(entry, dict):
            inner = entry.get("hooks")
            if isinstance(inner, list):
                kept = [
                    h for h in inner
                    if not (
                        isinstance(h, dict)
                        and _is_radar_hook(str(h.get("command", "")))
                    )
                ]
                if not kept:
                    continue  # entry only held our hooks; drop it.
                if len(kept) != len(inner):
                    new_entry = dict(entry)
                    new_entry["hooks"] = kept
                    out.append(new_entry)
                    continue
        out.append(entry)
    return out


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".radar-settings-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_settings(path: Path) -> Tuple[Dict[str, Any], bool]:
    """Return ``(settings_dict, existed_before)``."""
    if not path.exists():
        return {}, False
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}, True
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise SystemExit(
            f"refusing to touch {path}: file is not valid JSON ({exc})"
        )
    if not isinstance(data, dict):
        raise SystemExit(
            f"refusing to touch {path}: top-level value is not a JSON object"
        )
    return data, True


def inject(
    settings_path: Path,
    install_dir: Path,
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Add (or keep) the three claude-radar hooks. Returns the new settings dict."""
    settings, existed = _load_settings(settings_path)
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise SystemExit(
            f"refusing to touch {settings_path}: 'hooks' is not an object"
        )

    changed = False
    for event in HOOK_EVENTS:
        block = _ensure_event_block(hooks, event)
        cmd = _command_for(event, install_dir)
        already = any(
            _entry_contains_radar(entry) for entry in block
        )
        if already:
            # Make sure the command string is up-to-date (handles moved
            # install dirs).
            for entry in block:
                if isinstance(entry, dict) and isinstance(entry.get("hooks"), list):
                    for h in entry["hooks"]:
                        if isinstance(h, dict) and _is_radar_hook(str(h.get("command", ""))):
                            if h.get("command") != cmd:
                                h["command"] = cmd
                                changed = True
            continue
        block.append({"hooks": [{"type": "command", "command": cmd}]})
        changed = True

    if changed and not dry_run:
        text = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
        _atomic_write(settings_path, text)
    return settings


def remove(settings_path: Path, *, dry_run: bool = False) -> Dict[str, Any]:
    """Remove every claude-radar hook from the settings file."""
    settings, existed = _load_settings(settings_path)
    if not existed:
        return settings
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return settings

    changed = False
    for event in HOOK_EVENTS:
        block = hooks.get(event)
        if not isinstance(block, list):
            continue
        new_block = _strip_radar_hooks(block)
        if new_block != block:
            changed = True
            if new_block:
                hooks[event] = new_block
            else:
                hooks.pop(event, None)

    # Drop the empty "hooks" container if we cleaned everything out.
    if isinstance(hooks, dict) and not hooks:
        settings.pop("hooks", None)
        changed = True

    if changed and not dry_run:
        text = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
        _atomic_write(settings_path, text)
    return settings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="inject-hooks",
        description="Idempotently inject claude-radar hooks into a Claude Code settings file.",
    )
    parser.add_argument(
        "--settings",
        type=Path,
        default=DEFAULT_SETTINGS,
        help=f"Path to settings.json (default: {DEFAULT_SETTINGS}).",
    )
    parser.add_argument(
        "--install-dir",
        type=Path,
        default=DEFAULT_INSTALL_DIR,
        help=f"Path the hooks will be invoked from (default: {DEFAULT_INSTALL_DIR}).",
    )
    parser.add_argument(
        "--remove",
        action="store_true",
        help="Remove our hooks instead of adding them.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change but do not write the file.",
    )
    return parser


def main(argv: List[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    settings_path = args.settings.expanduser()
    install_dir = args.install_dir.expanduser()

    if args.remove:
        settings = remove(settings_path, dry_run=args.dry_run)
        sys.stderr.write(
            f"removed claude-radar hooks from {settings_path}{' (dry-run)' if args.dry_run else ''}\n"
        )
    else:
        settings = inject(settings_path, install_dir, dry_run=args.dry_run)
        sys.stderr.write(
            f"injected claude-radar hooks into {settings_path}{' (dry-run)' if args.dry_run else ''}\n"
        )
    if args.dry_run:
        json.dump(settings, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
