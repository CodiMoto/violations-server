"""Keeps this computer's Violations Server up to date with GitHub.

Run every hour by the "Violations Server - Updates" task (install.ps1), and
by "Check for updates" in Violations Settings. When a new version has been
pushed to GitHub (main):

  download it → install any new libraries → run the tests on the new copy →
  wait until nobody has used the phone app for 10 minutes → put the new files
  in → restart the phone server → if it doesn't come back, put the old ones back
  (and don't try that version again).

Only files that are part of the program are ever written or removed. This
computer's login, settings and data are never touched: config.json,
violations_config.json, data/, tools/, venv/ (the same list .gitignore keeps
out of GitHub). New settings a later version needs come from
violations_config.example.json at load time — see violations.load_config.

A folder that is a git checkout (Codi's development copy) is left alone.

The repository is public, so no GitHub account or key is needed. (If it is
ever made private, put a read-only GitHub key in data/github_token.)

  pythonw updater.py            check, and update if there's something new
  python  updater.py --now      don't wait for the phone app to be idle
"""

import io
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request
import zipfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = "CodiMoto/violations-server"
BRANCH = "main"
TASK = "Violations Server"
DATA = os.path.join(HERE, "data")
WORK = os.path.join(DATA, "update")
STATE = os.path.join(DATA, "update.json")
TOKEN = os.path.join(DATA, "github_token")
LOG = os.path.join(DATA, "update.log")
ACTIVITY = os.path.join(DATA, "violations", "last_activity")   # touched by mgrserver
VENV_PY = os.path.join(HERE, "venv", "Scripts", "python.exe")
IDLE_MINUTES = 10
NO_WINDOW = 0x08000000

# Never written, replaced or removed by an update — this computer's own.
KEEP_FILES = {"config.json", "violations_config.json"}
KEEP_DIRS = {"data", "tools", "venv", ".git", "__pycache__"}


def log(msg):
    os.makedirs(DATA, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def status():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(**changes):
    st = status() | changes
    os.makedirs(DATA, exist_ok=True)
    with open(STATE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2)
    os.replace(STATE + ".tmp", STATE)
    return st


def _kept(rel):
    parts = rel.replace("\\", "/").split("/")
    return parts[0] in KEEP_DIRS or rel in KEEP_FILES or "__pycache__" in parts or rel.endswith(".pyc")


# ---- GitHub ---------------------------------------------------------------------

class KeyProblem(Exception):
    pass


def _get(url, accept):
    headers = {"Accept": accept, "User-Agent": "violations-server-updater",
               "X-GitHub-Api-Version": "2022-11-28"}
    if os.path.exists(TOKEN):                  # only needed if the repository is made private
        with open(TOKEN, encoding="utf-8-sig") as f:
            headers["Authorization"] = f"Bearer {f.read().strip()}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 404):
            raise KeyProblem("GitHub says the program isn't there any more (was the repository made private?). "
                             "Ask Codi.")
        raise


def latest_version():
    return _get(f"https://api.github.com/repos/{REPO}/commits/{BRANCH}",
                "application/vnd.github.sha").decode().strip()


def download(sha, into):
    """The whole program at that version, unpacked into `into` (no top folder)."""
    data = _get(f"https://api.github.com/repos/{REPO}/zipball/{sha}", "application/vnd.github+json")
    shutil.rmtree(into, ignore_errors=True)
    files = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            rel = info.filename.split("/", 1)[-1]          # drop "CodiMoto-violations-server-<sha>/"
            if not rel or info.is_dir() or ".." in rel.split("/") or _kept(rel):
                continue
            dest = os.path.join(into, *rel.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(z.read(info))
            files.append(rel)
    return sorted(files)


# ---- the phone server -------------------------------------------------------------

def _port():
    try:
        with open(os.path.join(HERE, "violations_config.json"), encoding="utf-8-sig") as f:
            return int(json.load(f)["server"]["port"])
    except Exception:
        return 8790


def _listener_pid(port):
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True,
                         creationflags=NO_WINDOW).stdout
    for line in out.splitlines():
        p = line.split()
        if len(p) == 5 and p[3] == "LISTENING" and p[1].endswith(f":{port}"):
            return int(p[4])
    return None


def stop_server():
    port = _port()
    # The task runs venv\Scripts\pythonw.exe, which starts the real Python as a
    # child: end the task (the launcher) and the process holding the port.
    subprocess.run(["schtasks", "/End", "/TN", TASK], capture_output=True, creationflags=NO_WINDOW)
    for _ in range(10):
        pid = _listener_pid(port)
        if not pid:
            return
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, creationflags=NO_WINDOW)
        time.sleep(1)


def start_server():
    subprocess.run(["schtasks", "/Run", "/TN", TASK], capture_output=True, creationflags=NO_WINDOW)


def server_ok(wait=45):
    """Is the new copy answering — the page AND the Python API behind it?"""
    port, until = _port(), time.time() + wait
    while time.time() < until:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
                page = r.status == 200
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/me", timeout=5)
                api = False
            except urllib.error.HTTPError as e:
                api = e.code == 401                    # "sign in first" = the API code loaded
            if page and api:
                return True
        except OSError:
            pass
        time.sleep(2)
    return False


def idle_minutes():
    try:
        return (time.time() - os.path.getmtime(ACTIVITY)) / 60
    except OSError:
        return 1e9


# ---- the update ---------------------------------------------------------------------

