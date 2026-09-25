"""Print the notices the phone server queued (see violations.py, _queue_print).

Run by the "Violations Print" task in the signed-in Windows session: at
sign-in, every minute, and straight away when the server queues a notice.
Each job is data/violations/print-queue/<ref>.json = {"pdf": ..., "printer": ...}.
What became of it goes back to the server in <ref>.result.json (and to print.log).
"""

import glob
import json
import os
import time
from datetime import datetime

import violations as V

ROOT = os.path.dirname(os.path.abspath(__file__))
QUEUE = V.PRINT_QUEUE
LOG = os.path.join(ROOT, "data", "violations", "print.log")
MAX_TRIES = 5


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def report(ref, res):
    V._write(os.path.join(QUEUE, f"{ref}.result.json"),
             res | {"at": datetime.now().isoformat(timespec="seconds")})


def tidy():
    """Answers the server never collected (it waits a couple of minutes) — drop after an hour."""
    for f in glob.glob(os.path.join(QUEUE, "*.result.json")):
        try:
            if time.time() - os.path.getmtime(f) > 3600:
                os.remove(f)
        except OSError:
            pass


def main():
    os.makedirs(QUEUE, exist_ok=True)
    tidy()
    tried = set()
    while True:     # a notice can be queued while this is printing another
        jobs = [j for j in sorted(glob.glob(os.path.join(QUEUE, "*.json")))
                if j not in tried and not j.endswith(".result.json")]
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
            res = V.send_to_printer(j["pdf"], j["printer"])
            report(ref, res)
            if res["ok"]:
                how = f"{res['pages']} page(s) printed" if res.get("pages") else "handed to Windows"
                log(f"{ref}: {how} on {j['printer']}")
                os.remove(job)
                continue
            tries = j.get("tries", 0) + 1
            log(f"{ref}: didn't print (try {tries}): {res['error']}")
            if not res.get("retry"):
                # It's sitting in the Windows print queue (printer off, out of paper...):
                # Windows prints it when the printer is ready, and sending it again
                # would print two copies. So it's Windows' job now.
                log(f"{ref}: left in the Windows print queue")
                os.remove(job)
            elif tries >= MAX_TRIES:
                os.replace(job, job[:-5] + ".failed")
            else:
                j["tries"] = tries
                with open(job, "w", encoding="utf-8") as f:
                    json.dump(j, f)


if __name__ == "__main__":
    main()
