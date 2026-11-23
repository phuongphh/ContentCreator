"""ops/deploy.py on a copy of this repository with a real database and the real migration runner.

23/11: a hand-written Production plan said the new migration runs at startup and
rolled back with `git reset --hard` plus the old database. These tests hold the
deploy command to what ops/fleet/README.md requires.
"""

import datetime as dt
import importlib.util
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("ops_deploy", ROOT / "ops" / "deploy.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)

QUIET = dt.datetime(2026, 11, 23, 22, 0)
PROBE = "999_deploy_probe"


class FakeServices:
    def __init__(self, fail_quiesce=False):
        self.calls, self.fail_quiesce = [], fail_quiesce

    def quiesce(self):
        self.calls.append("quiesce")
        if self.fail_quiesce:
            raise deploy.Refused("scheduled jobs are running now")

    def resume(self):
        self.calls.append("resume")

    def status(self):
        return {"bot_pid": 4242, "loaded": [], "unloaded": []}


def run(cmd, cwd, **kw):
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True, **kw)


class DeployTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name) / "ContentCreator"
        shutil.copytree(ROOT / "content-pipeline", self.repo / "content-pipeline",
                        ignore=shutil.ignore_patterns("tests", "venv", "logs", "output", "*.db", "__pycache__",
                                                      ".env", "queue_tiktok", "cache"))
        (self.repo / "ops").mkdir()
        shutil.copy(ROOT / "ops" / "deploy.py", self.repo / "ops" / "deploy.py")
        for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"],
                     ["add", "-A"], ["commit", "-q", "-m", "baseline"]):
            run(["git", *args], self.repo)
        self.base = self.head()
        migrations = self.repo / deploy.MIGRATIONS
        (migrations / f"{PROBE}.sql").write_text("ALTER TABLE videos ADD COLUMN deploy_probe INTEGER;\n")
        (migrations / f"{PROBE}_down.sql").write_text("ALTER TABLE videos DROP COLUMN deploy_probe;\n")
        run(["git", "add", "-A"], self.repo)
        run(["git", "commit", "-q", "-m", "add a migration"], self.repo)
        self.target = self.head()
        run(["git", "checkout", "-q", "--detach", self.base], self.repo)
        self.env = {k: v for k, v in os.environ.items() if k not in ("CONTENT_RUNTIME_MODE", "CONTENT_STATE_DIR")}
        run([sys.executable, "-m", "storage.migrate", "up"], self.repo / "content-pipeline", env=self.env)
        self.db = deploy.default_db(self.repo)
        self.assertNotIn(PROBE, deploy.applied_migrations(self.db))

    def head(self):
        return run(["git", "rev-parse", "HEAD"], self.repo).stdout.strip()

    def apply(self, services, **kw):
        os.environ.pop("CONTENT_RUNTIME_MODE", None)
        return deploy.apply(self.repo, self.target, services, sys.executable,
                            backup_dir=self.repo.parent / "backup", now=QUIET, **kw)

    def test_plan_names_the_pending_migration_and_a_code_only_rollback(self):
        before = self.db.stat().st_mtime_ns
        plan = deploy.plan(self.repo, self.target, now=QUIET)
        self.assertTrue(plan["ok"])
        self.assertEqual(plan["migrations_pending"], [PROBE])
        self.assertEqual(plan["changes"]["migrations_added"], [PROBE])
        self.assertTrue(any("storage.migrate up" in s for s in plan["steps"]))
        self.assertIn(f"git checkout --detach {self.base[:12]}", plan["rollback"])
        self.assertNotIn("reset", plan["rollback"])
        self.assertEqual(self.db.stat().st_mtime_ns, before)  # read-only
        self.assertEqual(self.head(), self.base)

    def test_apply_moves_the_code_runs_the_migration_and_keeps_a_backup(self):
        services = FakeServices()
        result = self.apply(services)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.head(), self.target)
        self.assertIn(PROBE, deploy.applied_migrations(self.db))
        self.assertNotIn(PROBE, deploy.applied_migrations(result["backup"]))  # taken before the migration
        self.assertEqual(services.calls, ["quiesce", "resume"])
        self.assertEqual(deploy.verify(self.repo, self.target)["problems"], [])

    def test_a_failed_migration_puts_the_code_back_and_restarts(self):
        run(["git", "checkout", "-q", "-B", "broken", self.target], self.repo)
        (self.repo / deploy.MIGRATIONS / f"{PROBE}.sql").write_text("THIS IS NOT SQL;\n")
        run(["git", "commit", "-qam", "broken migration"], self.repo)
        self.target = self.head()
        run(["git", "checkout", "-q", "--detach", self.base], self.repo)
        services = FakeServices()
        result = self.apply(services)
        self.assertFalse(result["ok"])
        self.assertIn("storage.migrate up failed", result["error"])
        self.assertEqual(self.head(), self.base)
        self.assertEqual(services.calls, ["quiesce", "resume"])

    def test_hand_edits_and_busy_windows_stop_before_anything_is_stopped(self):
        services = FakeServices()
        with self.assertRaisesRegex(deploy.Refused, "scheduled-work window"):
            deploy.apply(self.repo, self.target, services, sys.executable, now=dt.datetime(2026, 11, 24, 6, 40))
        config = self.repo / "content-pipeline" / "config.py"
        config.write_text(config.read_text() + "\n# edited on the host\n")
        self.assertFalse(deploy.plan(self.repo, self.target, now=QUIET)["ok"])
        with self.assertRaisesRegex(deploy.Refused, "edited by hand"):
            self.apply(services)
        self.assertEqual(services.calls, [])

    def test_jobs_still_running_leave_production_as_it_was(self):
        services = FakeServices(fail_quiesce=True)
        with self.assertRaisesRegex(deploy.Refused, "running now"):
            self.apply(services)
        self.assertEqual(services.calls, ["quiesce", "resume"])
        self.assertEqual(self.head(), self.base)

    def test_verify_catches_a_checkout_moved_without_its_migration(self):
        run(["git", "merge", "-q", "--ff-only", self.target], self.repo)
        report = deploy.verify(self.repo, self.target, services=FakeServices())
        self.assertFalse(report["ok"])
        self.assertEqual(report["migrations_missing"], [PROBE])

    def test_rollback_moves_code_only_and_says_the_database_was_kept(self):
        self.assertTrue(self.apply(FakeServices())["ok"])
        before = self.db.read_bytes()
        services = FakeServices()
        report = deploy.rollback(self.repo, self.base, services)
        self.assertEqual(self.head(), self.base)
        self.assertEqual(report["database_left_with_newer_migrations"], [PROBE])
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(services.calls, ["quiesce", "resume"])
        with self.assertRaisesRegex(deploy.Refused, "not an earlier commit"):
            deploy.rollback(self.repo, self.target, FakeServices())

    def test_no_destructive_git_command_exists_in_the_script(self):
        source = (ROOT / "ops" / "deploy.py").read_text()
        calls = re.findall(r'git\(repo, ([^)]*)\)', source)
        self.assertTrue(calls)
        for words in ('"reset"', '"stash"', '"clean"', '"--force"', '"-f"', '"push"'):
            self.assertFalse(any(words in call for call in calls), words)


if __name__ == "__main__":
    unittest.main()
