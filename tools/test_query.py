import sys, time
sys.path.insert(0, ".")
from engine.app import App

app = App(letters=["C"])
t0 = time.time()
app.start()
print("scan done in %.1fs" % (time.time() - t0))
st = app.stats()
print("stats:", st["objects"], "objects |", st["files"], "files |", st["folders"], "folders")

tests = [
    ("zcode", None),
    ("readme.md", None),
    ("*.exe", None),
    ("hello | world", None),
    ("ext:txt;md readme", None),
    ("size:>100mb", None),
    ("dm:today", None),
    ("folder: windows system32", None),
    ("case:README", None),
    ("regex:^zcode.*workspace$", None),
    ("path:zcode-workspace everything", None),
    ("attrib:h folder:", None),
    ("dupe:name", None),
]
for text, _ in tests:
    t1 = time.time()
    page, total = app.search(text, limit=5)
    dt = (time.time() - t1) * 1000
    t2 = time.time()
    page, total = app.search(text, limit=5)
    dt2 = (time.time() - t2) * 1000
    print("%-38r -> %6d results  cold %7.1fms  warm %6.1fms | %s" %
          (text, total, dt, dt2, page[0].path if page else ""))
