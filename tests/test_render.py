"""Unit tests for ``claude_radar.render``."""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from claude_radar import render  # noqa: E402


def _state(
    sid: str,
    status: str,
    *,
    task: str = "",
    minutes_ago: int = 0,
    now: datetime,
) -> dict:
    """Helper: build a state dict whose status_changed_at is ``minutes_ago``."""
    ts = (now - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
    return {
        "session_id": sid,
        "status": status,
        "current_task": task,
        "status_changed_at": ts,
    }


class TestFormatDuration(unittest.TestCase):
    def test_seconds(self) -> None:
        self.assertEqual(render.format_duration(7), "7s")

    def test_minute_rounds_down(self) -> None:
        self.assertEqual(render.format_duration(95), "1m")

    def test_minutes(self) -> None:
        self.assertEqual(render.format_duration(13 * 60), "13m")

    def test_hours_round(self) -> None:
        self.assertEqual(render.format_duration(2 * 3600), "2h")

    def test_hours_minutes(self) -> None:
        self.assertEqual(render.format_duration(2 * 3600 + 5 * 60), "2h5m")

    def test_negative_clamped(self) -> None:
        self.assertEqual(render.format_duration(-30), "0s")


class TestDisplayWidth(unittest.TestCase):
    def test_ascii_one_cell(self) -> None:
        self.assertEqual(render._display_width("hello"), 5)

    def test_cjk_double(self) -> None:
        self.assertEqual(render._display_width("中"), 2)
        self.assertEqual(render._display_width("中文"), 4)

    def test_truncate_under(self) -> None:
        self.assertEqual(render.truncate_display("hello", 10), "hello")

    def test_truncate_exact(self) -> None:
        self.assertEqual(render.truncate_display("hello", 5), "hello")

    def test_truncate_over(self) -> None:
        out = render.truncate_display("hello world", 8)
        self.assertEqual(out, "hello w…")
        self.assertLessEqual(render._display_width(out), 8)

    def test_truncate_cjk(self) -> None:
        out = render.truncate_display("线下新客归因分析", 6)
        # Each CJK char is 2 cells wide. 6 cells = 2 chars + ellipsis.
        self.assertLessEqual(render._display_width(out), 6)
        self.assertTrue(out.endswith("…"))

    def test_truncate_zero(self) -> None:
        self.assertEqual(render.truncate_display("anything", 0), "")

    def test_pad_display_pads(self) -> None:
        self.assertEqual(render.pad_display("hi", 5), "hi   ")

    def test_pad_display_no_op_when_already_long(self) -> None:
        self.assertEqual(render.pad_display("hello", 3), "hello")


class TestDeriveViews(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 4, 25, 15, 23, 0, tzinfo=timezone.utc)

    def test_sort_waiting_before_working_before_idle(self) -> None:
        states = [
            _state("dev", "working", task="refactor", minutes_ago=41, now=self.now),
            _state("review", "waiting", task="review", minutes_ago=2, now=self.now),
            _state("idle1", "idle", minutes_ago=120, now=self.now),
        ]
        views = render.derive_views(states, now=self.now)
        self.assertEqual([v.session_id for v in views], ["review", "dev", "idle1"])

    def test_within_status_sorted_by_age_desc(self) -> None:
        states = [
            _state("a", "waiting", minutes_ago=5, now=self.now),
            _state("b", "waiting", minutes_ago=13, now=self.now),
            _state("c", "waiting", minutes_ago=1, now=self.now),
        ]
        views = render.derive_views(states, now=self.now)
        self.assertEqual([v.session_id for v in views], ["b", "a", "c"])

    def test_working_escalates_to_idle_after_threshold(self) -> None:
        states = [
            _state("stale", "working", task="x", minutes_ago=120, now=self.now),
        ]
        views = render.derive_views(states, now=self.now, idle_after_seconds=30 * 60)
        self.assertEqual(views[0].status, render.STATUS_IDLE)
        self.assertEqual(views[0].raw_status, render.STATUS_WORKING)

    def test_waiting_does_not_escalate(self) -> None:
        states = [
            _state("hung", "waiting", minutes_ago=240, now=self.now),
        ]
        views = render.derive_views(states, now=self.now, idle_after_seconds=30 * 60)
        self.assertEqual(views[0].status, render.STATUS_WAITING)

    def test_unknown_status_treated_as_idle(self) -> None:
        states = [{"session_id": "weird", "status": "kaboom", "status_changed_at": None}]
        views = render.derive_views(states, now=self.now)
        self.assertEqual(views[0].status, render.STATUS_IDLE)

    def test_missing_status_changed_at_age_zero(self) -> None:
        states = [{"session_id": "x", "status": "working", "current_task": "t"}]
        views = render.derive_views(states, now=self.now)
        self.assertEqual(views[0].age_seconds, 0)

    def test_subagent_english_prompt_collapsed(self) -> None:
        states = [{
            "session_id": "x",
            "status": "working",
            "current_task": "You are a knowledge compiler. Your job is to read a daily log and...",
        }]
        view = render.derive_views(states, now=self.now)[0]
        self.assertEqual(view.task, "")

    def test_subagent_chinese_prompt_collapsed(self) -> None:
        states = [{
            "session_id": "x",
            "status": "working",
            "current_task": "你是一个日记整理助手。阅读下面的工作日志原始记录，写一份摘要。",
        }]
        view = render.derive_views(states, now=self.now)[0]
        self.assertEqual(view.task, "")

    def test_subagent_chinese_no_quantifier(self) -> None:
        states = [{
            "session_id": "x",
            "status": "working",
            "current_task": "你是数据分析师，请聚合下列指标。",
        }]
        view = render.derive_views(states, now=self.now)[0]
        self.assertEqual(view.task, "")

    def test_long_imperative_review_collapsed(self) -> None:
        # Real sub-agent prompts are long boilerplate; only collapse when len
        # crosses the render threshold.
        long_review = (
            "Review the conversation transcripts from today, extract key "
            "decisions, group them by topic, and produce a summary in markdown."
        )
        states = [{"session_id": "x", "status": "working", "current_task": long_review}]
        view = render.derive_views(states, now=self.now)[0]
        self.assertEqual(view.task, "")

    def test_short_imperative_review_unchanged(self) -> None:
        # User typing "Review the code" must NOT be collapsed.
        states = [{
            "session_id": "x",
            "status": "working",
            "current_task": "Review the code I just pushed",
        }]
        view = render.derive_views(states, now=self.now)[0]
        self.assertEqual(view.task, "Review the code I just pushed")

    def test_short_summarize_unchanged(self) -> None:
        states = [{
            "session_id": "x",
            "status": "working",
            "current_task": "Summarize this article",
        }]
        view = render.derive_views(states, now=self.now)[0]
        self.assertEqual(view.task, "Summarize this article")

    def test_long_your_task_collapsed(self) -> None:
        long_task = (
            "Your task is to walk through every file in src/, identify ones "
            "that import deprecated APIs, and emit a JSON list of fix targets."
        )
        states = [{"session_id": "x", "status": "working", "current_task": long_task}]
        view = render.derive_views(states, now=self.now)[0]
        self.assertEqual(view.task, "")

    def test_real_user_prompt_unchanged(self) -> None:
        states = [{
            "session_id": "x",
            "status": "working",
            "current_task": "[Image #5] 这里的说明不太对",
        }]
        view = render.derive_views(states, now=self.now)[0]
        self.assertEqual(view.task, "[Image #5] 这里的说明不太对")

    def test_tmux_session_propagated_to_view(self) -> None:
        states = [{
            "session_id": "data",
            "status": "waiting",
            "tmux_session": "data",
        }]
        view = render.derive_views(states, now=self.now)[0]
        self.assertEqual(view.tmux_session, "data")

    def test_tmux_session_none_when_missing(self) -> None:
        states = [{"session_id": "pid-9999", "status": "waiting"}]
        view = render.derive_views(states, now=self.now)[0]
        self.assertIsNone(view.tmux_session)

    def test_user_prompt_starting_with_you_are_not_collapsed(self) -> None:
        # Real user prompt that *happens* to start with "you are" but lacks the
        # role+punctuation structure should pass through unchanged.
        states = [{
            "session_id": "x",
            "status": "working",
            "current_task": "you are kidding me",
        }]
        view = render.derive_views(states, now=self.now)[0]
        self.assertEqual(view.task, "you are kidding me")


class TestRenderCompact(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 4, 25, 15, 23, 0, tzinfo=timezone.utc)

    def test_empty(self) -> None:
        self.assertEqual(render.render_compact([], now=self.now), "○0")

    def test_counts_format(self) -> None:
        states = [
            _state("a", "waiting", minutes_ago=5, now=self.now),
            _state("b", "waiting", minutes_ago=2, now=self.now),
            _state("c", "working", minutes_ago=1, now=self.now),
            _state("d", "idle", minutes_ago=300, now=self.now),
        ]
        out = render.render_compact(states, now=self.now)
        self.assertIn("💬2", out)
        self.assertIn("⚡1", out)
        self.assertIn("○1", out)

    def test_verbose_lists_sessions(self) -> None:
        states = [
            _state("data", "waiting", task="归因分析", minutes_ago=13, now=self.now),
            _state("dev", "working", task="重构 report_utils", minutes_ago=41, now=self.now),
        ]
        out = render.render_compact(states, now=self.now, verbose=True)
        self.assertIn("data:", out)
        self.assertIn("dev:", out)
        self.assertIn("13m", out)
        self.assertIn("41m", out)
        self.assertIn("💬", out)
        self.assertIn("⚡", out)

    def test_verbose_truncates_long_task(self) -> None:
        long_task = "a" * 200
        states = [_state("x", "waiting", task=long_task, minutes_ago=5, now=self.now)]
        out = render.render_compact(states, now=self.now, verbose=True)
        # Output should not contain the full long task.
        self.assertNotIn("a" * 100, out)


class TestRenderBoard(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 4, 25, 15, 23, 0, tzinfo=timezone.utc)

    def _assert_dimensions(self, lines, *, width: int, height: int) -> None:
        self.assertEqual(len(lines), height, msg=f"expected {height} rows, got {len(lines)}")
        for line in lines:
            self.assertLessEqual(
                render._display_width(line),
                width,
                msg=f"line too wide: {line!r}",
            )

    def test_empty_board_shows_friendly_message(self) -> None:
        lines = render.render_board([], width=60, height=10, now=self.now)
        self._assert_dimensions(lines, width=60, height=10)
        joined = "\n".join(lines)
        self.assertIn("No active", joined)

    def test_board_contains_session_ids(self) -> None:
        states = [
            _state("data-analysis", "waiting", task="归因分析", minutes_ago=13, now=self.now),
            _state("dev", "working", task="重构 report_utils", minutes_ago=41, now=self.now),
            _state("review", "idle", minutes_ago=200, now=self.now),
        ]
        lines = render.render_board(states, width=60, height=10, now=self.now)
        self._assert_dimensions(lines, width=60, height=10)
        joined = "\n".join(lines)
        self.assertIn("data-analysis", joined)
        self.assertIn("dev", joined)
        self.assertIn("review", joined)
        self.assertIn("13m", joined)
        self.assertIn("41m", joined)
        # Footer present
        self.assertIn("quit", joined)

    def test_board_truncates_to_height(self) -> None:
        states = [
            _state(f"s{i}", "waiting", task="task", minutes_ago=i + 1, now=self.now)
            for i in range(20)
        ]
        lines = render.render_board(states, width=60, height=8, now=self.now)
        self._assert_dimensions(lines, width=60, height=8)
        joined = "\n".join(lines)
        self.assertIn("more", joined)

    def test_narrow_board_does_not_crash(self) -> None:
        states = [_state("a", "working", task="x", minutes_ago=1, now=self.now)]
        # width 20 is the floor enforced by the renderer.
        lines = render.render_board(states, width=20, height=6, now=self.now)
        self._assert_dimensions(lines, width=20, height=6)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
