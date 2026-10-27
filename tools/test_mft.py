import struct, ctypes
from ctypes import wintypes

GENERIC_READ = 0x80000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

k32 = ctypes.windll.kernel32
k32.CreateFileW.restype = wintypes.HANDLE
k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]

vol = "\\\\.\\C:"
h = k32.CreateFileW(vol, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
if h == INVALID_HANDLE_VALUE or h is None:
    print("CreateFileW failed", ctypes.GetLastError())
    raise SystemExit(1)

def read_at(off, n):
    b = ctypes.create_string_buffer(n)
    low = off & 0xFFFFFFFF
    high = ctypes.c_long((off >> 32) & 0xFFFFFFFF)
    k32.SetFilePointer(h, low, ctypes.byref(high), 0)
    got = wintypes.DWORD(0)
    ok = k32.ReadFile(h, b, n, ctypes.byref(got), None)
    if not ok:
        print("read fail", ctypes.GetLastError())
        raise SystemExit(1)
    return b.raw[:got.value]

bs = read_at(0, 512)
print("OEM:", bs[3:11])
bps, = struct.unpack_from("<H", bs, 11)
spc, = struct.unpack_from("<B", bs, 13)
mft_lcn, = struct.unpack_from("<Q", bs, 48)
print("bytes/sector:", bps, "sectors/cluster:", spc, "MFT LCN:", mft_lcn)

rec = bytearray(read_at(mft_lcn * spc * bps, 1024))
print("magic:", bytes(rec[:4]))
usa_off, = struct.unpack_from("<H", rec, 4)
usa_cnt, = struct.unpack_from("<H", rec, 6)
usa = struct.unpack_from("<%dH" % usa_cnt, rec, usa_off)
for i in range(1, usa_cnt):
    off = (i - 1) * 512 - 2
    struct.pack_into("<H", rec, off, usa[i])
print("fixup ok:", bytes(rec[:4]))
k32.CloseHandle(h)
