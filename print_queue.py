"""Print the notices the phone server queued (see violations.py, _queue_print).

Run by the "Violations Print" task in the signed-in Windows session: at
sign-in, every minute, and straight away when the server queues a notice.
Each job is data/violations/print-queue/<ref>.json = {"pdf": ..., "printer": ...}.
"""

import glob
import json
import os
import subprocess
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(ROOT, "data", "violations", "print-queue")
LOG = os.path.join(ROOT, "data", "violations", "print.log")
SUMATRA = os.path.join(ROOT, "tools", "SumatraPDF", "SumatraPDF.exe")
MAX_TRIES = 5


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def main():
    tried = set()
    while True:     # a notice can be queued while this is printing another
        jobs = [j for j in sorted(glob.glob(os.path.join(QUEUE, "*.json"))) if j not in tried]
        if not jobs:
            return
        for job in jobs:
            tried.add(job)
            ref = os.path.basename(job)[:-5]
            try:
                with open(job, encoding="utf-8") as f:
                    j = json.load(f)
            except (OSError, ValueError) as e:
                log(f"{ref}: can't read the job ({e})")
                continue
            if not os.path.exists(j["pdf"]):
                log(f"{ref}: {j['pdf']} is missing, dropped")
                os.remove(job)
                continue
            r = subprocess.run([SUMATRA, "-print-to", j["printer"], "-print-settings", "fit",
                                "-silent", j["pdf"]],
                               capture_output=True, text=True, timeout=180)
            if r.returncode == 0:
                log(f"{ref}: sent to {j['printer']}")
                os.remove(job)
                continue
            tries = j.get("tries", 0) + 1
            log(f"{ref}: print failed (try {tries}): {(r.stderr or r.stdout).strip()[:300]}")
            if tries >= MAX_TRIES:
                os.replace(job, job[:-5] + ".failed")
            else:
                j["tries"] = tries
                with open(job, "w", encoding="utf-8") as f:
                    json.dump(j, f)


if __name__ == "__main__":
    main()
