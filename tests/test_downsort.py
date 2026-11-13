#!/usr/bin/env python3
"""Tests for downsort. Run with: python tests/test_downsort.py  (or python -m unittest)."""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import downsort  # noqa: E402


class TestClassify(unittest.TestCase):
    def test_known_extensions(self):
        cases = {
            "photo.JPG": "images",
            "screenshot.png": "images",
            "report.pdf": "documents",
            "notes.md": "documents",
            "sheet.xlsx": "spreadsheets",
            "budget.csv": "spreadsheets",
            "backup.zip": "archives",
            "setup.exe": "installers",
            "song.mp3": "audio",
            "clip.mp4": "video",
            "app.py": "code",
            "book.epub": "ebooks",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(downsort.classify(name), expected)

    def test_compound_suffixes(self):
        self.assertEqual(downsort.classify("source.tar.gz"), "archives")
        self.assertEqual(downsort.classify("source.tar.bz2"), "archives")

    def test_unknown_and_extensionless(self):
        self.assertEqual(downsort.classify("mystery.qqq"), "other")
        self.assertEqual(downsort.classify("README"), "other")
        self.assertEqual(downsort.classify("no.ext.at.all"), "other")

    def test_case_insensitive(self):
        self.assertEqual(downsort.classify("SHOUTING.PDF"), "documents")


class TestInProgress(unittest.TestCase):
    def test_partial_downloads_are_flagged(self):
        for name in ("x.crdownload", "x.part", "x.partial", "x.download", "x.opdownload"):
            with self.subTest(name=name):
                self.assertTrue(downsort.is_in_progress(name))

    def test_normal_files_are_not(self):
        for name in ("x.pdf", "x.zip", "x"):
            with self.subTest(name=name):
                self.assertFalse(downsort.is_in_progress(name))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="downsort-test-"))
        self.root = self.tmp / "Downloads"
        self.root.mkdir()
        self.log_dir = self.tmp / "logs"
        self._orig_log_dir = downsort.LOG_DIR
        downsort.LOG_DIR = self.log_dir

    def tearDown(self):
        downsort.LOG_DIR = self._orig_log_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    def touch(self, name, content=b"x"):
        path = self.root / name
        path.write_bytes(content)
        return path


class TestBuildPlan(Base):
    def test_plan_has_correct_destinations(self):
        self.touch("a.pdf")
        self.touch("b.png")
        self.touch("c.unknown")
        plan, skipped = downsort.build_plan(self.root)
        dests = {Path(i["src"]).name: Path(i["dst"]) for i in plan}
        self.assertEqual(dests["a.pdf"].parent.name, "documents")
        self.assertEqual(dests["b.png"].parent.name, "images")
        self.assertEqual(dests["c.unknown"].parent.name, "other")
        self.assertEqual(len(plan), 3)
        self.assertEqual(skipped, [])

    def test_hidden_and_partial_are_skipped(self):
        self.touch(".hidden.pdf")
        self.touch("movie.mp4.crdownload")
        plan, skipped = downsort.build_plan(self.root)
        self.assertEqual(plan, [])
        reasons = sorted(r for _, r in skipped)
        self.assertEqual(reasons, ["hidden file", "still downloading"])

    def test_include_hidden_option(self):
        self.touch(".hidden.pdf")
        plan, _ = downsort.build_plan(self.root, include_hidden=True)
        self.assertEqual(len(plan), 1)

    def test_by_date_uses_year_month(self):
        self.touch("a.pdf")
        plan, _ = downsort.build_plan(self.root, by_date=True)
        parent = Path(plan[0]["dst"]).parent
        self.assertEqual(parent.parent.name, "documents")
        self.assertRegex(parent.name, r"^\d{4}-\d{2}$")

    def test_directories_are_left_alone(self):
        (self.root / "already-a-folder").mkdir()
        plan, _ = downsort.build_plan(self.root)
        self.assertEqual(plan, [])

    def test_build_plan_moves_nothing(self):
        path = self.touch("a.pdf")
        downsort.build_plan(self.root)
        self.assertTrue(path.exists())


