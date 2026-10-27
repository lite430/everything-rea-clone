import sys, time, random
sys.path.insert(0, ".")
from engine.index import VolumeIndex

t0 = time.time()
last = [0]

def prog(n, t):
    if n - last[0] >= 800000:
        last[0] = n
        print("  %d records %.1fs" % (n, t))

vi = VolumeIndex("C")
n = vi.scan(prog)
print("MFT records:", n, "| files:", vi.counts["files"], "| folders:", vi.counts["folders"],
      "| %.1fs" % (time.time() - t0))
es = list(vi.entries())
print("total entries:", len(es))
random.seed(1)
for e in random.sample(es, 10):
    tail = "\\" if e.is_dir else ""
    print("  ", e.path + tail, e.size)
bad = sum(1 for e in es if not e.path.startswith("C:"))
print("bad paths:", bad)
