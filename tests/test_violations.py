"""Offline checks for violations — no Rent Manager, no phone, no printer."""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import violations as V  # noqa: E402

CFG = {"items": [{"id": "debris", "label": "Debris in yard", "days": 4, "rule": "next_monday"},
                 {"id": "grass", "label": "Parking in grass", "days": 2, "rule": "exact"},
                 {"id": "shed", "label": "Storage shed in disrepair", "days": 10, "rule": "next_monday"}],
       "other": {"days": 4, "rule": "next_monday"},
       "warning_levels": ["Reminder", "1", "2", "Final"], "warning_lookback_days": 180}


class Deadlines(unittest.TestCase):
    def test_next_monday_always_includes_a_weekend(self):
        for day in range(21, 28):                  # Mon 9/21 .. Sun 9/27
            issued = date(2026, 9, day)
            due = V.item_deadline(issued, 4, "next_monday")
            self.assertEqual(due.weekday(), 0)      # a Monday
            self.assertGreaterEqual((due - issued).days, 4)
            self.assertLessEqual((due - issued).days, 10)

    def test_exact(self):
        self.assertEqual(V.item_deadline(date(2026, 9, 23), 2, "exact"), date(2026, 9, 25))

    def test_letter_uses_latest(self):
        self.assertEqual(V.deadline(date(2026, 9, 23), ["debris", "grass"], [], CFG), date(2026, 9, 28))
        self.assertEqual(V.deadline(date(2026, 9, 23), ["grass"], ["Weeds"], CFG), date(2026, 9, 28))
        self.assertIsNone(V.deadline(date(2026, 9, 23), [], [], CFG))


class NoteRoundTrip(unittest.TestCase):
    def v(self, warning):
        return {"warning": warning, "park": "Freeburg", "lot": "98", "tenant_name": "A Resident",
                "item_labels": ["Debris in yard"], "others": ["Weeds"], "notes": "Tidy up",
                "correct_by": date(2026, 9, 28), "issued": date(2026, 9, 23),
                "issued_by": "Park Manager", "photos": [1, 2], "ref": "V-20260923-120000"}

    def test_watcher_can_read_back_what_was_written(self):
        for w in ("Reminder", "1", "2", "Final"):
            p = V.parse_note("[TEST] " + V.note_text(self.v(w)))
            self.assertEqual(p["warning"], w)
            self.assertEqual(p["correct_by"], date(2026, 9, 28))
            self.assertEqual((p["park"], p["lot"], p["tenant_name"]), ("Freeburg", "98", "A Resident"))
            self.assertEqual(p["ref"], "V-20260923-120000")

    def test_hand_written_notes_have_no_deadline(self):
        self.assertNotIn("correct_by", V.parse_note("Yard needs to be cleaned up and weeds need to be taken care of."))


class Suggestion(unittest.TestCase):
    def test_ladder(self):
        s = lambda h: V.suggest_warning(h, CFG)["level"]
        self.assertEqual(s([]), "1")
        self.assertEqual(s([{"warning": None}]), "2")
        self.assertEqual(s([{"warning": None}] * 5), "Final")
        self.assertEqual(s([{"warning": "Reminder"}]), "1")
        self.assertEqual(s([{"warning": "Final"}]), "Final")

    def test_rent_notices_are_not_violations(self):
        for t in ("5 Day Notice September 2026. Paying 9-11", "Late rent", "5 day Violation notice September 2026"):
            self.assertTrue(V.NOT_A_VIOLATION.search(t), t)
        self.assertFalse(V.NOT_A_VIOLATION.search("Yard needs to be cleaned up"))


class Passwords(unittest.TestCase):
    def test_hash(self):
        h = V.hash_password("correct horse")
        self.assertTrue(V.check_password("correct horse", h))
        self.assertFalse(V.check_password("wrong", h))
        self.assertFalse(V.check_password("x", None))


if __name__ == "__main__":
    unittest.main()


class Printing(unittest.TestCase):
    """What the phone is told after a print, from what Windows reported (no printer needed)."""
    P = "HP OfficeJet"

    def test_printed(self):
        r = V.print_verdict({"verdict": "printed", "pages": 2, "total": 2}, self.P)
        self.assertTrue(r["ok"])
        self.assertTrue(r["verified"])

    def test_printer_problem_is_reported_and_not_sent_twice(self):
        r = V.print_verdict({"verdict": "error", "status": "Error, Printing", "pages": 0}, self.P)
        self.assertFalse(r["ok"])
        self.assertFalse(r["retry"])            # Windows still has it — a resend = two copies
        self.assertIn("Error, Printing", r["error"])

    def test_windows_gave_up_means_send_again(self):
        r = V.print_verdict({"verdict": "gone", "failed": True}, self.P)
        self.assertFalse(r["ok"])
        self.assertTrue(r["retry"])
        self.assertIn(self.P, r["error"])

    def test_no_verdict_is_not_an_alarm(self):
        for info in (None, {"verdict": "gone", "failed": False}):
            r = V.print_verdict(info, self.P)
            self.assertTrue(r["ok"])
            self.assertFalse(r["verified"])

    def test_still_waiting(self):
        r = V.print_verdict({"verdict": "waiting", "status": "Printing", "seconds": 120}, self.P)
        self.assertFalse(r["ok"])
        self.assertFalse(r["retry"])
        self.assertIn("120", r["error"])

    def test_sumatra_problem_in_plain_words(self):
        out = ("Starting SumatraPDF 3.5.2\nPrinting problem.: Printer with given name doesn't exist\n"
               "Exiting with exit code: 1")
        self.assertIn("can't find the printer 'HP OfficeJet'", V.sumatra_problem(out, self.P))
        self.assertIn("out of paper", V.sumatra_problem("Printing problem.: out of paper", self.P))
