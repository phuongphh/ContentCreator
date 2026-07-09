"""Tests for storage/launchd_status.py (issue #72 — launchd watchdog)."""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import storage.launchd_status as ls


def _launchctl_output(labels: list[str]) -> str:
    lines = ["PID\tStatus\tLabel"]
    for label in labels:
        lines.append(f"-\t0\t{label}")
    return "\n".join(lines)


class TestExpectedServices(unittest.TestCase):
    def test_reads_plist_filenames_from_repo(self):
        expected = ls.expected_services()
        # Các plist đã có trong repo từ Phase 1-6 phải được nhận diện tự động
        for label in ("com.ai5phut.pipeline", "com.ai5phut.bot",
                      "com.ai5phut.drama-pipeline", "com.ai5phut.post-scheduler",
                      "com.ai5phut.reddit-drama", "com.ai5phut.metrics-pull"):
            self.assertIn(label, expected)

    def test_sorted_and_labels_only(self):
        expected = ls.expected_services()
        self.assertEqual(expected, sorted(expected))
        self.assertTrue(all(e.startswith("com.ai5phut.") for e in expected))
        self.assertTrue(all(not e.endswith(".plist") for e in expected))


class TestLoadedServices(unittest.TestCase):
    def test_parses_launchctl_list_output(self):
        proc = MagicMock(returncode=0, stdout=_launchctl_output(
            ["com.ai5phut.pipeline", "com.ai5phut.bot", "com.apple.foo"]))
        with patch.object(ls.subprocess, "run", return_value=proc):
            loaded = ls.loaded_services()
        self.assertIn("com.ai5phut.pipeline", loaded)
        self.assertIn("com.apple.foo", loaded)

    def test_returns_none_when_launchctl_missing(self):
        with patch.object(ls.subprocess, "run",
                          side_effect=FileNotFoundError("launchctl")):
            self.assertIsNone(ls.loaded_services())

    def test_returns_none_on_timeout(self):
        with patch.object(ls.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired("launchctl", 5)):
            self.assertIsNone(ls.loaded_services())

    def test_returns_none_on_nonzero_exit(self):
        proc = MagicMock(returncode=1, stdout="")
        with patch.object(ls.subprocess, "run", return_value=proc):
            self.assertIsNone(ls.loaded_services())


class TestMissingServices(unittest.TestCase):
    def test_detects_issue_72_scenario(self):
        # Kịch bản đúng như issue #72: chỉ pipeline + bot được load
        with patch.object(ls, "loaded_services",
                          return_value={"com.ai5phut.pipeline", "com.ai5phut.bot"}):
            missing = ls.missing_services()
        self.assertIn("com.ai5phut.drama-pipeline", missing)
        self.assertIn("com.ai5phut.post-scheduler", missing)
        self.assertIn("com.ai5phut.reddit-drama", missing)
        self.assertIn("com.ai5phut.metrics-pull", missing)
        self.assertNotIn("com.ai5phut.pipeline", missing)
        self.assertNotIn("com.ai5phut.bot", missing)

    def test_none_when_undetectable(self):
        with patch.object(ls, "loaded_services", return_value=None):
            self.assertIsNone(ls.missing_services())

    def test_empty_when_all_loaded(self):
        with patch.object(ls, "loaded_services",
                          return_value=set(ls.expected_services())):
            self.assertEqual(ls.missing_services(), [])


class TestCheckAndAlert(unittest.TestCase):
    def test_alerts_on_missing(self):
        sent = []
        fake_bot = MagicMock()
        fake_bot.send_alert = lambda msg: sent.append(msg)
        with patch.object(ls, "missing_services",
                          return_value=["com.ai5phut.drama-pipeline"]), \
             patch.dict(sys.modules, {"notifier.telegram_bot": fake_bot}):
            missing = ls.check_and_alert()
        self.assertEqual(missing, ["com.ai5phut.drama-pipeline"])
        self.assertEqual(len(sent), 1)
        self.assertIn("com.ai5phut.drama-pipeline", sent[0])
        self.assertIn("install.sh", sent[0])  # alert phải chỉ đường fix

    def test_silent_when_all_loaded(self):
        fake_bot = MagicMock()
        with patch.object(ls, "missing_services", return_value=[]), \
             patch.dict(sys.modules, {"notifier.telegram_bot": fake_bot}):
            self.assertEqual(ls.check_and_alert(), [])
        fake_bot.send_alert.assert_not_called()

    def test_silent_when_not_macos(self):
        fake_bot = MagicMock()
        with patch.object(ls, "missing_services", return_value=None), \
             patch.dict(sys.modules, {"notifier.telegram_bot": fake_bot}):
            self.assertEqual(ls.check_and_alert(), [])
        fake_bot.send_alert.assert_not_called()

    def test_never_raises_when_telegram_fails(self):
        fake_bot = MagicMock()
        fake_bot.send_alert = MagicMock(side_effect=RuntimeError("telegram down"))
        with patch.object(ls, "missing_services",
                          return_value=["com.ai5phut.drama-pipeline"]), \
             patch.dict(sys.modules, {"notifier.telegram_bot": fake_bot}):
            missing = ls.check_and_alert()  # không được raise
        self.assertEqual(missing, ["com.ai5phut.drama-pipeline"])


if __name__ == "__main__":
    unittest.main()
