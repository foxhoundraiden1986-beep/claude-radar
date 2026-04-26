"""Tests for ``install/inject-hooks.py``."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_module() -> object:
    """Import ``install/inject-hooks.py`` as a module despite the dash in the name."""
    path = ROOT / "install" / "inject-hooks.py"
    spec = importlib.util.spec_from_file_location("inject_hooks", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["inject_hooks"] = mod
    spec.loader.exec_module(mod)
    return mod


inject_hooks = _load_module()


class InjectHooksTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = Path(self.tmp.name) / "settings.json"
        self.install_dir = Path(self.tmp.name) / "install"

    def _write(self, payload: dict) -> None:
        self.settings.write_text(json.dumps(payload), encoding="utf-8")

    def _read(self) -> dict:
        return json.loads(self.settings.read_text(encoding="utf-8"))


class TestInject(InjectHooksTestBase):
    def test_inject_into_missing_file(self) -> None:
        # No settings file at all — inject should create a valid one.
        inject_hooks.inject(self.settings, self.install_dir)
        self.assertTrue(self.settings.exists())
        data = self._read()
        for event in inject_hooks.HOOK_EVENTS:
            self.assertIn(event, data["hooks"])

    def test_inject_preserves_other_keys(self) -> None:
        self._write(
            {
                "model": "claude-opus",
                "hooks": {
                    "PostToolUse": [
                        {"hooks": [{"type": "command", "command": "echo hi"}]}
                    ]
                },
            }
        )
        inject_hooks.inject(self.settings, self.install_dir)
        data = self._read()
        self.assertEqual(data["model"], "claude-opus")
        self.assertIn("PostToolUse", data["hooks"])
        # Pre-existing PostToolUse command must still be there.
        self.assertEqual(
            data["hooks"]["PostToolUse"][0]["hooks"][0]["command"], "echo hi"
        )

    def test_inject_is_idempotent(self) -> None:
        self._write({})
        inject_hooks.inject(self.settings, self.install_dir)
        first = self.settings.read_text(encoding="utf-8")
        for _ in range(3):
            inject_hooks.inject(self.settings, self.install_dir)
        self.assertEqual(self.settings.read_text(encoding="utf-8"), first)

    def test_inject_updates_command_when_install_dir_changes(self) -> None:
        self._write({})
        old = Path(self.tmp.name) / "old"
        new = Path(self.tmp.name) / "new"
        inject_hooks.inject(self.settings, old)
        inject_hooks.inject(self.settings, new)
        data = self._read()
        # exactly one entry per event, all pointing to the new dir.
        for event in inject_hooks.HOOK_EVENTS:
            self.assertEqual(len(data["hooks"][event]), 1)
            cmd = data["hooks"][event][0]["hooks"][0]["command"]
            self.assertIn(str(new), cmd)
            self.assertNotIn(str(old), cmd)


class TestRemove(InjectHooksTestBase):
    def test_remove_after_inject_clears_radar_hooks(self) -> None:
        self._write({})
        inject_hooks.inject(self.settings, self.install_dir)
        inject_hooks.remove(self.settings)
        data = self._read()
        # No hooks left — entire 'hooks' container should be absent.
        self.assertNotIn("hooks", data)

    def test_remove_keeps_user_hooks(self) -> None:
        self._write(
            {
                "hooks": {
                    "PostToolUse": [
                        {"hooks": [{"type": "command", "command": "echo hi"}]}
                    ]
                }
            }
        )
        inject_hooks.inject(self.settings, self.install_dir)
        inject_hooks.remove(self.settings)
        data = self._read()
        self.assertIn("PostToolUse", data["hooks"])
        self.assertEqual(
            data["hooks"]["PostToolUse"][0]["hooks"][0]["command"], "echo hi"
        )
        for ev in inject_hooks.HOOK_EVENTS:
            self.assertNotIn(ev, data["hooks"])

    def test_remove_when_settings_missing(self) -> None:
        # Should not crash.
        inject_hooks.remove(self.settings)


class TestRefuseInvalid(InjectHooksTestBase):
    def test_refuses_corrupt_json(self) -> None:
        self.settings.write_text("{ not valid", encoding="utf-8")
        with self.assertRaises(SystemExit):
            inject_hooks.inject(self.settings, self.install_dir)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
