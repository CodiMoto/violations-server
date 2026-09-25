"""Offline checks for the step-by-step violation drafts — temp folder only."""
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import drafts  # noqa: E402
import violations as V  # noqa: E402
from PIL import Image  # noqa: E402

PARK = {"property_id": 105, "park": "Morristown", "phone": "",
        "lots": [{"unit_id": 1, "lot": "020", "tenants": [{"id": 9, "name": "A Resident"}]},
                 {"unit_id": 2, "lot": "021", "tenants": []}]}


class Steps(unittest.TestCase):
    def setUp(self):
        self.old = drafts.ROOT
        drafts.ROOT = tempfile.mkdtemp()
        self.user = {"username": "codi"}

    def tearDown(self):
        drafts.ROOT = self.old

    def photo(self):
        buf = io.BytesIO()
        Image.new("RGB", (800, 600), (90, 120, 90)).save(buf, "JPEG")
        return buf.getvalue()

    def test_whole_flow_and_resume(self):
        d = drafts.create(self.user)
        self.assertEqual(d["stage"], "photo")
        d = drafts.add_photo(d["id"], self.photo())
        self.assertEqual(d["stage"], "circle")
        self.assertEqual(drafts.open_draft(self.user)["id"], d["id"])      # resumable
        d = drafts.set_marks(d["id"], [[[0.2, 0.2], [0.5, 0.2], [0.5, 0.5]]])
        self.assertEqual(d["stage"], "lot")
        marked = Image.open(drafts.photo_path(d["id"], "marked"))
        self.assertEqual(marked.getpixel((int(0.35 * 800), int(0.2 * 600)))[0] > 180, True)  # red line drawn
        with self.assertRaises(ValueError):
            drafts.set_lot(d["id"], PARK, 2)                                # vacant lot refused
        d = drafts.set_lot(d["id"], PARK, 1)
        self.assertEqual((d["stage"], d["lot"]["tenant_name"]), ("items", "A Resident"))
        d2, form = drafts.to_form(d["id"], ["debris"], [], "", "1")
        self.assertEqual([p["kind"] for p in form["photos"]], ["marked", "original"])
        drafts.mark_issued(d["id"], "V-x")
        self.assertIsNone(drafts.open_draft(self.user))                    # finished → not resumable

    def test_bad_ids_rejected(self):
        for bad in ("..", "d123", "../secret", "dZZZZZZZZZZZZ"):
            with self.assertRaises(ValueError):
                drafts.load(bad)


class VoidNotes(unittest.TestCase):
    def test_void_never_counts(self):
        old = V.VOID, V.RESOLVED
        tmp = tempfile.mkdtemp()
        V.VOID, V.RESOLVED = os.path.join(tmp, "v.json"), os.path.join(tmp, "r.json")
        try:
            V.mark_void(5, "test")

            class C:
                def get(self, *a, **k):
                    return [{"HistoryID": 5, "HistoryCategoryID": 3, "CreateDate": "2099-01-01",
                             "Note": "VIOLATION NOTICE - Warning 1\n..."},
                            {"HistoryID": 6, "HistoryCategoryID": 3, "CreateDate": "2099-01-01",
                             "Note": "Yard needs cleaning"}]
            h = V.tenant_violations(C(), 1)
            self.assertEqual(len(h), 1)
        finally:
            V.VOID, V.RESOLVED = old


if __name__ == "__main__":
    unittest.main()
