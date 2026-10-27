"""EVERYTHING_IPC compatible IPC server.

Mirrors the IPC machinery observed in everything.exe via REA evidence:
  - imports: CreateNamedPipeW / ConnectNamedPipe (named pipe transport)
  - strings: "EVERYTHING_IPC_COPYDATA_COMMAND_LINE_UTF8", "EVERYTHING_IPC_CREATED",
    "IPC: execute query: %s", "IPC: query complete: %u max results, offset %d,
    reply hwnd %d", "-install-service-pipe-name <name>"

Two transports, same engine:

1. WM_COPYDATA window of class "EVERYTHING" (what the official SDK clients find
   with FindWindow(L"EVERYTHING", NULL)).  Query packet layout from the SDK
   (everything.h):

     EVERYTHING_IPC_QUERY (dwData = EVERYTHING_IPC_COPYDATAQUERY = 1, W):
       DWORD reply_hwnd;            // window to receive the reply
       DWORD reply_copydata_message;// dwData for the reply (clients use 0)
       DWORD search_flags;          // MATCHCASE 1 | MATCHWHOLEWORD 2 | MATCHPATH 4 | REGEX 8
       DWORD offset;
       DWORD max_results;           // 0xFFFFFFFF = all
       WCHAR search_string[];       // NUL terminated

     EVERYTHING_IPC_LIST reply (items + strings appended):
       DWORD totfolders; DWORD totfiles; DWORD totitems; DWORD offset;
       struct { DWORD flags; DWORD filename_offset; DWORD path_offset; } items[];
       // offsets are in WCHARs from the start of the list

2. Named pipe (default \\\\.\\pipe\\EverythingClone, renamable like the
   original's -install-service-pipe-name).  Line protocol:
       {"q": "search", "count": 100, "offset": 0, "sort": "name"}\n
   -> one JSON line: {"total": N, "items": [{"name","path","size","is_dir"}]}
   A bare non-JSON line is treated as a plain search string.
"""

import ctypes
import json
import struct
import threading
from ctypes import wintypes

WM_COPYDATA = 0x004A
WM_QUIT = 0x0012
HWND_BROADCAST = 0xFFFF

EVERYTHING_IPC_COPYDATA_QUERYREPLY = 0
EVERYTHING_IPC_COPYDATAQUERY = 1
EVERYTHING_IPC_COPYDATA_COMMAND_LINE_UTF8 = 4

EVERYTHING_IPC_ALLRESULTS = 0xFFFFFFFF
EVERYTHING_IPC_FOLDER = 0x1
EVERYTHING_IPC_DRIVE = 0x2

IPC_MATCHCASE = 0x1
IPC_MATCHWHOLEWORD = 0x2
IPC_MATCHPATH = 0x4
IPC_REGEX = 0x8

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32
# x64-safe prototypes (default int conversion overflows on pointer-sized args)
u32.DefWindowProcW.restype = ctypes.c_ssize_t
u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                               wintypes.WPARAM, wintypes.LPARAM]
u32.CreateWindowExW.restype = wintypes.HWND
u32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                wintypes.HINSTANCE, ctypes.c_void_p]
u32.SendMessageW.restype = ctypes.c_ssize_t
u32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
u32.PostMessageW.restype = wintypes.BOOL
u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
k32.CreateNamedPipeW.restype = wintypes.HANDLE
k32.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.DWORD, ctypes.c_void_p]

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class COPYDATASTRUCT(ctypes.Structure):
    _fields_ = [("dwData", ctypes.c_size_t),
                ("cbData", wintypes.DWORD),
                ("lpData", ctypes.c_void_p)]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HANDLE),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


def _flags_to_mods(search_flags):
    mods = []
    if search_flags & IPC_MATCHCASE:
        mods.append("case:")
    if search_flags & IPC_MATCHWHOLEWORD:
        mods.append("wholeword:")
    if search_flags & IPC_MATCHPATH:
        mods.append("matchpath:")
    if search_flags & IPC_REGEX:
        mods.append("regex:")
    return " ".join(mods)