def _same(a, b):
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def _run_tests(folder):
    r = subprocess.run([VENV_PY, "-m", "unittest", "discover", "-s", "tests"], cwd=folder,
                       capture_output=True, text=True, timeout=600, creationflags=NO_WINDOW)
    return r.returncode == 0, (r.stdout + r.stderr)[-3000:]


def apply(sha, new_dir, new_files):
    """Swap the files in, restart, check; on failure put everything back."""
    old_files = status().get("files") or []
    backup = os.path.join(WORK, "previous")
    shutil.rmtree(backup, ignore_errors=True)
    changed = [f for f in new_files if not _same(os.path.join(new_dir, f), os.path.join(HERE, f))]
    removed = [f for f in old_files if f not in new_files and not _kept(f)
               and os.path.exists(os.path.join(HERE, f))]
    added = [f for f in changed if not os.path.exists(os.path.join(HERE, f))]
    for f in changed + removed:
        src = os.path.join(HERE, f)
        if os.path.exists(src):
            os.makedirs(os.path.dirname(os.path.join(backup, f)), exist_ok=True)
            shutil.copy2(src, os.path.join(backup, f))

    stop_server()
    if _listener_pid(_port()):
        start_server()
        return False, "the phone server wouldn't stop, so nothing was changed"
    try:
        for f in changed:
            dest = os.path.join(HERE, f)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(os.path.join(new_dir, f), dest)
        for f in removed:
            os.remove(os.path.join(HERE, f))
    finally:
        start_server()
    if server_ok():
        return True, f"{len(changed)} file(s) changed, {len(removed)} removed"

    log(f"{sha[:7]}: the phone server didn't come back — putting the old version back")
    stop_server()
    for f in added:
        try:
            os.remove(os.path.join(HERE, f))
        except OSError:
            pass
    for root, _, names in os.walk(backup):
        for n in names:
            src = os.path.join(root, n)
            shutil.copy2(src, os.path.join(HERE, os.path.relpath(src, backup)))
    start_server()
    return False, "the new version didn't start, so the old one was put back" + \
        ("" if server_ok() else " — and the server still isn't answering: restart the computer")


def check(now=False):
    if os.path.isdir(os.path.join(HERE, ".git")):
        return _save(last_check=_stamp(), result="This is the development copy — it's updated with git, not here.")
    try:
        sha = latest_version()
    except KeyProblem as e:
        return _save(last_check=_stamp(), result=str(e), problem=True)
    except Exception as e:
        return _save(last_check=_stamp(), result=f"Couldn't reach GitHub ({type(e).__name__}) — will try again.")

    st = status()
    if sha == st.get("installed"):
        return _save(last_check=_stamp(), result="Up to date.", problem=False)
    if sha == st.get("bad") and not now:
        return _save(last_check=_stamp(), result=f"Version {sha[:7]} didn't work here, so it wasn't kept. "
                                                 "Waiting for a newer one.", problem=True)
    if not now and idle_minutes() < IDLE_MINUTES:
        return _save(last_check=_stamp(), result=f"New version {sha[:7]} waiting — the phone app is in use, "
                                                 "it'll go in once it's been quiet for 10 minutes.")

    new_dir = os.path.join(WORK, "new")
    files = download(sha, new_dir)
    log(f"{sha[:7]}: downloaded {len(files)} files")

    req_new, req_old = os.path.join(new_dir, "requirements.txt"), os.path.join(HERE, "requirements.txt")
    if os.path.exists(req_new) and not _same(req_new, req_old):
        r = subprocess.run([VENV_PY, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                            "-r", req_new], capture_output=True, text=True, timeout=900, creationflags=NO_WINDOW)
        if r.returncode:
            log(f"{sha[:7]}: library install failed\n{r.stdout[-2000:]}{r.stderr[-2000:]}")
            return _save(last_check=_stamp(), bad=sha, problem=True,
                         result=f"Version {sha[:7]} needs libraries that wouldn't install, so it wasn't put in.")

    ok, out = _run_tests(new_dir)
    if not ok:
        log(f"{sha[:7]}: tests failed — not installed\n{out}")
        return _save(last_check=_stamp(), bad=sha, problem=True,
                     result=f"Version {sha[:7]} failed its checks, so it wasn't put in.")

    ok, what = apply(sha, new_dir, files)
    log(f"{sha[:7]}: {'installed' if ok else 'NOT installed'} — {what}")
    shutil.rmtree(new_dir, ignore_errors=True)
    if not ok:
        return _save(last_check=_stamp(), bad=sha, problem=True, result=f"Version {sha[:7]}: {what}.")
    return _save(last_check=_stamp(), installed=sha, installed_at=_stamp(), files=files, bad=None,
                 problem=False, result=f"Updated to version {sha[:7]}.")


def _stamp():
    return datetime.now().isoformat(timespec="seconds")


def main():
    os.makedirs(WORK, exist_ok=True)
    lock = os.path.join(WORK, "running")
    try:                                       # one at a time (hourly task + the Settings button)
        if time.time() - os.path.getmtime(lock) < 3600:
            return
    except OSError:
        pass
    with open(lock, "w") as f:
        f.write(str(os.getpid()))
    try:
        st = check(now="--now" in sys.argv)
        print(st.get("result"))
    except Exception:
        log("update check failed\n" + traceback.format_exc())
        _save(last_check=_stamp(), result="The update check hit an error — see data\\update.log.", problem=True)
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


if __name__ == "__main__":
    main()
