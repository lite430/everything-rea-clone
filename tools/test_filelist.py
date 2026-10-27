"""File-list + thumbnail tests that do not need a full MFT scan.

Uses a fake volume-less App: file lists + a tiny real directory scanned by
walking it into a VolumeIndex-shaped structure is overkill; instead we test
load_file_list round-trip and list: filter against an App with no volumes,
then thumbnail/preview against the real HTTP server in test_server_extra.py.
"""

import json
import os
import sys
import time

sys.path.insert(0, ".")
from engine.app import App
from engine.filelist import load_file_list

D = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(D), "analysis")

app = App(letters=[])          # no MFT volumes: pure file-list app
app.stats.__self__.volumes = []  # explicit: empty

# 1) round-trip: our own CSV export header (== EFU) loaded back
efu = os.path.join(OUT, "tmp_filelist.efu")
with open(efu, "w", encoding="utf-8") as f:
    f.write("Filename,Size,Date Modified,Date Created,Attributes\n")
    f.write('C:\\workspace\\zcode-workspace\\everything-rea-clone\\README.md,1234,'
            '"2026-10-26 03:00:00","2026-10-26 03:00:00",....a.........\n')
    f.write('C:\\workspace\\zcode-workspace\\everything-rea-clone\\engine,0,'
            '"2026-10-26 03:00:00","2026-10-26 03:00:00",....d.........\n')
    f.write('docs\\note.txt,10,"2026-10-26 03:00:00","2026-10-26 03:00:00",....a.........\n')

label, n = load_file_list(app, efu)
print("EFU loaded: label=%r entries=%d" % (label, n))
assert n == 3, n

page, total = app.search("readme")
assert total == 1 and page[0].name == "README.md", (total, page)
print("search 'readme' inside file-list app:", total)

page, total = app.search("list:%s readme" % label)
assert total == 1, total
print("list: filter works:", total)

page, total = app.search("folder:")
assert total == 1 and page[0].is_dir, total
print("folder: filter over file list:", total)

page, total = app.search("ext:txt")
assert total == 1 and page[0].path.endswith("docs\\note.txt"), total
print("relative path resolved against list dir:", page[0].path)

# 2) plain TXT list
txt = os.path.join(OUT, "tmp_filelist.txt")
with open(txt, "w", encoding="utf-8") as f:
    f.write("C:\\Windows\\notepad.exe\n")
    f.write("C:\\Windows\\System32\\cmd.exe\n")
label2, n2 = load_file_list(app, txt)
print("TXT loaded: label=%r entries=%d" % (label2, n2))
assert n2 == 2
page, total = app.search("list:%s cmd" % label2)
assert total == 1 and page[0].name == "cmd.exe"
print("txt list search:", total)

st = app.stats()
print("stats:", st["objects"], "objects | file_lists:", st["file_lists"])
assert st["file_lists"] == [{"name": label, "count": 3}, {"name": label2, "count": 2}]

os.remove(efu)
os.remove(txt)
print("FILE LIST TESTS PASSED")