class TestApplyAndRestore(Base):
    def test_apply_moves_and_logs(self):
        self.touch("a.pdf")
        self.touch("b.png")
        plan, _ = downsort.build_plan(self.root)
        log = downsort.apply_plan(plan)

        self.assertTrue((self.root / "documents" / "a.pdf").exists())
        self.assertTrue((self.root / "images" / "b.png").exists())
        self.assertFalse((self.root / "a.pdf").exists())
        self.assertEqual(len(log["moves"]), 2)
        self.assertTrue(Path(log["log_path"]).exists())

        saved = json.loads(Path(log["log_path"]).read_text())
        self.assertEqual(saved["app"], "downsort")
        self.assertEqual(len(saved["moves"]), 2)

    def test_restore_puts_files_back(self):
        self.touch("a.pdf")
        self.touch("b.png")
        plan, _ = downsort.build_plan(self.root)
        downsort.apply_plan(plan)

        restored, skipped = downsort.restore()
        self.assertEqual(len(restored), 2)
        self.assertEqual(skipped, [])
        self.assertTrue((self.root / "a.pdf").exists())
        self.assertTrue((self.root / "b.png").exists())
        self.assertFalse((self.root / "documents" / "a.pdf").exists())

    def test_restore_skips_when_original_is_occupied(self):
        self.touch("a.pdf")
        plan, _ = downsort.build_plan(self.root)
        downsort.apply_plan(plan)
        self.touch("a.pdf", b"a new file with the same name")

        restored, skipped = downsort.restore()
        self.assertEqual(restored, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("occupied", skipped[0][1])

    def test_restore_without_log(self):
        restored, skipped = downsort.restore()
        self.assertEqual(restored, [])
        self.assertEqual(skipped[0][1], "no log file found")

    def test_never_overwrites_an_existing_destination(self):
        self.touch("a.pdf", b"first")
        (self.root / "documents").mkdir()
        (self.root / "documents" / "a.pdf").write_bytes(b"already here")

        plan, _ = downsort.build_plan(self.root)
        self.assertTrue(Path(plan[0]["dst"]).name.startswith("a (1)"))

    def test_two_runs_do_not_clobber(self):
        self.touch("a.pdf", b"one")
        plan, _ = downsort.build_plan(self.root)
        downsort.apply_plan(plan)
        self.touch("a.pdf", b"two")
        plan2, _ = downsort.build_plan(self.root)
        downsort.apply_plan(plan2)

        docs = sorted(p.name for p in (self.root / "documents").iterdir())
        self.assertEqual(len(docs), 2)
        self.assertEqual((self.root / "documents" / "a.pdf").read_bytes(), b"one")


class TestDedupe(Base):
    def test_finds_identical_content(self):
        self.touch("one.pdf", b"same bytes")
        self.touch("two.pdf", b"same bytes")
        self.touch("three.pdf", b"different")
        groups = downsort.find_duplicates(self.root)
        self.assertEqual(len(groups), 1)
        paths = list(groups.values())[0]
        self.assertEqual(len(paths), 2)

    def test_no_duplicates(self):
        self.touch("a.pdf", b"one")
        self.touch("b.pdf", b"two")
        self.assertEqual(downsort.find_duplicates(self.root), {})

    def test_dedupe_apply_moves_duplicates(self):
        self.touch("one.pdf", b"same bytes")
        self.touch("two.pdf", b"same bytes")
        out = io.StringIO()
        with redirect_stdout(out):
            downsort.main(["--path", str(self.root), "--dedupe", "--apply"])
        self.assertTrue((self.root / "_duplicates").exists())
        self.assertEqual(len(list((self.root / "_duplicates").iterdir())), 1)
        self.assertEqual(len(list(self.root.glob("*.pdf"))), 1)


class TestCli(Base):
    def test_dry_run_moves_nothing(self):
        path = self.touch("a.pdf")
        out = io.StringIO()
        with redirect_stdout(out):
            code = downsort.main(["--path", str(self.root)])
        self.assertEqual(code, 0)
        self.assertTrue(path.exists())
        self.assertIn("dry run", out.getvalue())

    def test_apply_then_restore_through_cli(self):
        self.touch("a.pdf")
        with redirect_stdout(io.StringIO()):
            downsort.main(["--path", str(self.root), "--apply"])
        self.assertTrue((self.root / "documents" / "a.pdf").exists())

        with redirect_stdout(io.StringIO()):
            downsort.main(["--restore"])
        self.assertTrue((self.root / "a.pdf").exists())

    def test_missing_folder_returns_2(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = downsort.main(["--path", str(self.tmp / "nope")])
        self.assertEqual(code, 2)
        self.assertIn("not found", out.getvalue())

    def test_empty_folder_says_so(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = downsort.main(["--path", str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn("Nothing to sort", out.getvalue())

    def test_list_logs(self):
        self.touch("a.pdf")
        with redirect_stdout(io.StringIO()):
            downsort.main(["--path", str(self.root), "--apply"])
            out = io.StringIO()
        with redirect_stdout(out):
            downsort.main(["--list-logs"])
        self.assertIn("log-", out.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
