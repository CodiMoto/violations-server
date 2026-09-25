"""The phone's one-shot send: received once, never issued twice — offline, no Rent Manager."""
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import inbox  # noqa: E402
import violations as V  # noqa: E402

USER = {"username": "codi", "name": "Codi"}


class Inbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = (inbox.FILE, V.LOG, inbox.drafts, V.build, V.issue, inbox.rmconn.Connection)
        inbox.FILE = os.path.join(self.tmp, "received.json")
        V.LOG = os.path.join(self.tmp, "issued.json")
        self.built, self.issued = [], []
        release = self.release = threading.Event()

        calls = self.calls = []

        class Drafts:
            MAX_PHOTOS = 6
            create = staticmethod(lambda user: {"id": "d000000000001"})
            add_photo = staticmethod(lambda did, data, n=0: calls.append(("photo", n, data)))
            set_marks = staticmethod(lambda did, strokes, n=0: calls.append(("marks", n, len(strokes))))
            set_lot = staticmethod(lambda *a: None)
            to_form = staticmethod(lambda *a: (None, {}))
            mark_issued = staticmethod(lambda *a: None)
        inbox.drafts = Drafts
        inbox.rmconn.Connection = lambda mode: None

        def build(form, user, conn):
            self.built.append(form)
            return {"ref": "V-20260925-101500", "park": "Morristown", "lot": "20", "tenant_name": "A Resident"}

        def issue(conn, v, progress=None):
            release.wait(5)
            progress("Saved to Rent Manager")
            self.issued.append(v["ref"])
            return {"ref": v["ref"], "history_id": 1, "attached": 2, "correct_by": "2026-09-29",
                    "printed": None, "test": True}
        V.build, V.issue = build, issue

    def tearDown(self):
        self.release.set()
        inbox.FILE, V.LOG, inbox.drafts, V.build, V.issue, inbox.rmconn.Connection = self.saved

    def form(self, cid="phone-abc-123"):
        return {"cid": cid, "unit_id": 1, "tenant_id": 9, "items": ["debris"], "warning": "1", "strokes": []}

    def wait_done(self, cid):
        for _ in range(100):
            st = inbox.status(cid, USER)
            if st["state"] != "working":
                return st
            threading.Event().wait(0.05)
        self.fail("never finished")

    def test_answered_before_rent_manager_then_done(self):
        st = inbox.submit_violation(USER, self.form(), b"jpeg", {"park": "Morristown"})
        self.assertEqual(st["state"], "working")                 # answered straight away
        self.release.set()
        st = self.wait_done("phone-abc-123")
        self.assertEqual(st["state"], "done")
        self.assertEqual(st["result"]["pdf"], "/api/letters/V-20260925-101500.pdf")
        self.assertEqual(st["steps"], ["Saved to Rent Manager"])

    def test_several_photos_each_with_its_own_circle(self):
        form = dict(self.form(), marks=[[[[0.1, 0.1], [0.2, 0.2]]], [], [[[0.3, 0.3], [0.4, 0.4]], [[0.5, 0.5], [0.6, 0.6]]]])
        inbox.submit_violation(USER, form, [b"one", b"two", b"three"], {})
        self.assertEqual(self.calls, [("photo", 0, b"one"), ("marks", 0, 1), ("photo", 1, b"two"), ("marks", 1, 0),
                                      ("photo", 2, b"three"), ("marks", 2, 2)])

    def test_sent_twice_is_issued_once(self):
        inbox.submit_violation(USER, self.form(), b"jpeg", {})
        inbox.submit_violation(USER, self.form(), b"jpeg", {})  # the signal dropped; the phone sent it again
        self.release.set()
        self.wait_done("phone-abc-123")
        inbox.submit_violation(USER, self.form(), b"jpeg", {})  # and again after it was done
        self.assertEqual(len(self.built), 1)
        self.assertEqual(self.issued, ["V-20260925-101500"])

    def test_two_in_the_same_second_get_different_refs(self):
        self.release.set()
        inbox.submit_violation(USER, self.form("phone-aaaa-1"), b"jpeg", {})
        inbox.submit_violation(USER, self.form("phone-bbbb-2"), b"jpeg", {})
        a, b = self.wait_done("phone-aaaa-1"), self.wait_done("phone-bbbb-2")
        self.assertNotEqual(a["ref"], b["ref"])

    def test_only_the_sender_can_see_it(self):
        inbox.submit_violation(USER, self.form(), b"jpeg", {})
        self.assertIsNone(inbox.status("phone-abc-123", {"username": "someone-else"}))

    def test_needs_an_id_and_a_photo(self):
        with self.assertRaises(ValueError):
            inbox.submit_violation(USER, self.form(cid="x"), b"jpeg", {})
        with self.assertRaises(ValueError):
            inbox.submit_violation(USER, self.form(), None, {})

    def test_restart_mid_way(self):
        inbox._put("gone-before-log", user="codi", state="working", ref="V-1")
        inbox._put("made-it-to-log", user="codi", state="working", ref="V-2")
        V._write(V.LOG, [{"ref": "V-2", "history_id": 7}])
        inbox.recover()
        self.assertEqual(inbox.status("made-it-to-log", USER)["state"], "done")
        st = inbox.status("gone-before-log", USER)
        self.assertEqual(st["state"], "failed")
        self.assertIn("History & Notes", st["error"])


if __name__ == "__main__":
    unittest.main()
