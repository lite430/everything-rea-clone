"""NTFS raw volume access and MFT parsing.

Reimplements the core indexing mechanism observed by REA reverse analysis of
everything.exe 1.4.1.1028:
  - CreateFileW on \\\\.\\C: with GENERIC_READ + full sharing (evidence: imports CreateFileW)
  - FSCTL_GET_NTFS_VOLUME_DATA / boot sector parse for MFT location
    (evidence: string "DeviceIoControl(%p,FSCTL_GET_NTFS_VOLUME_DATA,...)")
  - direct $MFT record walk with USA fix-up
    (evidence: strings "MFT USA Count %u", "MFT USA Offset %u", "BAD mft record length %d",
     source path ".\\src\\ntfs.c")
  - $FILE_NAME attribute tree to build full paths
"""

import ctypes
import struct
from ctypes import wintypes

GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

FSCTL_GET_NTFS_VOLUME_DATA = 0x00090064
FSCTL_GET_NTFS_FILE_RECORD = 0x00090068
FSCTL_CREATE_USN_JOURNAL = 0x000900E7
FSCTL_READ_USN_JOURNAL = 0x000900BB
FSCTL_QUERY_USN_JOURNAL = 0x000900F4

FILE_RECORD_IN_USE = 0x0001
FILE_RECORD_IS_DIRECTORY = 0x0002

ATTR_STANDARD_INFORMATION = 0x10
ATTR_FILE_NAME = 0x30
ATTR_DATA = 0x80
ATTR_END = 0xFFFFFFFF

k32 = ctypes.windll.kernel32
k32.CreateFileW.restype = wintypes.HANDLE
k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
k32.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_longlong,
                                 ctypes.c_void_p, wintypes.DWORD]
k32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                         ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]


class NtfsError(Exception):
    pass


def win_open(path):
    h = k32.CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                        None, OPEN_EXISTING, 0, None)
    if h == INVALID_HANDLE_VALUE or h is None:
        raise NtfsError("cannot open %s (error %d, admin rights required)" %
                        (path, ctypes.GetLastError()))
    return h


