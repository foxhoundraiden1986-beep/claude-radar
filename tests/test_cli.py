"""Integration-ish tests for the CLI entry points (no curses)."""

from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claude_radar import cli, state  # noqa: E402


class CliTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._old_env = os.environ.get("CLAUDE_RADAR_HOME")
        os.environ["CLAUDE_RADAR_HOME"] = self.tmp.name

    def tearDown(self) -> None:
        if self._old_env is None:
            os.environ.pop("CLAUDE_RADAR_HOME", None)
        else:
            os.environ["CLAUDE_RADAR_HOME"] = self._old_env

    def _capture(self, fn, *argv) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = fn(list(argv))
        self.assertEqual(rc, 0, msg=buf.getvalue())
        return buf.getvalue()


class TestStatusCli(CliTestBase):
    def test_empty_compact(self) -> None:
        out = self._capture(cli.status_main).strip()
        self.assertEqual(out, "○0")

    def test_compact_counts(self) -> None:
        state.set_state("a", "waiting")
        state.set_state("b", "working", task="t")
        out = self._capture(cli.status_main).strip()
        self.assertIn("💬1", out)
        self.assertIn("⚡1", out)

    def test_verbose_lists_tasks(self) -> None:
        # Use a recent timestamp so the session doesn't get escalated to idle
        # by render's stale-working threshold (DEFAULT_IDLE_AFTER_SECONDS).
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        state.set_state("alpha", "working", task="long task here", timestamp=now_iso)
        out = self._capture(cli.status_main, "--verbose").strip()
        self.assertIn("alpha:", out)


class TestRadarCli(CliTestBase):
    def test_once_snapshot_lists_sessions(self) -> None:
        state.set_state("data", "waiting", task="归因分析")
        out = self._capture(cli.main, "--once")
        self.assertIn("data", out)
        self.assertIn("Claude Sessions", out)

    def test_reset_clears_state(self) -> None:
        state.set_state("a", "working")
        out = self._capture(cli.main, "--reset")
        self.assertIn("removed", out)
        self.assertEqual(state.list_states(), [])

    def test_cleanup_runs(self) -> None:
        state.set_state("a", "waiting")
        out = self._capture(cli.main, "--cleanup")
        self.assertIn("idle state file", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