class IpcServer:
    """WM_COPYDATA 'EVERYTHING' window + named pipe, both driven by App.search."""

    def __init__(self, app, pipe_name=r"\\.\pipe\EverythingClone", log=print):
        self.app = app
        self.pipe_name = pipe_name
        self.log = log
        self.hwnd = None
        self._thread = None
        self._pipe_thread = None
        self._wndproc = None  # keep callback alive
        self._stop = threading.Event()

    def start(self):
        self._thread = threading.Thread(target=self._window_loop, daemon=True)
        self._thread.start()
        self._pipe_thread = threading.Thread(target=self._pipe_loop, daemon=True)
        self._pipe_thread.start()
        return self

    def stop(self):
        self._stop.set()
        if self.hwnd:
            u32.PostMessageW(self.hwnd, WM_QUIT, 0, 0)

    # ---- WM_COPYDATA window -------------------------------------------
    def _window_loop(self):
        inst = k32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = WNDPROC(self._wnd_proc)
        wc.hInstance = inst
        wc.lpszClassName = "EVERYTHING"
        self._wndproc = wc.lpfnWndProc
        if not u32.RegisterClassW(ctypes.byref(wc)):
            # class already registered (second start in same process)
            pass
        self.hwnd = u32.CreateWindowExW(0, "EVERYTHING", "Everything Clone IPC",
                                        0, 0, 0, 0, 0, None, None, inst, None)
        # broadcast EVERYTHING_IPC_CREATED like the original announces itself
        created_msg = u32.RegisterWindowMessageW("EVERYTHING_IPC_CREATED")
        if created_msg:
            u32.PostMessageW(HWND_BROADCAST, created_msg, 0, 0)
        msg = wintypes.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_COPYDATA:
            try:
                self._on_copydata(hwnd, wparam, lparam)
            except Exception as exc:  # never kill the window proc
                try:
                    self.log("IPC error: %r" % exc)
                except Exception:
                    pass
            return 0
        return u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _on_copydata(self, hwnd, wparam, lparam):
        cds = ctypes.cast(lparam, ctypes.POINTER(COPYDATASTRUCT)).contents
        kind = cds.dwData & 0xFFFFFFFF
        data = ctypes.string_at(cds.lpData, cds.cbData) if cds.cbData else b""
        if kind == EVERYTHING_IPC_COPYDATAQUERY:
            self._handle_query(hwnd, data)
        elif kind == EVERYTHING_IPC_COPYDATA_COMMAND_LINE_UTF8:
            cmdline = data.split(b"\0", 1)[0].decode("utf-8", "replace")
            self.log("IPC: execute command line: %s" % cmdline)
        # QUERYREPLY (0) is what we *send*; ignore any inbound copies.

    def _handle_query(self, from_hwnd, data):
        if len(data) < 20:
            return
        reply_hwnd, reply_msg, search_flags, offset, max_results = \
            struct.unpack_from("<5I", data, 0)
        wide = data[20:].decode("utf-16-le", "replace")
        search = wide.split("\0", 1)[0]
        q = (_flags_to_mods(search_flags) + " " + search).strip()
        self.log("IPC: execute query: %s" % q)
        if max_results == 0 or max_results == EVERYTHING_IPC_ALLRESULTS:
            limit = 0xFFFFFFFF - 1
        else:
            limit = max_results
        page, total = self.app.search(q, sort=None, ascending=True,
                                      limit=limit, offset=offset)
        page = page[:max_results] if max_results != EVERYTHING_IPC_ALLRESULTS else page
        self.log("IPC: query complete: %u max results, offset %d, reply hwnd %x"
                 % (len(page), offset, reply_hwnd))

        items = page
        totfolders = sum(1 for e in items if e.is_dir)
        totfiles = len(items) - totfolders
        head = struct.pack("<4I", totfolders, totfiles, len(items), offset)
        item_recs = b""
        strings = b""
        # string area starts after the fixed part; offsets in WCHARs
        str_base_wchars = (16 + 12 * len(items)) // 2
        running = str_base_wchars
        for e in items:
            name_w = e.name.encode("utf-16-le") + b"\x00\x00"
            path_w = e.path.encode("utf-16-le") + b"\x00\x00"
            flags = (EVERYTHING_IPC_FOLDER if e.is_dir else 0) | \
                    (EVERYTHING_IPC_DRIVE if e.path.endswith(":") else 0)
            item_recs += struct.pack("<3I", flags, running, running + len(name_w) // 2)
            strings += name_w + path_w
            running += (len(name_w) + len(path_w)) // 2
        payload = head + item_recs + strings
        buf = ctypes.create_string_buffer(payload, len(payload))
        cds = COPYDATASTRUCT(reply_msg, len(payload), ctypes.cast(buf, ctypes.c_void_p))
        u32.SendMessageW(reply_hwnd, WM_COPYDATA, self.hwnd, ctypes.byref(cds))

    # ---- named pipe -----------------------------------------------------
    def _pipe_loop(self):
        try:
            self._pipe_loop_inner()
        except Exception as exc:
            try:
                self.log("IPC pipe thread crashed: %r" % exc)
            except Exception:
                pass

    def _pipe_loop_inner(self):
        PIPE_ACCESS_DUPLEX = 0x3
        PIPE_TYPE_BYTE = 0x0
        PIPE_READMODE_BYTE = 0x0
        PIPE_WAIT = 0x0
        PIPE_UNLIMITED_INSTANCES = 255
        while not self._stop.is_set():
            h = k32.CreateNamedPipeW(self.pipe_name, PIPE_ACCESS_DUPLEX,
                                     PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
                                     PIPE_UNLIMITED_INSTANCES, 1 << 16, 1 << 16, 0, None)
            if h in (wintypes.HANDLE(-1).value, None, 0):
                self.log("IPC pipe create failed: error %d (name %s)"
                         % (ctypes.GetLastError(), self.pipe_name))
                self._stop.wait(5)
                continue
            if k32.ConnectNamedPipe(h, None) or ctypes.GetLastError() == 535:  # ERROR_NO_DATA
                pass
            try:
                self._pipe_serve(h)
            except Exception as exc:
                self.log("IPC pipe error: %r" % exc)
            finally:
                k32.FlushFileBuffers(h)
                k32.DisconnectNamedPipe(h)
                k32.CloseHandle(h)

    def _pipe_serve(self, h):
        buf = ctypes.create_string_buffer(8192)
        got = wintypes.DWORD(0)
        data = b""
        # read one newline-terminated request
        while b"\n" not in data:
            if not k32.ReadFile(h, buf, 8192, ctypes.byref(got), None) or got.value == 0:
                return
            data += buf.raw[:got.value]
            if len(data) > (1 << 20):
                return
        line = data.split(b"\n", 1)[0].strip()
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError:
            text = line.decode("utf-8", "replace")
        req = {}
        if text.startswith("{"):
            try:
                req = json.loads(text)
            except ValueError:
                req = {}
        q = str(req.get("q", text))
        count = int(req.get("count", 100))
        offset = int(req.get("offset", 0))
        sort = req.get("sort")
        page, total = self.app.search(q, sort=sort, ascending=True,
                                      limit=count, offset=offset)
        resp = json.dumps({"total": total, "items": [
            {"name": e.name, "path": e.path, "size": e.size, "is_dir": e.is_dir}
            for e in page]}, ensure_ascii=False) + "\n"
        raw = resp.encode("utf-8")
        sent = wintypes.DWORD(0)
        k32.WriteFile(h, raw, len(raw), ctypes.byref(sent), None)
