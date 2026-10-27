"""Offline File List loading and indexing.

Mirrors Everything's File List feature observed via REA strings:
  - "File List", "File List &Editor", "File List Filename" sort constants,
    "Files with no Extension"
  - the CSV export header our own server emits is exactly the EFU header:
    "Filename,Size,Date Modified,Date Created,Attributes"

Supported inputs:
  *.efu / *.csv : header line "Filename,Size,Date Modified,Date Created,Attributes"
                  followed by rows (relative paths resolve against the list file)
  *.txt         : one file path per line

Loaded entries are indexed like volume entries and selectable with the
Everything `list:<name>` filter; <name> defaults to the file's basename.
"""

import csv
import os
import time
from datetime import datetime

from .index import Entry

ATTR_BITS = {"r": 0x1, "h": 0x2, "s": 0x4, "d": 0x10, "a": 0x20, "n": 0x80,
             "t": 0x100, "l": 0x200, "p": 0x400, "c": 0x800, "o": 0x1000,
             "i": 0x2000, "e": 0x4000, "b": 0x8000}


def _parse_attrs(s):
    s = (s or "").strip()
    if not s:
        return 0x80
    if s.isdigit():
        try:
            return int(s)
        except ValueError:
            return 0x80
    bits = 0
    for ch in s:
        bits |= ATTR_BITS.get(ch.lower(), 0)
    return bits or 0x80


def _parse_date(s):
    s = (s or "").strip().strip('"')
    if not s:
        return 0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return int(time.mktime(datetime.strptime(s, fmt).timetuple()))
        except ValueError:
            continue
    return 0


def _mk_entry(label, path, size, modified, created, attrs_text):
    path = path.strip().strip('"')
    if not path:
        return None
    is_dir = path.endswith("\\") or path.endswith("/")
    name = os.path.basename(path.rstrip("\\/")) or path
    attrs = _parse_attrs(attrs_text)
    if attrs & 0x10:
        is_dir = True
    try:
        size = int(float(size or 0))
    except (TypeError, ValueError):
        size = 0
    e = Entry(label, 0, name, is_dir, size, _parse_date(created) or 0,
              _parse_date(modified) or 0, attrs, 0)
    e.path = path
    return e


def load_file_list(app, path, label=None):
    """Parse one list file into app.file_lists; -> (label, entry_count)."""
    label = label or os.path.splitext(os.path.basename(path))[0]
    base_dir = os.path.dirname(os.path.abspath(path))
    entries = []
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        first = f.readline()
        if first.startswith("Filename,"):
            reader = csv.reader(f)
            for row in reader:
                if not row or not row[0].strip():
                    continue
                name = row[0]
                if not os.path.isabs(name):
                    name = os.path.join(base_dir, name)
                size = row[1] if len(row) > 1 else 0
                dm = row[2] if len(row) > 2 else ""
                dc = row[3] if len(row) > 3 else ""
                at = row[4] if len(row) > 4 else ""
                e = _mk_entry(label, name, size, dm, dc, at)
                if e:
                    entries.append(e)
        else:
            for line in [first] + f.readlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if not os.path.isabs(line):
                    line = os.path.join(base_dir, line)
                e = _mk_entry(label, line, 0, "", "", "")
                if e:
                    entries.append(e)
    app.file_lists.append((label, entries))
    return label, len(entries)
