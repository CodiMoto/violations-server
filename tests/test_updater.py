"""Offline checks for the self-updater — temp folders only, no GitHub, no server."""
import io
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import updater as U  # noqa: E402
import violations as V  # noqa: E402


def fake_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr("CodiMoto-violations-server-abc1234/" + name, text)
    return buf.getvalue()


class Updater(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.here = os.path.join(self.tmp.name, "install")
        os.makedirs(os.path.join(self.here, "data"))
        self.saved = {k: getattr(U, k) for k in ("HERE", "DATA", "WORK", "STATE", "LOG", "_get",
                                                  "stop_server", "start_server", "server_ok", "_listener_pid")}
        U.HERE, U.DATA = self.here, os.path.join(self.here, "data")
        U.WORK, U.STATE, U.LOG = (os.path.join(U.DATA, "update"), os.path.join(U.DATA, "update.json"),
                                  os.path.join(U.DATA, "update.log"))
        U.stop_server = U.start_server = lambda: None
        U._listener_pid = lambda port: None
        U.server_ok = lambda wait=45: True

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(U, k, v)
        self.tmp.cleanup()

    def write(self, rel, text):
        path = os.path.join(self.here, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def read(self, rel):
        with open(os.path.join(self.here, *rel.split("/")), encoding="utf-8") as f:
            return f.read()

    def test_this_computers_own_files_are_never_downloaded(self):
        U._get = lambda url, accept: fake_zip({
            "mgrserver.py": "new", "ui/phone/phone.js": "new", "config.json": "EVIL",
            "violations_config.json": "EVIL", "data/x.json": "EVIL", "tools/a.exe": "EVIL",
            "venv/x": "EVIL", "../outside.txt": "EVIL"})
        files = U.download("abc1234", os.path.join(U.WORK, "new"))
        self.assertEqual(files, ["mgrserver.py", "ui/phone/phone.js"])

    def test_update_replaces_program_files_and_keeps_settings(self):
        self.write("mgrserver.py", "old")
        self.write("gone.py", "old")
        self.write("config.json", "LOGIN")
        self.write("violations_config.json", "SETTINGS")
        self.write("data/violations/issued.json", "DATA")
        U._save(installed="old1234", files=["mgrserver.py", "gone.py"])
        new = os.path.join(U.WORK, "new")
        for rel, text in {"mgrserver.py": "new", "added.py": "new"}.items():
            os.makedirs(os.path.dirname(os.path.join(new, rel)) or new, exist_ok=True)
            with open(os.path.join(new, rel), "w", encoding="utf-8") as f:
                f.write(text)
        ok, _ = U.apply("abc1234", new, ["added.py", "mgrserver.py"])
        self.assertTrue(ok)
        self.assertEqual(self.read("mgrserver.py"), "new")
        self.assertEqual(self.read("added.py"), "new")
        self.assertFalse(os.path.exists(os.path.join(self.here, "gone.py")))
        self.assertEqual(self.read("config.json"), "LOGIN")
        self.assertEqual(self.read("violations_config.json"), "SETTINGS")
        self.assertEqual(self.read("data/violations/issued.json"), "DATA")

    def test_a_version_that_wont_start_is_rolled_back(self):
        self.write("mgrserver.py", "old")
        U._save(files=["mgrserver.py"])
        new = os.path.join(U.WORK, "new")
        os.makedirs(new, exist_ok=True)
        for rel in ("mgrserver.py", "added.py"):
            with open(os.path.join(new, rel), "w", encoding="utf-8") as f:
                f.write("broken")
        answers = iter([False, True])            # new copy fails, old copy comes back
        U.server_ok = lambda wait=45: next(answers)
        ok, what = U.apply("abc1234", new, ["added.py", "mgrserver.py"])
        self.assertFalse(ok)
        self.assertIn("old one was put back", what)
        self.assertEqual(self.read("mgrserver.py"), "old")
        self.assertFalse(os.path.exists(os.path.join(self.here, "added.py")))

    def test_nothing_changes_if_the_server_wont_stop(self):
        self.write("mgrserver.py", "old")
        new = os.path.join(U.WORK, "new")
        os.makedirs(new, exist_ok=True)
        with open(os.path.join(new, "mgrserver.py"), "w", encoding="utf-8") as f:
            f.write("new")
        U._listener_pid = lambda port: 1234
        ok, _ = U.apply("abc1234", new, ["mgrserver.py"])
        self.assertFalse(ok)
        self.assertEqual(self.read("mgrserver.py"), "old")


    def test_release_number_shown_on_the_phone(self):
        self.assertEqual(U.current_version(), {"version": None, "installed_at": None, "dev": False})
        self.write("VERSION", "1.4.0\n")
        U._save(installed="2a878fe79f835aaf", installed_at="2026-09-24T21:00:00")
        self.assertEqual(U.current_version(), {"version": "1.4.0", "installed_at": "2026-09-24T21:00:00",
                                               "dev": False})
        os.makedirs(os.path.join(self.here, ".git"))
        self.assertTrue(U.current_version()["dev"])

    def test_every_release_has_a_version_file(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertRegex(U.release(here) or "", r"^\d+\.\d+\.\d+$")


class NewSettings(unittest.TestCase):
    def test_new_settings_fill_gaps_but_never_change_a_setting(self):
        mine = {"mode": "live", "printer": "Office", "reminders": {"email": True},
                "items": [{"id": "debris", "days": 9}]}
        example = {"mode": "test", "printer": None, "new_thing": 5,
                   "reminders": {"email": False, "moornings": False},
                   "items": [{"id": "debris", "days": 4}, {"id": "boat", "days": 10}]}
        cfg = V.with_defaults(mine, example)
        self.assertEqual(cfg["mode"], "live")
        self.assertEqual(cfg["printer"], "Office")
        self.assertEqual(cfg["new_thing"], 5)
        self.assertEqual(cfg["reminders"], {"email": True, "moornings": False})
        self.assertEqual([(i["id"], i["days"]) for i in cfg["items"]], [("debris", 9), ("boat", 10)])


if __name__ == "__main__":
    unittest.main()
