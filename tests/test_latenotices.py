"""Offline checks for late notices — no Rent Manager, no printer."""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import latenotices as L  # noqa: E402


def tx(day, amount, kind="Charge"):
    return {"TransactionDate": f"{day}T00:00:00", "Amount": amount, "TransactionType": kind}


class LedgerStart(unittest.TestCase):
    def test_starts_the_day_after_the_last_zero_balance(self):
        # Morristown lot 05, 2026-10-08: paid to zero on 9/21, October charges + late fee since.
        rows = [tx("2026-09-01", 647.14), tx("2026-09-21", -647.14, "Payment"),
                tx("2026-10-01", 606.30), tx("2026-10-08", 42.80)]
        start, total, ok = L.ledger_start(rows, 649.10)
        self.assertEqual(start, date(2026, 9, 22))
        self.assertEqual(total, 649.10)
        self.assertTrue(ok)

    def test_a_credit_balance_counts_as_paid_up(self):
        rows = [tx("2026-08-01", 500), tx("2026-08-03", -600, "Payment"), tx("2026-09-01", 500),
                tx("2026-09-02", 50)]
        start, total, ok = L.ledger_start(rows, 450)
        self.assertEqual(start, date(2026, 8, 4))
        self.assertTrue(ok)

    def test_payment_on_the_charge_day_is_netted(self):
        # Charge and full payment on the same day: that day ends at zero.
        rows = [tx("2026-09-01", 500), tx("2026-09-01", -500, "Payment"), tx("2026-10-01", 500)]
        self.assertEqual(L.ledger_start(rows, 500)[0], date(2026, 9, 2))

    def test_never_at_zero_starts_at_the_first_transaction(self):
        rows = [tx("2025-01-01", 100), tx("2025-02-01", 100)]
        self.assertEqual(L.ledger_start(rows, 200)[0], date(2025, 1, 1))

    def test_not_adding_up_is_caught(self):
        self.assertFalse(L.ledger_start([tx("2026-10-01", 100)], 150)[2])


class WhoAndWhen(unittest.TestCase):
    def lc(self, cid, tid, kind="Customer"):
        return {"ChargeID": cid, "AccountID": tid, "AccountType": kind}

    def test_waits_until_two_checks_see_the_same_late_fees(self):
        run = [self.lc(11, 1), self.lc(12, 2)]
        self.assertIsNone(L.due(run, {}, None))                       # first sight: maybe still posting
        self.assertIsNone(L.due(run + [self.lc(13, 3)], {}, L.fingerprint(run)))   # more came in
        self.assertEqual(L.due(run, {}, L.fingerprint(run)), [1, 2])  # unchanged: finished

    def test_each_tenant_once_a_month(self):
        run = [self.lc(11, 1), self.lc(12, 2), self.lc(13, 1)]
        self.assertEqual(L.due(run, {"1": {"state": "done"}}, L.fingerprint(run)), [2])

    def test_only_tenants(self):
        run = [self.lc(11, 1), self.lc(12, 9, kind="Prospect")]
        self.assertEqual(L.due(run, {}, L.fingerprint(run)), [1])

    def test_no_late_fees_yet(self):
        self.assertEqual(L.due([], {}, None), [])

    def test_note_is_tagged(self):
        t = L.note_text("2026-10", "14 day late notice (MN)", 649.10, date(2026, 9, 22), True)
        self.assertIn("14 day late notice (MN) printed", t)
        self.assertIn("$649.10", t)
        self.assertIn("[Clippy late notice 2026-10]", t)


class Schedule(unittest.TestCase):
    """When it looks at Rent Manager at all (no Rent Manager needed for these)."""

    def setUp(self):
        import tempfile
        from unittest import mock
        self.dir = tempfile.mkdtemp()
        cfg = {"mode": "live", "late_notices": {"letter_template_id": 1431, "start_day": 6}}
        self.patches = [mock.patch.object(L.V, "load_config", lambda: cfg),
                        mock.patch.object(L, "STATE", os.path.join(self.dir, "state.json"))]
        for p in self.patches:
            p.start()
        self.cfg = cfg

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_nothing_before_the_6th(self):
        from datetime import datetime
        r = L.check(conn=object(), now=datetime(2026, 10, 5, 23, 0))
        self.assertIn("waiting for the 6th", r["result"])

    def test_finished_month_is_not_checked_again(self):
        from datetime import datetime
        L.V._write(L.STATE, {"finished": {"2026-10": "2026-10-08T15:40:00"}})
        r = L.check(conn=object(), now=datetime(2026, 10, 20, 9, 0))   # object(): any Rent Manager call would fail
        self.assertIn("finished for 2026-10", r["result"])

    def test_off_without_a_template(self):
        self.cfg["late_notices"]["letter_template_id"] = None
        self.assertIn("off", L.check(conn=object())["result"])
