import sys, os, tempfile, unittest
from datetime import date
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import violations as V, notify

CFG = {"reminders": {"email": True, "email_me": "me@x.com", "email_writer": True, "moornings": True,
                     "send_from_hour": 0, "send_until_hour": 24},
       "users": [{"name": "Writer A", "email": "a@x.com"}, {"name": "Writer B"}]}
TODAY = date(2026, 11, 11)


def due(hid, park, lot, by="Writer A", test=False, correct_by=TODAY, what="Trash in yard"):
    note = (f"VIOLATION NOTICE - Warning 1\n{park} lot {lot} - Tenant {lot}\n{what}\n"
            f"Correct by: {correct_by:%m/%d/%Y}\nIssued 11/07/2026 by {by} - 1 photo attached\n"
            f"[Clippy violation V-{hid}]")
    d = V.parse_note(note)
    d.update(history_id=hid, parent_id=1, entity="Tenants", note=note, test=test)
    return d


class Reminders(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.sent = []
        self.due = ([due(100 + n, "Freeburg", str(n)) for n in (12, 3, 20, 7)] +
                    [due(200, "Freeburg", "5", by="Writer B", correct_by=date(2026, 11, 9))] +
                    [due(300, "Cokato", "4")] + [due(400, "Freeburg", "9", test=True)])
        self.p = [mock.patch.object(V, "load_config", return_value=CFG),
                  mock.patch.object(V, "REMINDED", os.path.join(self.tmp, "reminded.json")),
                  mock.patch.object(V, "MOORNINGS_INBOX", os.path.join(self.tmp, "m.json")),
                  mock.patch.object(V, "LOG", os.path.join(self.tmp, "issued.json")),
                  mock.patch.object(V, "due_violations", side_effect=lambda conn, today=None: [dict(d) for d in self.due]),
                  mock.patch.object(notify, "configured", return_value=True),
                  mock.patch.object(notify, "send", side_effect=lambda *a: self.sent.append(a) or {"ok": True})]
        for p in self.p: p.start()

    def tearDown(self):
        for p in self.p: p.stop()

    def test_one_email_per_park_per_person(self):
        r = V.remind(None, today=TODAY)
        subjects = sorted((to, s) for to, s, *_ in self.sent)
        self.assertEqual(subjects, [
            ("a@x.com", "Violation re-checks: Cokato - 1 lot today (Wed 11/11)"),
            ("a@x.com", "Violation re-checks: Freeburg - 4 lots today (Wed 11/11)"),
            ("a@x.com", "[TEST] Violation re-checks: Freeburg - 1 lot today (Wed 11/11)"),
            ("me@x.com", "Violation re-checks: Cokato - 1 lot today (Wed 11/11)"),
            ("me@x.com", "Violation re-checks: Freeburg - 5 lots today (Wed 11/11)"),
            ("me@x.com", "[TEST] Violation re-checks: Freeburg - 1 lot today (Wed 11/11)"),
        ])
        self.assertEqual(r["violations"], 7)
        self.assertEqual(len(r["reminders"]), 3)

    def test_checklist_sorted_by_lot_with_late_days(self):
        V.remind(None, today=TODAY)
        body = next(b for to, s, b, _ in self.sent if to == "me@x.com" and s.startswith("Violation re-checks: Freeburg"))
        rows = [l for l in body.splitlines() if l.startswith("[ ]")]
        self.assertEqual([l.split(" - ")[0] for l in rows], ["[ ] Lot 3", "[ ] Lot 5", "[ ] Lot 7", "[ ] Lot 12", "[ ] Lot 20"])
        self.assertIn("Trash in yard", rows[0])
        self.assertIn("(due Mon 11/09, 2 days late)", rows[1])

    def test_each_violation_reminded_once(self):
        V.remind(None, today=TODAY)
        self.assertEqual(len(V._read(V.REMINDED, {})), 7)

    def test_moornings_one_line_per_park(self):
        V.remind(None, today=TODAY)
        q = V._read(V.MOORNINGS_INBOX, [])
        texts = sorted(i["text"] for i in q)
        self.assertEqual(texts, ["Re-check 1 Cokato violation (lots 4)",
                                 "Re-check 5 Freeburg violations (lots 3, 5, 7, 12, 20)",
                                 "[TEST] Re-check 1 Freeburg violation (lots 9)"])
        # a later batch the same day joins the line Moornings hasn't picked up yet
        self.due = [due(500, "Freeburg", "1")]
        V.remind(None, today=TODAY)
        q = V._read(V.MOORNINGS_INBOX, [])
        self.assertEqual(len(q), 3)
        self.assertIn("Re-check 6 Freeburg violations (lots 1, 3, 5, 7, 12, 20)", [i["text"] for i in q])

    def test_no_email_when_off(self):
        with mock.patch.object(V, "load_config", return_value=dict(CFG, reminders=dict(CFG["reminders"], email=False))):
            V.remind(None, today=TODAY)
        self.assertEqual(self.sent, [])
        self.assertEqual(len(V._read(V.REMINDED, {})), 7)


if __name__ == "__main__":
    unittest.main()
