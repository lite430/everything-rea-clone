import sys, os, time
sys.path.insert(0, ".")
from engine.app import App

app = App(letters=["C"])
app.start()
print("started; monitors:", [(m.letter if hasattr(m, "letter") else m.vi.letter, m.is_alive(), m.ok)
                             for m in app.monitors])
D = r"C:\Users\Administrator\Desktop"


def idx(sub):
    with app.lock:
        vi = app.volumes[0]
        return [e.path for lst in vi.by_frn.values() for e in lst if sub in e.name]


for r in range(1, 4):
    p1 = os.path.join(D, "zz_dbg_%d_a.txt" % r)
    p2 = os.path.join(D, "zz_dbg_%d_b.txt" % r)
    open(p1, "w").write("x")
    time.sleep(4)
    ev1 = [e for e in app.monitors[0].last_events if "zz_dbg_%d" % r in e[2]]
    print("round", r, "create: index:", idx("zz_dbg_%d" % r), "events:", ev1)
    os.rename(p1, p2)
    time.sleep(4)
    ev2 = [e for e in app.monitors[0].last_events if "zz_dbg_%d" % r in e[2]]
    print("round", r, "rename: index:", idx("zz_dbg_%d" % r), "events:", ev2)
    os.remove(p2)
    time.sleep(4)
    ev3 = [e for e in app.monitors[0].last_events if "zz_dbg_%d" % r in e[2]]
    print("round", r, "delete: index:", idx("zz_dbg_%d" % r), "events:", ev3)
