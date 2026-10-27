"""Live IPC verification against the running server (pipe + file-list)."""

import sys

sys.path.insert(0, "tools")
from pipe_client import pipe_query

PIPE = "\\\\.\\pipe\\EverythingClone"

r = pipe_query(PIPE, {"q": "list:demo-filelist", "count": 10})
print("pipe list: filter ->", r["total"], "items:", [i["name"] for i in r["items"]])
assert r["total"] == 6

r2 = pipe_query(PIPE, {"q": "ext:png zcode", "count": 3})
print("pipe png query ->", r2["total"])
assert r2["total"] >= 1

print("PIPE TESTS PASSED")
