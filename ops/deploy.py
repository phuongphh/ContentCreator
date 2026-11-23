#!/usr/bin/env python3
"""Production deploy for ContentCreator, as one command instead of a written plan.

23/11: the Production preparation for b9df140 was written by hand and said the
new migration "runs automatically at startup" (no service runs it) and that
rollback was `git reset --hard` plus restoring the old DB (both forbidden by
ops/fleet/README.md). Nothing checked the plan against the code. The steps now
live here, computed from the commits and run in a fixed order, and CI runs them
on a copy (content-pipeline/tests/test_ops_deploy.py).

    python3 ops/deploy.py plan <sha> [--fetch]   # read-only: what apply would do
    python3 ops/deploy.py apply <sha> [--backup-dir DIR]
    python3 ops/deploy.py verify <sha>           # read-only: Production is at <sha> and complete
    python3 ops/deploy.py rollback <sha>         # code only, to an earlier commit

Every command prints one JSON object on stdout and exits non-zero when it did
not reach its goal. The checkout is the repository this file belongs to.

Never: reset, stash, clean, force, or write to the database except through the
product's own migration runner. Rolling the database back is a separate
decision taken after reconciling the writes made since (ops/fleet/README.md).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PIPELINE = "content-pipeline"
MIGRATIONS = f"{PIPELINE}/storage/migrations"
LAUNCHD = f"{PIPELINE}/launchd"
BOT = "com.ai5phut.bot"
SHA = re.compile(r"[0-9a-f]{40}")
# Scheduled runs that write the DB or publish; deploying across them is avoidable risk.
BUSY_WINDOWS = (("06:00", "07:30", "drama/AI pipelines"), ("23:25", "23:45", "disk cleanup"))


class Refused(Exception):
    """The command stopped before changing anything it could not undo."""


def git(repo, *args, check=True):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise Refused(f"git {' '.join(args)}: {result.stderr.strip() or result.stdout.strip()}")
    return result


def resolve(repo, ref, fetch=False):
    if fetch:
        git(repo, "fetch", "--quiet", "origin")
    found = git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
    if found.returncode != 0:
        raise Refused(f"commit {ref} is not in this checkout; run `git fetch origin` or use --fetch")
    return found.stdout.strip()


def tracked_edits(repo):
    return [line[3:] for line in git(repo, "status", "--porcelain", "--untracked-files=no").stdout.splitlines()]


def migrations_at(repo, sha):
    names = git(repo, "ls-tree", "--name-only", f"{sha}:{MIGRATIONS}", check=False).stdout.split()
    return sorted(Path(n).stem for n in names if n.endswith(".sql") and not n.endswith("_down.sql"))


def default_db(repo):
    return repo / PIPELINE / "storage" / "content.db"


def applied_migrations(db):
    """Versions recorded by storage/migrate.py, read without writing (None: no DB yet)."""
    if not Path(db).exists():
        return None
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='_migrations'").fetchone():
            return set()
        return {row[0] for row in conn.execute("SELECT version FROM _migrations")}
    finally:
        conn.close()


def changes(repo, base, target):
    files = git(repo, "diff", "--name-status", "--no-renames", f"{base}..{target}").stdout.splitlines()
    names = [line.split("\t", 1)[1] for line in files]
    return {
        "files": len(names),
        "migrations_added": sorted(Path(n).stem for n in names
                                   if n.startswith(MIGRATIONS + "/") and n.endswith(".sql") and not n.endswith("_down.sql")),
        "launchd_changed": sorted(n for n in names if n.startswith(LAUNCHD + "/") and (n.endswith(".plist") or n.endswith(".sh"))),
        "requirements_changed": f"{PIPELINE}/requirements.txt" in names,
        "wrappers_changed": sorted(n for n in names if n in (f"{PIPELINE}/run_module.sh", f"{PIPELINE}/run_pipeline.sh")),
    }


def busy_now(now=None):
    clock = (now or dt.datetime.now()).strftime("%H:%M")
    return [why for start, end, why in BUSY_WINDOWS if start <= clock <= end]


def plan(repo, target, db=None, fetch=False, now=None):
    """Everything apply would do, from the commits and the database. Writes nothing."""
    head = resolve(repo, "HEAD")
    target = resolve(repo, target, fetch)
    edits = tracked_edits(repo)
    forward = git(repo, "merge-base", "--is-ancestor", head, target, check=False).returncode == 0
    applied = applied_migrations(db or default_db(repo))
    pending = [v for v in migrations_at(repo, target) if applied is None or v not in applied]
    diff = changes(repo, head, target)
    steps = ["check: no scheduled job is running", "stop the bot and pause the scheduled jobs",
             "back up the database (sqlite backup API) outside the checkout",
             f"git merge --ff-only {target}"]
    if diff["requirements_changed"]:
        steps.append("venv/bin/pip install -r requirements.txt")
    if pending:
        steps.append(f"python -m storage.migrate up (pending: {', '.join(pending)})")
    steps += ["launchd/install.sh (re-render every plist, start the bot and the scheduled jobs)",
              f"verify {target[:12]}"]
    blockers = []
    if edits:
        blockers.append(f"tracked files edited by hand: {', '.join(edits)}")
    if head != target and not forward:
        blockers.append(f"{target[:12]} does not contain Production {head[:12]}: a fast-forward is impossible")
    return {
        "command": "plan", "ok": not blockers, "repository": str(repo), "production": head, "target": target,
        "already_there": head == target, "blockers": blockers, "changes": diff,
        "migrations_pending": pending, "database_found": applied is not None,
        "steps": [] if head == target else steps,
        "rollback": f"python3 ops/deploy.py rollback {head}  (code only: git checkout --detach {head[:12]}; "
                    "the database is never restored by a rollback)",
        "busy_window_now": busy_now(now),
    }


def verify(repo, target, db=None, services=None):
    """Production is exactly at target, complete and running. Writes nothing."""
    head = resolve(repo, "HEAD")
    target = resolve(repo, target)
    edits = tracked_edits(repo)
    applied = applied_migrations(db or default_db(repo))
    expected = migrations_at(repo, target)
    missing = expected if applied is None else [v for v in expected if v not in applied]
    problems = []
    if head != target:
        problems.append(f"checkout is at {head[:12]}, not {target[:12]}")
    if edits:
        problems.append(f"tracked files edited by hand: {', '.join(edits)}")
    if applied is None:
        problems.append("database not found")
    elif missing:
        problems.append(f"migrations not applied: {', '.join(missing)}")
    report = {"command": "verify", "production": head, "target": target, "tracked_edits": edits,
              "migrations_missing": missing}
    if services is not None:
        state = services.status()
        report["services"] = state
        if not state.get("bot_pid"):
            problems.append("the bot is not running")
        if state.get("unloaded"):
            problems.append(f"scheduled jobs not loaded: {', '.join(state['unloaded'])}")
    report.update(ok=not problems, problems=problems)
    return report


def backup(db, directory):
    directory.mkdir(parents=True, exist_ok=False)
    target = directory / "content.db"
    source = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        copy = sqlite3.connect(target)
        source.backup(copy)
        copy.close()
    finally:
        source.close()
    check = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    try:
        if check.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise Refused(f"backup at {target} failed quick_check")
    finally:
        check.close()
    return str(target)


def run_python(repo, python, *args):
    result = subprocess.run([python, *args], cwd=repo / PIPELINE, capture_output=True, text=True)
    if result.returncode != 0:
        raise Refused(f"{' '.join(args)} failed: {(result.stderr or result.stdout).strip()[-800:]}")
    return result.stdout.strip()


def apply(repo, target, services, python, db=None, backup_dir=None, now=None, allow_busy=False):
    """Move Production to target in a fixed order; on a failure before the services restart, put the code back."""
    db = Path(db or default_db(repo))
    planned = plan(repo, target, db, now=now)
    log = {"command": "apply", "plan": planned, "done": []}
    if not planned["ok"]:
        raise Refused("; ".join(planned["blockers"]))
    if planned["already_there"]:
        log["verify"] = verify(repo, planned["target"], db, services)
        log["ok"] = log["verify"]["ok"]
        return log
    if planned["busy_window_now"] and not allow_busy:
        raise Refused(f"now is a scheduled-work window ({', '.join(planned['busy_window_now'])}); "
                      "deploy outside it or pass --allow-busy-window")
    base, target = planned["production"], planned["target"]
    quiesce(services)
    log["done"].append("services stopped")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    where = Path(backup_dir) if backup_dir else REPO.parent / "ContentCreator-backups" / f"{stamp}-{target[:12]}"
    try:
        if db.exists():
            log["backup"] = backup(db, where)
            log["done"].append("database backed up")
        git(repo, "merge", "--ff-only", "--quiet", target)
        log["done"].append(f"checkout at {target[:12]}")
        if planned["changes"]["requirements_changed"]:
            run_python(repo, python, "-m", "pip", "install", "--quiet", "-r", "requirements.txt")
            log["done"].append("dependencies installed")
        if planned["migrations_pending"]:
            log["migrate"] = run_python(repo, python, "-m", "storage.migrate", "up")
            log["done"].append("migrations applied")
    except Exception as failure:  # noqa: BLE001 — whatever failed, Production must come back up
        # The services are still stopped. Put the code back where it was (the database is untouched
        # except by migrations that committed, which the old code tolerates as added columns), then
        # restart, so Production is never left down or half-moved.
        if resolve(repo, "HEAD") != base:
            git(repo, "checkout", "--quiet", "--detach", base)
            log["done"].append(f"code returned to {base[:12]}")
        services.resume()
        log["done"].append("services restarted on the previous code")
        log.update(ok=False, error=str(failure))
        return log
    services.resume()
    log["done"].append("services restarted")
    log["verify"] = verify(repo, target, db, services)
    log["ok"] = log["verify"]["ok"]
    if not log["ok"]:
        log["next"] = f"python3 ops/deploy.py rollback {base}"
    return log


def rollback(repo, target, services, db=None):
    """Code only, to an earlier commit. The database stays as it is."""
    head = resolve(repo, "HEAD")
    target = resolve(repo, target)
    if tracked_edits(repo):
        raise Refused(f"tracked files edited by hand: {', '.join(tracked_edits(repo))}")
    if git(repo, "merge-base", "--is-ancestor", target, head, check=False).returncode != 0:
        raise Refused(f"{target[:12]} is not an earlier commit of Production {head[:12]}")
    applied = applied_migrations(db or default_db(repo)) or set()
    newer = sorted(applied - set(migrations_at(repo, target)))
    quiesce(services)
    try:
        git(repo, "checkout", "--quiet", "--detach", target)
    finally:
        services.resume()
    report = verify(repo, target, db, services)
    report.update(command="rollback", rolled_back_from=head,
                  database_left_with_newer_migrations=newer,
                  note="database not restored: rolling it back is a separate decision after reconciling new writes")
    return report


def quiesce(services):
    """Stop everything, or restart what was stopped and refuse."""
    try:
        services.quiesce()
    except Exception:
        services.resume()
        raise


class Launchd:
    """The bot and the scheduled jobs on the Mac Mini (brain runbook products/content-creator/_ops/restart)."""

    def __init__(self, repo):
        self.repo = repo
        self.labels = sorted(p.stem for p in (repo / LAUNCHD).glob("com.ai5phut.*.plist"))
        self.domain = f"gui/{os.getuid()}"

    def _list(self):
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, check=True).stdout
        seen = {}
        for line in out.splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) == 3 and parts[2] in self.labels:
                seen[parts[2]] = int(parts[0]) if parts[0].isdigit() else None
        return seen

    def status(self):
        seen = self._list()
        return {"bot_pid": seen.get(BOT), "loaded": sorted(seen),
                "unloaded": [label for label in self.labels if label not in seen]}

    def quiesce(self, timeout=90):
        seen = self._list()
        running = [label for label, pid in seen.items() if pid and label != BOT]
        if running:
            raise Refused(f"scheduled jobs are running now: {', '.join(running)}; wait for them to finish")
        old = seen.get(BOT)
        subprocess.run(["launchctl", "bootout", f"{self.domain}/{BOT}"], capture_output=True)
        # Scheduled jobs are loaded by the login session: `remove` pauses them (bootout in gui/ fails).
        for label in self.labels:
            if label != BOT:
                subprocess.run(["launchctl", "remove", label], capture_output=True)
        deadline = time.time() + timeout
        while time.time() < deadline:
            alive = old and subprocess.run(["kill", "-0", str(old)], capture_output=True).returncode == 0
            if BOT not in self._list() and not alive:
                return
            time.sleep(2)
        raise Refused(f"the bot (pid {old}) did not stop within {timeout}s")

    def resume(self):
        script = self.repo / LAUNCHD / "install.sh"
        subprocess.run([str(script), "install"], capture_output=True, text=True)
        for _ in range(2):
            if self.status()["bot_pid"]:
                break
            # The first bootstrap right after a bootout often fails with "5: Input/output error".
            time.sleep(3)
            subprocess.run([str(script), "reload", BOT], capture_output=True, text=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=("plan", "apply", "verify", "rollback"))
    parser.add_argument("sha")
    parser.add_argument("--fetch", action="store_true", help="plan/apply: git fetch origin first")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--allow-busy-window", action="store_true")
    parser.add_argument("--python", default=str(REPO / PIPELINE / "venv" / "bin" / "python3"))
    args = parser.parse_args(argv)
    services = Launchd(REPO) if platform.system() == "Darwin" else None
    try:
        if args.command == "plan":
            result = plan(REPO, args.sha, fetch=args.fetch)
        elif args.command == "verify":
            result = verify(REPO, args.sha, services=services)
        else:
            if services is None:
                raise Refused("apply and rollback run only on the Mac Mini (launchd)")
            if args.command == "apply":
                if args.fetch:
                    resolve(REPO, args.sha, fetch=True)
                result = apply(REPO, args.sha, services, args.python, backup_dir=args.backup_dir,
                               allow_busy=args.allow_busy_window)
            else:
                result = rollback(REPO, args.sha, services)
    except Refused as refusal:
        result = {"command": args.command, "ok": False, "refused": str(refusal)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