class Volume:
    """Raw handle on one NTFS volume with boot-sector geometry and MFT helpers."""

    def __init__(self, letter):
        self.letter = letter.rstrip(":\\").upper()
        self.path = "\\\\.\\%s:" % self.letter
        self.handle = win_open(self.path)
        bs = self.read_at(0, 512)
        if bs[3:11] != b"NTFS    ":
            k32.CloseHandle(self.handle)
            raise NtfsError("volume %s: is not NTFS" % self.letter)
        self.bytes_per_sector, = struct.unpack_from("<H", bs, 0x0B)
        self.sectors_per_cluster, = struct.unpack_from("<B", bs, 0x0D)
        self.total_sectors, = struct.unpack_from("<Q", bs, 0x28)
        self.mft_lcn, = struct.unpack_from("<Q", bs, 0x30)
        self.mft_mirror_lcn, = struct.unpack_from("<Q", bs, 0x38)
        cpm, = struct.unpack_from("<b", bs, 0x40)
        if cpm < 0:
            self.record_size = 1 << (-cpm)
        else:
            self.record_size = cpm * self.sectors_per_cluster * self.bytes_per_sector
        self.cluster_size = self.sectors_per_cluster * self.bytes_per_sector
        self.sector_usa_count = self.record_size // self.bytes_per_sector
        self._mft_runs = None
        self.mft_record_count = 0

    # ---- raw reads -------------------------------------------------------
    def read_at(self, offset, size):
        buf = ctypes.create_string_buffer(size)
        if not k32.SetFilePointerEx(self.handle, offset, None, 0):
            raise NtfsError("seek to %d failed (%d)" % (offset, ctypes.GetLastError()))
        got = wintypes.DWORD(0)
        if not k32.ReadFile(self.handle, buf, size, ctypes.byref(got), None):
            raise NtfsError("read at %d failed (%d)" % (offset, ctypes.GetLastError()))
        return buf.raw[:got.value]

    def read_lcn(self, lcn, num_clusters):
        return self.read_at(lcn * self.cluster_size, num_clusters * self.cluster_size)

    def close(self):
        k32.CloseHandle(self.handle)

    # ---- run lists -------------------------------------------------------
    def read_mft_bytes(self, start_record, num_records):
        """Read a byte range of $MFT through its data runs."""
        if self._mft_runs is None:
            rec0 = bytearray(self.read_at(self.mft_lcn * self.cluster_size, self.record_size))
            self._fixup(rec0)
            self._mft_runs = self._parse_mft_record0_runs(rec0)
        off = start_record * self.record_size
        end = off + num_records * self.record_size
        out = []
        pos = 0
        for (first_vcn, last_vcn, lcn, clusters) in self._mft_runs:
            run_start = first_vcn * self.cluster_size
            run_end = (last_vcn + 1) * self.cluster_size
            if run_end <= off or run_start >= end:
                continue
            lo = max(off, run_start) - run_start
            hi = min(end, run_end) - run_start
            want = hi - lo
            data = self.read_at(lcn * self.cluster_size + lo, want)
            out.append(data)
            pos += len(data)
        return b"".join(out)

    def _parse_mft_record0_runs(self, rec0):
        attr_off, = struct.unpack_from("<H", rec0, 0x14)
        runs = []
        p = attr_off
        while p + 16 <= len(rec0):
            atype, alen = struct.unpack_from("<II", rec0, p)
            if atype == ATTR_END:
                break
            if atype == ATTR_DATA and alen > 0:
                nonres, name_len, name_off = struct.unpack_from("<BBH", rec0, p + 8)
                if nonres == 1 and name_len == 0:
                    run_off, = struct.unpack_from("<H", rec0, p + 0x20)
                    runs = self._decode_runs(rec0, p + run_off)
                    break
            if alen == 0:
                break
            p += alen
        if not runs:
            # contiguous fallback: whole MFT as one run starting at mft_lcn
            total = (self.total_sectors * self.bytes_per_sector) // self.cluster_size
            runs = [(0, total - self.mft_lcn, self.mft_lcn, total - self.mft_lcn)]
        return runs

    @staticmethod
    def _decode_runs(buf, p):
        """Decode an NTFS data run list into (first_vcn, last_vcn, lcn, clusters)."""
        runs = []
        vcn = 0
        lcn = 0
        n = len(buf)
        while p < n:
            hdr = buf[p]
            if hdr == 0:
                break
            len_sz = hdr & 0x0F
            off_sz = hdr >> 4
            p += 1
            length = int.from_bytes(buf[p:p + len_sz], "little")
            p += len_sz
            if off_sz == 0:
                lcn = None  # sparse
            else:
                raw = buf[p:p + off_sz]
                p += off_sz
                val = int.from_bytes(raw, "little")
                if raw[off_sz - 1] & 0x80:
                    val -= (1 << (off_sz * 8))
                lcn = (lcn or 0) + val
            if lcn is not None and length:
                runs.append((vcn, vcn + length - 1, lcn, length))
            vcn += length
        return runs

    # ---- record parsing ---------------------------------------------------
    def _fixup(self, rec):
        """Apply Update Sequence Array fix-up in place (bytes)."""
        magic = rec[0:4]
        if magic not in (b"FILE", b"BAAD", b"INDX"):
            raise NtfsError("bad MFT record magic %r" % magic)
        usa_off, usa_cnt = struct.unpack_from("<HH", rec, 4)
        if usa_cnt != self.sector_usa_count + 1:
            # non-standard sector/record combo still handled generically
            pass
        usa = struct.unpack_from("<%dH" % usa_cnt, rec, usa_off)
        for i in range(1, usa_cnt):
            off = i * self.bytes_per_sector - 2
            if off + 2 > len(rec):
                break
            struct.pack_into("<H", rec, off, usa[i])

    def parse_record(self, raw):
        """Parse one MFT record -> dict or None when not in use / deleted."""
        if raw[0:4] != b"FILE":
            return None
        rec = bytearray(raw)
        try:
            self._fixup(rec)
        except NtfsError:
            return None
        flags, = struct.unpack_from("<H", rec, 0x16)
        if not (flags & FILE_RECORD_IN_USE):
            return None
        attr_off, = struct.unpack_from("<H", rec, 0x14)
        info = {"frn": None, "seq": struct.unpack_from("<H", rec, 0x10)[0],
                "is_dir": bool(flags & FILE_RECORD_IS_DIRECTORY),
                "names": [], "si": None, "data_size": None}
        p = attr_off
        n = len(rec)
        while p + 8 <= n:
            atype, alen = struct.unpack_from("<II", rec, p)
            if atype == ATTR_END or alen == 0:
                break
            try:
                if atype == ATTR_STANDARD_INFORMATION:
                    info["si"] = self._attr_standard(rec, p)
                elif atype == ATTR_FILE_NAME:
                    fn = self._attr_filename(rec, p)
                    if fn:
                        info["names"].append(fn)
                elif atype == ATTR_DATA:
                    sz = self._attr_data_size(rec, p)
                    if sz is not None and info["data_size"] is None:
                        info["data_size"] = sz
            except struct.error:
                pass
            p += alen
        return info if (info["names"] or info["si"]) else None

    def _attr_standard(self, rec, p):
        nonres, name_len, name_off = struct.unpack_from("<BBH", rec, p + 8)
        if nonres != 0:
            return None
        clen, coff = struct.unpack_from("<IH", rec, p + 0x10)
        q = p + coff
        if q + 0x30 > len(rec):
            return None
        times = struct.unpack_from("<4Q", rec, q)  # created, modified, mft-modified, accessed
        attrs, = struct.unpack_from("<I", rec, q + 0x20)
        return {"created": times[0], "modified": times[1],
                "mft_modified": times[2], "accessed": times[3], "attrs": attrs}

    def _attr_filename(self, rec, p):
        nonres, name_len, name_off = struct.unpack_from("<BBH", rec, p + 8)
        if nonres != 0:
            return None
        clen, coff = struct.unpack_from("<IH", rec, p + 0x10)
        q = p + coff
        if q + 0x42 > len(rec):
            return None
        parent = int.from_bytes(rec[q:q + 8], "little") & 0xFFFFFFFFFFFF
        times = struct.unpack_from("<4Q", rec, q + 8)
        alloc, real = struct.unpack_from("<QQ", rec, q + 0x28)
        flags, = struct.unpack_from("<I", rec, q + 0x38)
        namelen = rec[q + 0x40]
        namespace = rec[q + 0x41]
        if q + 0x42 + namelen * 2 > len(rec):
            return None
        try:
            name = rec[q + 0x42:q + 0x42 + namelen * 2].decode("utf-16-le", "replace")
        except Exception:
            name = ""
        return {"parent": parent, "created": times[0], "modified": times[1],
                "mft_modified": times[2], "accessed": times[3],
                "alloc": alloc, "real": real, "flags": flags,
                "namespace": namespace, "name": name}

    def _attr_data_size(self, rec, p):
        nonres, name_len, name_off = struct.unpack_from("<BBH", rec, p + 8)
        if name_len != 0:
            return None  # named stream (eg ADS) — Everything ignores these too
        if nonres == 0:
            clen, coff = struct.unpack_from("<IH", rec, p + 0x10)
            return clen
        real, = struct.unpack_from("<Q", rec, p + 0x38)
        return real

    # ---- USN journal ------------------------------------------------------
    def device_io_control(self, code, in_buf, out_size):
        in_buf = in_buf or b""
        out = ctypes.create_string_buffer(out_size)
        ret = wintypes.DWORD(0)
        ok = k32.DeviceIoControl(self.handle, code, in_buf, len(in_buf),
                                 out, out_size, ctypes.byref(ret), None)
        if not ok:
            raise NtfsError("DeviceIoControl(0x%x) failed: %d" % (code, ctypes.GetLastError()))
        return out.raw[:ret.value]

    def query_usn_journal(self):
        """-> (journal_id, next_usn) or None when the journal does not exist."""
        try:
            raw = self.device_io_control(FSCTL_QUERY_USN_JOURNAL, None, 64)
        except NtfsError:
            return None
        jid, = struct.unpack_from("<Q", raw, 0)
        first, next_usn = struct.unpack_from("<QQ", raw, 8)
        return (jid, next_usn)

    def create_usn_journal(self):
        buf = struct.pack("<QQ", 0, 0)  # maximum size / delta: 0 = system defaults
        try:
            self.device_io_control(FSCTL_CREATE_USN_JOURNAL, buf, 0)
            return True
        except NtfsError:
            return False

    def read_usn_from(self, journal_id, usn, buf_size=1 << 16):
        """FSCTL_READ_USN_JOURNAL from a USN; -> (next_usn, [records]).

        Input is READ_USN_JOURNAL_DATA_V0: 40 bytes =
        StartUsn(8) ReasonMask(4) ReturnOnlyOnLowVolumeSpace(4) Timeout(8)
        BytesToWaitFor(4) UsnJournalID(8).
        """
        s = struct.pack("<QIIQI4xQ", usn, 0xFFFFFFFF, 0, 0, 0, journal_id)
        raw = self.device_io_control(FSCTL_READ_USN_JOURNAL, s, buf_size)
        if len(raw) < 8:
            return usn, []
        next_usn, = struct.unpack_from("<Q", raw, 0)
        recs = []
        p = 8
        end = len(raw)
        while p + 8 <= end:
            rlen, = struct.unpack_from("<I", raw, p)
            if rlen < 8 or p + rlen > end:
                break
            if rlen >= 0x40:
                frn = int.from_bytes(raw[p + 8:p + 16], "little") & 0xFFFFFFFFFFFF
                parent = int.from_bytes(raw[p + 16:p + 24], "little") & 0xFFFFFFFFFFFF
                reason, = struct.unpack_from("<I", raw, p + 24)
                attrs, = struct.unpack_from("<I", raw, p + 36)
                namelen, = struct.unpack_from("<H", raw, p + 56)   # bytes, not chars
                nameoff, = struct.unpack_from("<H", raw, p + 58)
                name = raw[p + nameoff:p + nameoff + namelen].decode("utf-16-le", "replace")
                recs.append({"frn": frn, "parent": parent, "reason": reason,
                             "attrs": attrs, "name": name})
            p += (rlen + 7) & ~7 if rlen % 8 else rlen
        return next_usn, recs

    def read_record_bytes(self, frn):
        """Read the raw MFT record for a file reference number.

        Uses FSCTL_GET_NTFS_FILE_RECORD exactly like everything.exe (see the
        decompiled ntfs-get-fd routine): the NTFS driver returns the LIVE
        in-memory record, which raw \$MFT reads can miss for a few seconds
        after a change.  Output: frn(8) + record_length(4) + record bytes.
        The returned record has the USA slots zeroed but the USA array is
        intact, so the normal fix-up restores the real bytes.
        """
        out_size = 16 + self.record_size * 2
        out = ctypes.create_string_buffer(out_size)
        inp = struct.pack("<Q", frn)
        ret = wintypes.DWORD(0)
        ok = k32.DeviceIoControl(self.handle, FSCTL_GET_NTFS_FILE_RECORD, inp, 8,
                                 out, out_size, ctypes.byref(ret), None)
        if not ok:
            raise NtfsError("get record %d failed (%d)" % (frn, ctypes.GetLastError()))
        raw = out.raw[:ret.value]
        if len(raw) < 16:
            return None
        got = struct.unpack_from("<Q", raw, 0)[0] & 0xFFFFFFFFFFFF
        if got != frn:
            return None  # "FRN not in use" — same check as everything.exe
        reclen = struct.unpack_from("<I", raw, 8)[0]
        return raw[12:12 + reclen]

    def read_record_bytes_raw(self, frn):
        return self.read_mft_bytes(frn, 1)
