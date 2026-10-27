"""Unit tests for the Everything-compatible query engine (no admin needed)."""

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.index import Entry
from engine.query import Query


def mk(name, is_dir=False, size=100, created=1600000000, modified=1700000000,
       attrs=0x80, path=None, vol="C"):
    e = Entry(vol, 0, name, is_dir, size, created, modified, attrs, 5)
    e.path = path or ("C:\\dir\\" + name)
    return e


ENTRIES = [
    mk("README.md", size=5000, modified=int(time.mktime(time.localtime())) - 60),
    mk("readme.txt", size=10),
    mk("Zcode-Setup.exe", size=1 << 20),
    mk("hello world.doc"),
    mk("song.mp3", size=5 * 1024 * 1024),
    mk("clip.mp4", size=2 * 1024 * 1024 * 1024),
    mk("photo.jpg"),
    mk("archive.zip"),
    mk("notes", is_dir=True, attrs=0x10 | 0x2),
    mk("hidden.dat", attrs=0x80 | 0x2),
    mk("dup1.txt", size=77),
    mk("dup1.txt", size=77, path="C:\\other\\dup1.txt"),
    mk("big.vhdx", size=500 * 1024 * 1024),
]


def run(q, entries=ENTRIES):
    return Query(q).execute(entries)


class TestBasics(unittest.TestCase):
    def test_plain_substring(self):
        self.assertEqual(len(run("readme")), 2)

    def test_and(self):
        self.assertEqual(len(run("readme md")), 1)

    def test_or(self):
        self.assertEqual(len(run("hello | photo")), 2)

    def test_not(self):
        r = run("readme !txt")
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0].name, "README.md")

    def test_phrase(self):
        self.assertEqual(len(run('"hello world"')), 1)

    def test_case_modifier(self):
        self.assertEqual(len(run("case:readme")), 1)

    def test_wildcard(self):
        self.assertEqual(len(run("*.mp3")), 1)
        self.assertEqual(len(run("readme.*")), 2)

    def test_regex(self):
        self.assertEqual(len(run(r"regex:^hello\sworld\.(doc)$")), 1)

    def test_matchpath(self):
        self.assertEqual(len(run("matchpath:other readme")), 0)
        self.assertEqual(len(run("matchpath:dir readme")), 2)


class TestFilters(unittest.TestCase):
    def test_file_folder(self):
        self.assertEqual(len(run("folder:")), 1)
        self.assertEqual(len(run("file: readme")), 2)

    def test_ext(self):
        self.assertEqual(len(run("ext:md;txt")), 4)

    def test_media_filters(self):
        self.assertEqual(len(run("audio:")), 1)
        self.assertEqual(len(run("video:")), 1)
        self.assertEqual(len(run("pic:")), 1)
        self.assertEqual(len(run("exe:")), 1)
        self.assertEqual(len(run("zip:")), 2)  # archive.zip + big.vhdx

    def test_size(self):
        self.assertEqual(len(run("size:>100mb")), 2)
        self.assertEqual(len(run("size:1kb-6mb")), 3)

    def test_attrib(self):
        self.assertEqual(len(run("attrib:h !folder:")), 1)

    def test_dupe(self):
        r = run("dupe:name")
        self.assertEqual(len(r), 2)
        self.assertTrue(all(e.name == "dup1.txt" for e in r))


class TestDates(unittest.TestCase):
    def test_dm_today(self):
        self.assertEqual(len(run("dm:today readme")), 1)

    def test_dm_range_abs(self):
        self.assertEqual(len(run("dm:2023-01-01-2030-01-01 readme")), 2)

    def test_dm_lt(self):
        self.assertEqual(len(run("dm:<2024-01-01 readme")), 1)


class TestParser(unittest.TestCase):
    def test_grouping(self):
        self.assertEqual(len(run("(hello | photo) ")), 2)
        self.assertEqual(len(run("readme (md | txt)")), 2)

    def test_path_prefix(self):
        self.assertEqual(len(run("C:\\dir\\")), len(ENTRIES) - 1)

    def test_empty(self):
        self.assertEqual(len(run("")), len(ENTRIES))


if __name__ == "__main__":
    unittest.main(verbosity=2)
