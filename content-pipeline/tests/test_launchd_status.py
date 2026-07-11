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


def _launchctl_output_with_status(rows: list[tuple[str, str, str]]) -> str:
    """rows = list of (pid, status, label) — mô phỏng output launchctl list."""
    lines = ["PID\tStatus\tLabel"]
    for pid, status, label in rows:
        lines.append(f"{pid}\t{status}\t{label}")
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


class TestServiceStatuses(unittest.TestCase):
    def test_parses_exit_status_column(self):
        out = _launchctl_output_with_status([
            ("-", "0", "com.ai5phut.pipeline"),
            ("-", "78", "com.ai5phut.drama-pipeline"),   # EX_CONFIG
            ("1234", "0", "com.ai5phut.bot"),            # đang chạy
            ("-", "-", "com.ai5phut.metrics-pull"),      # chưa chạy
        ])
        proc = MagicMock(returncode=0, stdout=out)
        with patch.object(ls.subprocess, "run", return_value=proc):
            st = ls.service_statuses()
        self.assertEqual(st["com.ai5phut.pipeline"], 0)
        self.assertEqual(st["com.ai5phut.drama-pipeline"], 78)
        self.assertEqual(st["com.ai5phut.bot"], 0)
        self.assertIsNone(st["com.ai5phut.metrics-pull"])  # '-' → None
        self.assertNotIn("Label", st)  # header bị bỏ

    def test_none_when_launchctl_missing(self):
        with patch.object(ls.subprocess, "run",
                          side_effect=FileNotFoundError("launchctl")):
            self.assertIsNone(ls.service_statuses())


class TestFailingServices(unittest.TestCase):
    def test_detects_ex_config_and_ignores_healthy_and_foreign(self):
        out = _launchctl_output_with_status([
            ("-", "0", "com.ai5phut.pipeline"),          # ok
            ("-", "78", "com.ai5phut.drama-pipeline"),   # fail 78
            ("-", "1", "com.ai5phut.reddit-drama"),      # fail khác
            ("-", "5", "com.apple.something"),           # ngoài scope → bỏ
        ])
        proc = MagicMock(returncode=0, stdout=out)
        with patch.object(ls.subprocess, "run", return_value=proc):
            failing = ls.failing_services()
        self.assertEqual(failing.get("com.ai5phut.drama-pipeline"), 78)
        self.assertEqual(failing.get("com.ai5phut.reddit-drama"), 1)
        self.assertNotIn("com.ai5phut.pipeline", failing)
        self.assertNotIn("com.apple.something", failing)

    def test_none_when_undetectable(self):
        with patch.object(ls, "service_statuses", return_value=None):
            self.assertIsNone(ls.failing_services())


class TestReloadService(unittest.TestCase):
    def test_true_on_success(self):
        proc = MagicMock(returncode=0, stdout="", stderr="")
        with patch.object(ls.subprocess, "run", return_value=proc) as run:
            self.assertTrue(ls.reload_service("com.ai5phut.drama-pipeline"))
        args = run.call_args[0][0]
        self.assertIn("reload", args)
        self.assertIn("com.ai5phut.drama-pipeline", args)

    def test_false_on_nonzero(self):
        proc = MagicMock(returncode=1, stdout="", stderr="boom")
        with patch.object(ls.subprocess, "run", return_value=proc):
            self.assertFalse(ls.reload_service("com.ai5phut.drama-pipeline"))

    def test_false_when_launchctl_missing(self):
        with patch.object(ls.subprocess, "run",
                          side_effect=FileNotFoundError("bash")):
            self.assertFalse(ls.reload_service("com.ai5phut.drama-pipeline"))


