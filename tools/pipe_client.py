"""Named-pipe IPC client (for external processes and tests)."""

import ctypes
import json
import sys

k = ctypes.windll.kernel32
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2


def pipe_query(pipe_name, req, timeout_ms=30000):
    h = k.CreateFileW(pipe_name, GENERIC_READ | GENERIC_WRITE,
                      FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
    if h in (-1, 0xFFFFFFFFFFFFFFFF, None) or h == 0:
        raise OSError("pipe not available: %s" % pipe_name)
    try:
        data = (json.dumps(req) + "\n").encode("utf-8")
        w = ctypes.c_ulong(0)
        if not k.WriteFile(h, data, len(data), ctypes.byref(w), None):
            raise OSError("pipe write failed %d" % ctypes.GetLastError())
        buf = ctypes.create_string_buffer(1 << 20)
        r = ctypes.c_ulong(0)
        chunks = b""
        while b"\n" not in chunks:
            if not k.ReadFile(h, buf, 1 << 20, ctypes.byref(r), None) or r.value == 0:
                break
            chunks += buf.raw[:r.value]
        return json.loads(chunks.decode("utf-8", "replace").strip())
    finally:
        k.CloseHandle(h)


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else r"\\.\pipe\EverythingClone"
    q = sys.argv[2] if len(sys.argv) > 2 else "readme"
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    print(json.dumps(pipe_query(name, {"q": q, "count": n}), ensure_ascii=False))
