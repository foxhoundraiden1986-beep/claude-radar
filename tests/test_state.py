"""Unit tests for ``claude_radar.state``."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Make the package importable when running from the repo root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claude_radar import state  # noqa: E402


class StateTestBase(unittest.TestCase):
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


class TestRadarHome(StateTestBase):
    def test_radar_home_uses_env_override(self) -> None:
        self.assertEqual(state.radar_home(), Path(self.tmp.name))

    def test_state_dir_under_home(self) -> None:
        self.assertEqual(state.state_dir(), Path(self.tmp.name) / "state")


class TestSanitize(unittest.TestCase):
    def test_replaces_unsafe_chars(self) -> None:
        self.assertEqual(state.sanitize_session_id("data analysis/foo"), "data_analysis_foo")

    def test_empty_input_returns_unknown(self) -> None:
        self.assertEqual(state.sanitize_session_id("   "), "unknown")

    def test_truncates_long_input(self) -> None:
        long = "a" * 500
        out = state.sanitize_session_id(long)
        self.assertLessEqual(len(out), 120)


class TestSetGet(StateTestBase):
    def test_set_creates_file(self) -> None:
        payload = state.set_state("alpha", "working", task="hello")
        self.assertEqual(payload["status"], "working")
        self.assertEqual(payload["current_task"], "hello")
        self.assertTrue(state.state_path("alpha").exists())

    def test_get_returns_none_for_unknown(self) -> None:
        self.assertIsNone(state.read_state("ghost"))

    def test_invalid_status_raises(self) -> None:
        with self.assertRaises(ValueError):
            state.set_state("alpha", "explod-ing")  # type: ignore[arg-type]

    def test_corrupt_file_returns_none(self) -> None:
        path = state.state_path("alpha")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ not json", encoding="utf-8")
        self.assertIsNone(state.read_state("alpha"))


class TestFieldPreservation(StateTestBase):
    def test_task_started_at_preserved_on_working_to_waiting(self) -> None:
        first = state.set_state(
            "alpha", "working", task="t1", timestamp="2026-04-25T10:00:00+00:00"
        )
        started = first["task_started_at"]
        second = state.set_state("alpha", "waiting", timestamp="2026-04-25T10:30:00+00:00")
        # task_started_at must NOT be reset by waiting transition
        self.assertEqual(second["task_started_at"], started)
        self.assertNotEqual(second["status_changed_at"], first["status_changed_at"])

    def test_repeat_working_keeps_first_task_started_at(self) -> None:
        first = state.set_state(
            "alpha", "working", task="t1", timestamp="2026-04-25T10:00:00+00:00"
        )
        second = state.set_state(
            "alpha", "working", task="t2", timestamp="2026-04-25T10:05:00+00:00"
        )
        # Same status (working->working) keeps task_started_at of the first.
        self.assertEqual(second["task_started_at"], first["task_started_at"])
        self.assertEqual(second["current_task"], "t2")

    def test_new_working_session_after_waiting_resets_task_started_at(self) -> None:
        state.set_state("alpha", "working", task="t1", timestamp="2026-04-25T10:00:00+00:00")
        state.set_state("alpha", "waiting", timestamp="2026-04-25T10:30:00+00:00")
        again = state.set_state(
            "alpha", "working", task="t2", timestamp="2026-04-25T11:00:00+00:00"
        )
        self.assertEqual(again["task_started_at"], "2026-04-25T11:00:00+00:00")

    def test_status_changed_at_only_updates_on_change(self) -> None:
        first = state.set_state(
            "alpha", "working", task="t1", timestamp="2026-04-25T10:00:00+00:00"
        )
        same = state.set_state(
            "alpha", "working", task="t2", timestamp="2026-04-25T10:05:00+00:00"
        )
        self.assertEqual(same["status_changed_at"], first["status_changed_at"])

    def test_via_tool_preserves_task_clock_after_stop(self) -> None:
        # The Stop hook can fire mid-turn; the next PreToolUse must flip us
        # back to working WITHOUT resetting task_started_at / current_task /
        # last_user_prompt_at, otherwise the dashboard age clock keeps
        # rebooting to 0.
        first = state.set_state(
            "alpha", "working", task="real prompt",
            timestamp="2026-04-25T10:00:00+00:00",
        )
        state.set_state("alpha", "waiting", timestamp="2026-04-25T10:00:30+00:00")
        flipped = state.set_state(
            "alpha", "working",
            timestamp="2026-04-25T10:01:00+00:00",
            via_tool=True,
        )
        self.assertEqual(flipped["task_started_at"], first["task_started_at"])
        self.assertEqual(flipped["current_task"], "real prompt")
        self.assertEqual(
            flipped["last_user_prompt_at"], first["last_user_prompt_at"]
        )
        self.assertEqual(flipped["status"], "working")

    def test_via_tool_does_not_overwrite_task(self) -> None:
        state.set_state(
            "alpha", "working", task="real prompt",
            timestamp="2026-04-25T10:00:00+00:00",
        )
        # PreToolUse never carries a task — confirm a via_tool flip with
        # task=None preserves current_task rather than clobbering it.
        flipped = state.set_state(
            "alpha", "working", task=None,
            timestamp="2026-04-25T10:01:00+00:00",
            via_tool=True,
        )
        self.assertEqual(flipped["current_task"], "real prompt")

    def test_via_tool_preserves_user_mute(self) -> None:
        # User mutes a session that has already flipped to waiting.
        state.set_state(
            "alpha", "working", task="real prompt",
            timestamp="2026-04-25T10:00:00+00:00",
        )
        state.set_state("alpha", "waiting", timestamp="2026-04-25T10:00:30+00:00")
        state.set_ignored("alpha", True)
        # PreToolUse flips back to working; the mute must survive — it's a
        # user-meaningful flag, not a status-transition artefact.
        flipped = state.set_state(
            "alpha", "working",
            timestamp="2026-04-25T10:01:00+00:00",
            via_tool=True,
        )
        self.assertTrue(flipped.get("ignored"))
        # And a *real* user prompt afterwards must still clear it.
        cleared = state.set_state(
            "alpha", "working", task="next prompt",
            timestamp="2026-04-25T10:02:00+00:00",
        )
        self.assertNotIn("ignored", cleared)


class TestAtomicWrite(StateTestBase):
    def test_no_tmp_files_left_after_set(self) -> None:
        state.set_state("alpha", "working", task="t")
        sd = state.state_dir()
        leftovers = [p for p in sd.iterdir() if p.name.startswith(".radar-")]
        self.assertEqual(leftovers, [])

    def test_replace_does_not_create_partial_file(self) -> None:
        # Simulate a few writes; ensure file is always valid JSON.
        for i in range(20):
            state.set_state(
                "alpha", "working", task=f"task-{i}", timestamp=f"2026-04-25T10:00:{i:02d}+00:00"
            )
            with state.state_path("alpha").open("r", encoding="utf-8") as f:
                json.load(f)  # raises if partial / corrupt


class TestList(StateTestBase):
    def test_list_returns_all_sessions(self) -> None:
        state.set_state("alpha", "working", task="a")
        state.set_state("beta", "waiting")
        state.set_state("gamma", "working", task="g")
        ids = sorted(s["session_id"] for s in state.list_states())
        self.assertEqual(ids, ["alpha", "beta", "gamma"])

    def test_list_skips_corrupt_files(self) -> None:
        state.set_state("alpha", "working")
        bad = state.state_dir() / "broken.json"
        bad.write_text("not json", encoding="utf-8")
        ids = [s["session_id"] for s in state.list_states()]
        self.assertEqual(ids, ["alpha"])


class TestResetAndCleanup(StateTestBase):
    def test_reset_removes_all(self) -> None:
        state.set_state("alpha", "working")
        state.set_state("beta", "waiting")
        n = state.reset_all()
        self.assertEqual(n, 2)
        self.assertEqual(state.list_states(), [])

    def test_forget_removes_one_session(self) -> None:
        state.set_state("alpha", "waiting")
        state.set_state("beta", "waiting")
        path = state.state_path("alpha")
        path.unlink()
        ids = [s["session_id"] for s in state.list_states()]
        self.assertEqual(ids, ["beta"])


class TestCLI(StateTestBase):
    def test_cli_set_and_get(self) -> None:
        rc = state.main(
            ["set", "--session", "alpha", "--status", "working", "--task", "hi"]
        )
        self.assertEqual(rc, 0)
        rc = state.main(["get", "--session", "alpha"])
        self.assertEqual(rc, 0)

    def test_cli_get_unknown_returns_1(self) -> None:
        rc = state.main(["get", "--session", "ghost"])
        self.assertEqual(rc, 1)

    def test_cli_list(self) -> None:
        state.set_state("alpha", "working")
        rc = state.main(["list"])
        self.assertEqual(rc, 0)

    def test_cli_reset_requires_yes(self) -> None:
        state.set_state("alpha", "working")
        rc = state.main(["reset"])
        self.assertEqual(rc, 2)
        self.assertEqual(len(state.list_states()), 1)
        rc = state.main(["reset", "--yes"])
        self.assertEqual(rc, 0)
        self.assertEqual(state.list_states(), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
