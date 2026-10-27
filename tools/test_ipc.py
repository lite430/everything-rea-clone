"""IPC client test: talks to the clone exactly like an official SDK client.

Creates a receiver window, FindWindow(L"EVERYTHING"), sends an
EVERYTHING_IPC_QUERY via WM_COPYDATA, parses the EVERYTHING_IPC_LIST reply.
Also exercises the named pipe transport.
"""

import ctypes
import json
import struct
import sys
import time
from ctypes import wintypes

sys.path.insert(0, ".")
from engine.app import App

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32
WM_COPYDATA = 0x004A
WM_QUIT = 0x0012

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class COPYDATASTRUCT(ctypes.Structure):
    _fields_ = [("dwData", ctypes.c_size_t), ("cbData", wintypes.DWORD),
                ("lpData", ctypes.c_void_p)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HANDLE),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


def main():
    app = App(letters=["C"])
    app.start()
    from engine.ipc import IpcServer
    ipc = IpcServer(app, log=lambda m: print("[ipc]", m))
    ipc.start()
    time.sleep(1.0)

    # --- client side -------------------------------------------------
    replies = []
    WNDPROCREF = []

    def client_proc(hwnd, msg, wparam, lparam):
        if msg == WM_COPYDATA:
            cds = ctypes.cast(lparam, ctypes.POINTER(COPYDATASTRUCT)).contents
            replies.append((cds.dwData, ctypes.string_at(cds.lpData, cds.cbData)))
            return 0
        return u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    inst = k32.GetModuleHandleW(None)
    wc = WNDCLASSW()
    wc.lpfnWndProc = WNDPROC(client_proc)
    wc.hInstance = inst
    wc.lpszClassName = "EverythingCloneTestClient"
    WNDPROCREF.append(wc.lpfnWndProc)
    u32.RegisterClassW(ctypes.byref(wc))
    client_hwnd = u32.CreateWindowExW(0, "EverythingCloneTestClient", "t", 0,
                                      0, 0, 0, 0, None, None, inst, None)

    server_hwnd = u32.FindWindowW("EVERYTHING", None)
    assert server_hwnd, "EVERYTHING window not found"

    def pump(timeout_ms=200):
        msg = wintypes.MSG()
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            while u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                u32.TranslateMessage(ctypes.byref(msg))
                u32.DispatchMessageW(ctypes.byref(msg))

    def query(search, flags=0, offset=0, max_results=10):
        payload = struct.pack("<5I", client_hwnd & 0xFFFFFFFF, 0, flags,
                              offset, max_results) + search.encode("utf-16-le") + b"\0\0"
        buf = ctypes.create_string_buffer(payload, len(payload))
        cds = COPYDATASTRUCT(1, len(payload), ctypes.cast(buf, ctypes.c_void_p))
        replies.clear()
        u32.SendMessageW(server_hwnd, WM_COPYDATA, client_hwnd, ctypes.byref(cds))
        pump(300)
        assert replies, "no COPYDATA reply received"
        dw, data = replies[0]
        totfolders, totfiles, totitems, roff = struct.unpack_from("<4I", data, 0)
        out = []
        for i in range(totitems):
            fl, noff, poff = struct.unpack_from("<3I", data, 16 + 12 * i)
            name = data[noff * 2:].decode("utf-16-le", "replace").split("\0", 1)[0]
            path = data[poff * 2:].decode("utf-16-le", "replace").split("\0", 1)[0]
            out.append({"flags": fl, "name": name, "path": path})
        return {"totfolders": totfolders, "totfiles": totfiles,
                "totitems": totitems, "offset": roff, "items": out}

    r1 = query("readme", max_results=5)
    print("WM_COPYDATA query 'readme': totitems=%d totfiles=%d totfolders=%d" %
          (r1["totitems"], r1["totfiles"], r1["totfolders"]))
    print("  first:", r1["items"][0]["path"])
    assert r1["totitems"] > 0 and r1["items"][0]["name"].lower().find("readme") >= 0

    r2 = query("folder: windows", max_results=3, offset=2)
    print("offset/flags query: totitems=%d offset=%d flags[0]=%d" %
          (r2["totitems"], r2["offset"], r2["items"][0]["flags"]))
    assert r2["items"][0]["flags"] & 0x1

    # --- named pipe (external process, like a real client) --------------
    import subprocess
    out = subprocess.run([sys.executable, "tools/pipe_client.py",
                          r"\\.\pipe\EverythingClone", "ext:md readme", "3"],
                         capture_output=True, text=True, timeout=120)
    print("pipe reply:", out.stdout.strip()[:200])
    if not out.stdout.strip():
        print("pipe stderr:", out.stderr[-400:])
    d = json.loads(out.stdout.strip())
    assert d["total"] > 0 and d["items"][0]["name"].lower().endswith(".md")
    print("IPC TESTS PASSED")
    ipc.stop()


if __name__ == "__main__":
    main()
