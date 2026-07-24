"""Tests cho watchdog chống treo + SIGTERM graceful shutdown (issue #107).

Root cause #107: getUpdates kẹt vĩnh viễn ở sock_connect sau chu kỳ ngủ/dậy
của Mac dù urlopen có timeout (deadline theo monotonic clock — đứng im khi máy
ngủ). Watchdog đo bằng wall clock và hạ process khi một pha vượt trần.
"""
from __future__ import annotations

import os
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import notifier.telegram_bot as tb


class TestWatchdogVerdict(unittest.TestCase):
    def setUp(self):
        p = patch.object(tb, "config")
        self.cfg = p.start()
        self.addCleanup(p.stop)
        self.cfg.BOT_WATCHDOG_POLL_TIMEOUT = 180
        self.cfg.BOT_WATCHDOG_HANDLE_TIMEOUT = 1800

    def test_poll_within_limit_ok(self):
        state = {"phase": "poll", "since": 1000.0}
        self.assertIsNone(tb._watchdog_verdict(state, 1000.0 + 60))

    def test_poll_over_limit_kills(self):
        state = {"phase": "poll", "since": 1000.0}
        self.assertIsNotNone(tb._watchdog_verdict(state, 1000.0 + 181))

    def test_handle_uses_wider_limit(self):
        # Pha handle (approve → upload) hợp lệ có thể dài nhiều phút — trần
        # poll chặt không được chém nhầm pha handle.
        state = {"phase": "handle", "since": 1000.0}
        self.assertIsNone(tb._watchdog_verdict(state, 1000.0 + 600))
        self.assertIsNotNone(tb._watchdog_verdict(state, 1000.0 + 1801))

    def test_idle_never_kills(self):
        self.assertIsNone(tb._watchdog_verdict({"phase": "idle", "since": 0.0}, 1e12))

    def test_zero_limit_disables_phase(self):
        self.cfg.BOT_WATCHDOG_POLL_TIMEOUT = 0
        self.assertIsNone(tb._watchdog_verdict({"phase": "poll", "since": 0.0}, 1e12))

    def test_mark_updates_state(self):
        old = dict(tb._watchdog_state)
        self.addCleanup(lambda: tb._watchdog_state.update(old))
        tb._watchdog_mark("poll")
        self.assertEqual(tb._watchdog_state["phase"], "poll")
        self.assertAlmostEqual(tb._watchdog_state["since"], time.time(), delta=5)


class TestWatchdogLoop(unittest.TestCase):
    def test_loop_exits_process_when_stuck(self):
        old = dict(tb._watchdog_state)
        self.addCleanup(lambda: tb._watchdog_state.update(old))
        with patch.object(tb, "config") as cfg, \
             patch.object(tb.time, "sleep"), \
             patch.object(tb, "_release_bot_lock") as rel, \
             patch.object(tb.os, "_exit", side_effect=SystemExit) as ex:
            cfg.BOT_WATCHDOG_POLL_TIMEOUT = 180
            cfg.BOT_WATCHDOG_HANDLE_TIMEOUT = 1800
            tb._watchdog_state.update({"phase": "poll",
                                       "since": time.time() - 999})
            with self.assertRaises(SystemExit):
                tb._watchdog_loop()
        rel.assert_called_once()
        # 70 = EX_SOFTWARE. KHÔNG được là 78 (EX_CONFIG): launchd khoá job
        # exit 78 vào "spawn scheduled" tới khi reload — KeepAlive không cứu.
        ex.assert_called_once_with(70)


class TestSigterm(unittest.TestCase):
    def test_handler_raises_systemexit(self):
        with self.assertRaises(SystemExit):
            tb._sigterm_handler(signal.SIGTERM, None)

    def test_run_bot_releases_lock_on_sigterm(self):
        # run_bot đang poll thì SystemExit (SIGTERM từ launchd khi Mac ngủ/
        # reload) → thoát gọn KHÔNG raise ra ngoài, PID lock được nhả để
        # instance mới không đụng stale lock.
        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        os.remove(tmp.name)
        old_handler = signal.getsignal(signal.SIGTERM)
        try:
            with patch.object(tb, "config") as cfg, \
                 patch.object(tb, "_BOT_LOCK_FILE", tmp.name), \
                 patch.object(tb, "_delete_webhook"), \
                 patch.object(tb, "_send_text"), \
                 patch.object(tb, "_start_watchdog"), \
                 patch.object(tb, "_get_updates", side_effect=SystemExit(0)):
                cfg.TELEGRAM_BOT_TOKEN = "token"
                tb.run_bot(publish_callback=lambda vid: None)
            self.assertFalse(os.path.exists(tmp.name))
        finally:
            signal.signal(signal.SIGTERM, old_handler)


if __name__ == "__main__":
    unittest.main()
