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

    def test_several_photos_each_with_its_own_circle(self):
        d = drafts.create(self.user)
        drafts.add_photo(d["id"], self.photo(), 0)
        drafts.set_marks(d["id"], [[[0.2, 0.2], [0.5, 0.2], [0.5, 0.5]]], 0)
        drafts.add_photo(d["id"], self.photo(), 1)
        drafts.set_marks(d["id"], [], 1)                                     # a wider shot, not circled
        drafts.add_photo(d["id"], self.photo(), 2)
        drafts.set_marks(d["id"], [[[0.1, 0.1], [0.3, 0.1], [0.3, 0.3]]], 2)
        drafts.set_lot(d["id"], PARK, 1)
        _, form = drafts.to_form(d["id"], ["debris"], [], "", "1")
        self.assertEqual([(p["n"], p["kind"]) for p in form["photos"]],
                         [(0, "marked"), (0, "original"), (1, "original"), (2, "marked"), (2, "original")])
        with self.assertRaises(ValueError):
            drafts.add_photo(d["id"], self.photo(), drafts.MAX_PHOTOS)

    def test_notice_prints_each_photo_once(self):
        import re
        from datetime import date

        def jpeg(colour):
            buf = io.BytesIO()
            Image.new("RGB", (800, 600), colour).save(buf, "JPEG")
            return buf.getvalue()
        v = {"ref": "V-1", "issued": date(2026, 9, 25), "park": "Morristown", "park_phone": "", "lot": "20",
             "tenant_name": "A Resident", "item_ids": [], "item_labels": ["Debris"], "others": [], "notes": "",
             "warning": "1", "correct_by": date(2026, 9, 29), "issued_by": "Codi",
             "photos": [{"n": 0, "kind": "marked", "jpeg": jpeg((200, 0, 0))},
                        {"n": 0, "kind": "original", "jpeg": jpeg((0, 200, 0))},
                        {"n": 1, "kind": "original", "jpeg": jpeg((0, 0, 200))}]}
        pdf = V.render_pdf(v, V.load_config())
        # photo 1 circled + photo 2 as it is; photo 1's untouched original isn't printed twice
        self.assertEqual(len(re.findall(rb"/Subtype\s*/Image", pdf)), 2)

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
