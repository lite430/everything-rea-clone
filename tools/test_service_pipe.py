"""Query the Windows service's IPC pipe (runs as an external process)."""

import json
import sys

sys.path.insert(0, "tools")
from pipe_client import pipe_query

PIPE = "\\\\.\\pipe\\EverythingClone"

r = pipe_query(PIPE, {"q": "readme", "count": 3})
print("service pipe query 'readme': total =", r["total"])
for it in r["items"]:
    print("  ", it["path"])
assert r["total"] > 0

r2 = pipe_query(PIPE, {"q": "folder: windows system32", "count": 3})
print("service pipe query folders:", r2["total"])
assert r2["total"] >= 1
print("SERVICE PIPE TESTS PASSED")
