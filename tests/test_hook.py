"""End-to-end tests for hooks/state-tracker.sh prompt filtering.

The hook is bash + inline Python. Easiest way to lock the regex behaviour
is to actually run it in a subprocess and assert against the resulting
state file.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "state-tracker.sh"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class HookTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.session_id = "hook-test"
        self.env = {
            **os.environ,
            "CLAUDE_RADAR_HOME": self.tmp.name,
            "CLAUDE_RADAR_SESSION_ID": self.session_id,
        }

    def fire(self, hook_type: str, payload: dict) -> None:
        subprocess.run(
            ["bash", str(HOOK), hook_type],
            input=json.dumps(payload).encode("utf-8"),
            env=self.env,
            check=True,
            capture_output=True,
        )

    def state(self) -> dict:
        path = Path(self.tmp.name) / "state" / f"{self.session_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))


class TestSystemTagPromptFilter(HookTestBase):
    """A real user prompt should land; system-injected XML-tag prompts
    must NOT overwrite it."""

    def test_real_prompt_lands(self) -> None:
        self.fire("UserPromptSubmit", {"prompt": "do the real thing"})
        self.assertEqual(self.state()["current_task"], "do the real thing")

    def test_scheduled_task_tag_is_filtered(self) -> None:
        self.fire("UserPromptSubmit", {"prompt": "do the real thing"})
        self.fire(
            "UserPromptSubmit",
            {"prompt": '<scheduled-task name="weekly" file="/x/y.md">'
                       'automated run begins...</scheduled-task>'},
        )
        self.assertEqual(self.state()["current_task"], "do the real thing")

    def test_task_notification_tag_is_filtered(self) -> None:
        self.fire("UserPromptSubmit", {"prompt": "do the real thing"})
        self.fire(
            "UserPromptSubmit",
            {"prompt": "<task-notification>\n<task-id>abc</task-id>\nresult"},
        )
        self.assertEqual(self.state()["current_task"], "do the real thing")

    def test_system_reminder_tag_is_filtered(self) -> None:
        self.fire("UserPromptSubmit", {"prompt": "do the real thing"})
        self.fire(
            "UserPromptSubmit",
            {"prompt": "<system-reminder>\nPlease keep the response short."},
        )
        self.assertEqual(self.state()["current_task"], "do the real thing")

    def test_single_word_html_tag_is_not_filtered(self) -> None:
        # User asking about an HTML element shouldn't be misclassified.
        # The filter only triggers on hyphenated tags (system convention).
        self.fire("UserPromptSubmit", {"prompt": "<div> tag does what?"})
        self.assertEqual(self.state()["current_task"], "<div> tag does what?")


if __name__ == "__main__":
    unittest.main()
