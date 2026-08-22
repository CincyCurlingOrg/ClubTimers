#!/usr/bin/env python3
"""Startup launcher for the Pi: updates the ClubTimer repo from git, then runs the app.

Installed as a fixed, stable path referenced by clubtimer.service (systemd),
kept OUTSIDE the git-managed clone directory so a bad push can never break
the launcher itself and there's no chicken/egg problem on first boot.
"""
import os, sys, time, subprocess, traceback

# ------------ Config ------------
GIT_HUB_REPO = "https://github.com/CincyCurlingOrg/ClubTimer.git"          # placeholder: e.g. "git@github.com:yourorg/CurlingTimer.git"
BRANCH = "CCCRelease"
REPO_DIR = "/home/jon/CurlingTimer"          # where the repo is cloned/kept on the Pi
APP_RELATIVE_PATH = "ClubTimer.py"  # entry point inside the repo
PYTHON_BIN = sys.executable or "/usr/bin/python3"
LOG_PATH = "/home/admin/clubtimer_launcher.log"

# First clone must succeed (nothing to run otherwise) - wait longer for wifi.
CLONE_RETRY_DELAYS = [5, 10, 20, 40, 60]
# Once we have a cached clone, don't block startup long on a flaky pull.
PULL_RETRY_DELAYS = [3, 6, 12]

GIT_TIMEOUT_S = 30


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def run_git(args, cwd=None):
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, timeout=GIT_TIMEOUT_S,
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return False, result.stderr.strip()
        return True, result.stdout.strip()
    except Exception as e:
        return False, str(e)


def ensure_repo_cloned():
    if os.path.isdir(os.path.join(REPO_DIR, ".git")):
        return
    log(f"No local clone at {REPO_DIR}, cloning {GIT_HUB_REPO} ({BRANCH})")
    for attempt, delay in enumerate([0, *CLONE_RETRY_DELAYS], start=1):
        if delay:
            time.sleep(delay)
        ok, output = run_git(["clone", "--branch", BRANCH, "--depth", "1", GIT_HUB_REPO, REPO_DIR])
        if ok:
            log("Clone succeeded")
            return
        log(f"Clone attempt {attempt} failed: {output}")
    log("FATAL: could not clone repo and no cached copy exists, nothing to run")
    sys.exit(1)


def update_repo():
    for attempt, delay in enumerate([0, *PULL_RETRY_DELAYS], start=1):
        if delay:
            time.sleep(delay)
        ok, output = run_git(["fetch", "--depth", "1", "origin", BRANCH], cwd=REPO_DIR)
        if not ok:
            log(f"Pull attempt {attempt} (fetch) failed: {output}")
            continue
        ok, output = run_git(["reset", "--hard", f"origin/{BRANCH}"], cwd=REPO_DIR)
        if not ok:
            log(f"Pull attempt {attempt} (reset) failed: {output}")
            continue
        log("Repo updated to latest")
        return
    log("Pull failed after retries, running cached code already on disk")


def launch_app():
    app_path = os.path.join(REPO_DIR, APP_RELATIVE_PATH)
    if not os.path.isfile(app_path):
        log(f"FATAL: app entry point not found at {app_path}")
        sys.exit(1)
    log(f"Launching {app_path}")
    os.chdir(os.path.dirname(app_path))
    os.execv(PYTHON_BIN, [PYTHON_BIN, app_path])


if __name__ == "__main__":
    try:
        ensure_repo_cloned()
        update_repo()
        launch_app()
    except Exception:
        log("FATAL: unhandled exception in launcher\n" + traceback.format_exc())
        sys.exit(1)
