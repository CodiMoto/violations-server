import sys, os, unittest
from datetime import date
from unittest import mock
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import violations as V, notify

class Copies(unittest.TestCase):
    V1 = {"park": "Freeburg", "lot": "5", "tenant_name": "T", "issued": date(2026, 10, 18), "issued_by": "Manager",
          "item_labels": ["Debris in yard"], "others": [], "warning": "1", "correct_by": date(2026, 10, 22),
          "photos": [], "ref": "V-1"}

    def test_sent_to_freeburg_list_only(self):
        cfg = {"park_copies": {"Freeburg": ["a@x.com"]}}
        with mock.patch.object(notify, "send", return_value={"ok": True}) as s:
            self.assertEqual(len(V.email_copies(self.V1, b"pdf", cfg)), 1)
            self.assertEqual(s.call_args[0][0], "a@x.com")
            s.reset_mock()
            self.assertEqual(V.email_copies(dict(self.V1, park="Cokato"), b"pdf", cfg), [])
            s.assert_not_called()

    def test_failure_does_not_raise(self):
        with mock.patch.object(notify, "send", side_effect=RuntimeError("boom")):
            r = V.email_copies(self.V1, b"pdf", {"park_copies": {"Freeburg": ["a@x.com"]}})
            self.assertFalse(r[0]["ok"])

if __name__ == "__main__":
    unittest.main()
