import sys, os, tempfile, unittest
from datetime import datetime
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import violations as V, notify

CFG = {"reminders": {"daily_to": "me@x.com", "daily_hour": 23}}

class Daily(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "letters"))
        self.recs = []
        for n, (park, test) in enumerate([("Freeburg", False), ("Cokato", False), ("Joplin", True)]):
            pdf = os.path.join(self.tmp, "letters", f"V-{n}.pdf")
            open(pdf, "wb").write(b"%PDF" + b"x" * 100)
            self.recs.append({"ref": f"V-{n}", "park": park, "lot": str(n), "tenant_name": "T", "warning": "1",
                              "issued_by": "Manager", "correct_by": "2026-10-26", "test": test,
                              "saved_at": "2026-10-19T10:00:00", "pdf": pdf})
        self.recs.append(dict(self.recs[0], ref="V-old", saved_at="2026-10-18T10:00:00"))
        self.p = [mock.patch.object(V, "DATA", self.tmp), mock.patch.object(V, "DAILY", os.path.join(self.tmp, "d.json")),
                  mock.patch.object(V, "load_config", return_value=CFG),
                  mock.patch.object(V, "LOG", os.path.join(self.tmp, "issued.json")),
                  mock.patch.object(notify, "configured", return_value=True)]
        for p in self.p: p.start()
        V._write(V.LOG, self.recs)

    def tearDown(self):
        for p in self.p: p.stop()

    def test_one_email_with_todays_real_notices_once(self):
        with mock.patch.object(notify, "send", return_value={"ok": True}) as s:
            r = V.daily_digest(now=datetime(2026, 10, 19, 23, 5))
            self.assertEqual(r["count"], 2)                 # not the test one, not yesterday's
            self.assertEqual(s.call_count, 1)
            self.assertEqual(len(s.call_args[0][3]), 2)     # both attached to the one email
            V.daily_digest(now=datetime(2026, 10, 19, 23, 30))
            self.assertEqual(s.call_count, 1)               # never twice in a day

    def test_not_before_11pm_nothing_issued_and_retry_after_failure(self):
        with mock.patch.object(notify, "send", return_value={"ok": False}) as s:
            self.assertEqual(V.daily_digest(now=datetime(2026, 10, 19, 22, 59))["skipped"], "not due")
            V.daily_digest(now=datetime(2026, 10, 19, 23, 0))
            V.daily_digest(now=datetime(2026, 10, 19, 23, 10))
            self.assertEqual(s.call_count, 2)               # failed, so it tried again
            self.assertEqual(V.daily_digest(now=datetime(2026, 10, 20, 23, 0))["count"], 0)
            self.assertEqual(s.call_count, 2)               # a day with nothing sends nothing

    def test_splits_big_days(self):
        with mock.patch.object(notify, "send", return_value={"ok": True}) as s:
            V.daily_digest(now=datetime(2026, 10, 19, 23, 5), max_mb=0.00005)
            self.assertEqual(s.call_count, 2)

if __name__ == "__main__":
    unittest.main()