class TestCheckFailingAndHeal(unittest.TestCase):
    def test_heals_ex_config_and_alerts(self):
        sent = []
        fake_bot = MagicMock()
        fake_bot.send_alert = lambda msg: sent.append(msg)
        reloaded = []
        with patch.object(ls, "failing_services",
                          return_value={"com.ai5phut.drama-pipeline": 78}), \
             patch.object(ls, "reload_service",
                          side_effect=lambda l: reloaded.append(l) or True), \
             patch.dict(sys.modules, {"notifier.telegram_bot": fake_bot}):
            failing = ls._check_failing_and_heal(self_label=None, heal=True)
        self.assertEqual(reloaded, ["com.ai5phut.drama-pipeline"])
        self.assertIn("com.ai5phut.drama-pipeline", failing)
        self.assertEqual(len(sent), 1)
        self.assertIn("re-bootstrap", sent[0])

    def test_skips_self_label(self):
        fake_bot = MagicMock()
        with patch.object(ls, "failing_services",
                          return_value={"com.ai5phut.pipeline": 78}), \
             patch.object(ls, "reload_service") as reload_mock, \
             patch.dict(sys.modules, {"notifier.telegram_bot": fake_bot}):
            failing = ls._check_failing_and_heal(
                self_label="com.ai5phut.pipeline", heal=True)
        self.assertEqual(failing, {})
        reload_mock.assert_not_called()
        fake_bot.send_alert.assert_not_called()

    def test_non_ex_config_alerts_without_reload(self):
        sent = []
        fake_bot = MagicMock()
        fake_bot.send_alert = lambda msg: sent.append(msg)
        with patch.object(ls, "failing_services",
                          return_value={"com.ai5phut.reddit-drama": 1}), \
             patch.object(ls, "reload_service") as reload_mock, \
             patch.dict(sys.modules, {"notifier.telegram_bot": fake_bot}):
            ls._check_failing_and_heal(self_label=None, heal=True)
        reload_mock.assert_not_called()  # exit 1 ≠ EX_CONFIG → chỉ alert
        self.assertEqual(len(sent), 1)
        self.assertIn("Vẫn fail", sent[0])

    def test_reload_failure_reported_as_still_failing(self):
        sent = []
        fake_bot = MagicMock()
        fake_bot.send_alert = lambda msg: sent.append(msg)
        with patch.object(ls, "failing_services",
                          return_value={"com.ai5phut.drama-pipeline": 78}), \
             patch.object(ls, "reload_service", return_value=False), \
             patch.dict(sys.modules, {"notifier.telegram_bot": fake_bot}):
            ls._check_failing_and_heal(self_label=None, heal=True)
        self.assertEqual(len(sent), 1)
        self.assertIn("Vẫn fail", sent[0])

    def test_silent_when_none(self):
        fake_bot = MagicMock()
        with patch.object(ls, "failing_services", return_value=None), \
             patch.dict(sys.modules, {"notifier.telegram_bot": fake_bot}):
            self.assertEqual(ls._check_failing_and_heal(None, True), {})
        fake_bot.send_alert.assert_not_called()


class TestCheckAndAlertIntegration(unittest.TestCase):
    def test_returns_missing_and_still_checks_failing(self):
        # missing + failing đồng thời: trả về missing, đồng thời heal failing.
        sent = []
        fake_bot = MagicMock()
        fake_bot.send_alert = lambda msg: sent.append(msg)
        with patch.object(ls, "missing_services",
                          return_value=["com.ai5phut.weekly-retro"]), \
             patch.object(ls, "failing_services",
                          return_value={"com.ai5phut.drama-pipeline": 78}), \
             patch.object(ls, "reload_service", return_value=True), \
             patch.dict(sys.modules, {"notifier.telegram_bot": fake_bot}):
            missing = ls.check_and_alert(self_label="com.ai5phut.pipeline")
        self.assertEqual(missing, ["com.ai5phut.weekly-retro"])
        self.assertEqual(len(sent), 2)  # 1 alert missing + 1 alert failing/heal

    def test_failing_check_never_raises(self):
        with patch.object(ls, "missing_services", return_value=[]), \
             patch.object(ls, "failing_services",
                          side_effect=RuntimeError("launchctl exploded")):
            # không được raise
            self.assertEqual(ls.check_and_alert(), [])


if __name__ == "__main__":
    unittest.main()
