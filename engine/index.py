"""In-memory filename index built from NTFS MFT scans + USN monitoring.

Mirrors everything.exe's indexer design observed via REA evidence:
  - "Allocating file indexes..." / "Allocating folder indexes..." (parallel entry arrays)
  - per-volume NTFS monitor threads ("add ntfs monitor %s", "%d ntfs monitors started")
  - full paths resolved through the $FILE_NAME parent chain
"""

import os
import time

from .ntfs import Volume

ROOT_FRN = 5
# NTFS metadata files ($MFT, $LogFile, ...) are hidden from results, same as Everything.
META_MAX_FRN = 23

_EPOCH = 116444736000000000  # FILETIME(1601) -> unix(1970) delta in 100ns


def ft_unix(ft):
    if not ft:
        return 0
    return (ft - _EPOCH) // 10000000


class Entry:
    __slots__ = ("vol", "frn", "name", "lname", "lbytes", "is_dir", "size",
                 "created", "modified", "attrs", "path", "parent")

    def __init__(self, vol, frn, name, is_dir, size, created, modified, attrs, parent):
        self.vol = vol
        self.frn = frn
        self.name = name
        self.lname = name.lower()
        self.lbytes = self.lname.encode("utf-8", "replace")
        self.is_dir = is_dir
        self.size = size
        self.created = created
        self.modified = modified
        self.attrs = attrs
        self.parent = parent          # parent frn (or 0 for root)
        self.path = None

    @property
    def ext(self):
        _, e = os.path.splitext(self.name)
        return e[1:].lower()


class VolumeIndex:
    def __init__(self, letter):
        self.letter = letter
        self.vol = Volume(letter)
        self.by_frn = {}   # frn -> list[Entry] (hard links have several names)
        self.journal = None  # (journal_id, next_usn) after scan
        self.counts = {"files": 0, "folders": 0}
        self.blob = None
        self.flat_entries = []
        self.flat_offsets = []
        self.path_map = {}
        self.blob_dirty = True
        self.blob_force = False
        self.blob_time = 0.0

    # ---- initial scan -----------------------------------------------------
    def scan(self, progress=None):
        vol = self.vol
        rec_size = vol.record_size
        chunk_records = 4096
        n = 0
        t0 = time.time()
        while True:
            try:
                chunk = vol.read_mft_bytes(n, chunk_records)
            except Exception:
                break
            nrec = len(chunk) // rec_size
            if nrec == 0:
                break
            all_zero = True
            for i in range(nrec):
                raw = chunk[i * rec_size:(i + 1) * rec_size]
                if raw[0:4] != b"FILE":
                    if all_zero and raw.count(0) != len(raw):
                        all_zero = False
                    continue
                all_zero = False
                frn = n + i
                if frn != ROOT_FRN and frn <= META_MAX_FRN:
                    # parse root anyway to anchor the tree; skip other metafiles
                    if frn != ROOT_FRN:
                        continue
                info = vol.parse_record(raw)
                if info is None:
                    continue
                self._add_record(frn, info)
            n += nrec
            if progress:
                progress(n, time.time() - t0)
            if nrec < chunk_records or all_zero:
                break
        self._link_paths()
        files = folders = 0
        for lst in self.by_frn.values():
            for e in lst:
                if e.is_dir:
                    folders += 1
                else:
                    files += 1
        self.counts = {"files": files, "folders": folders}
        return n

    def _add_record(self, frn, info):
        names = []
        has_long = any(fn["namespace"] in (0, 1) for fn in info["names"])
        for fn in info["names"]:
            if frn == ROOT_FRN:
                if fn["name"] in (".", ".."):
                    names.append({"parent": ROOT_FRN, "name": "", "created": 0,
                                  "modified": 0})
                    continue
            if has_long and fn["namespace"] in (2, 3):
                continue
            if not fn["name"] or fn["name"] in (".", ".."):
                continue
            names.append(fn)
        if not names:
            return
        si = info.get("si")
        created = modified = 0
        attrs = 0x80
        if si:
            created = ft_unix(si["created"])
            modified = ft_unix(si["modified"])
            attrs = si["attrs"]
        is_dir = info["is_dir"] or bool(attrs & 0x10)
        size = 0 if is_dir else (info.get("data_size") or 0)
        lst = []
        for fn in names:
            if not created:
                created = ft_unix(fn["created"])
            if not modified:
                modified = ft_unix(fn["modified"])
            lst.append(Entry(self.letter, frn, fn["name"], is_dir, size,
                             created, modified, attrs, fn["parent"]))
        self.by_frn[frn] = lst

    # ---- path resolution ---------------------------------------------------
    def _link_paths(self):
        # drop orphans (broken parent chain, e.g. records kept after parent removal)
        prefix = self.letter + ":"
        for frn in list(self.by_frn.keys()):
            if frn == ROOT_FRN:
                lst = self.by_frn[frn]
                for e in lst:
                    e.path = prefix
                continue
            lst = self.by_frn[frn]
            keep = []
            for e in lst:
                e.path = self._path_of(e)
                if e.path.startswith(prefix):
                    keep.append(e)
            if keep:
                self.by_frn[frn] = keep
            else:
                del self.by_frn[frn]

    def _path_of(self, e):
        parts = []
        cur = e
        hops = 0
        while cur is not None and hops < 128:
            hops += 1
            if cur.frn == ROOT_FRN:
                parts.append(self.letter + ":")
                break
            parts.append(cur.name)
            nxt = self.by_frn.get(cur.parent)
            cur = nxt[0] if nxt else None
        return "\\".join(reversed(parts))

    # ---- live updates -------------------------------------------------------
    def refresh_frn(self, frn):
        """Re-read one MFT record and upsert/remove it (used by USN monitor).

        Goes through FSCTL_GET_NTFS_FILE_RECORD (live NTFS view) so changes
        that are still in the filesystem cache are visible immediately —
        same design as everything.exe's ntfs get-fd routine.
        """
        vol = self.vol
        try:
            raw = vol.read_record_bytes(frn)
        except Exception:
            raw = None
        if raw is None or len(raw) < 64 or raw[0:4] != b"FILE":
            self.by_frn.pop(frn, None)
            self.blob_dirty = True
            return "removed"
        info = vol.parse_record(raw)
        if info is None:
            self.by_frn.pop(frn, None)
            self.blob_dirty = True
            return "removed"
        old = self.by_frn.get(frn)
        self._add_record(frn, info)
        lst = self.by_frn.get(frn)
        self.blob_force = True
        if lst:
            # re-link only affected entries + children of this folder need path
            # refresh; do it lazily: mark children dirty is costly, so recompute
            # paths for this frn's subtree only when name changed
            if old and [e.name for e in old] != [e.name for e in lst]:
                self._relink_subtree(frn)
            else:
                for e in lst:
                    e.path = self._path_of(e)
            self.blob_dirty = True
            return "updated"
        self.blob_dirty = True
        return "removed"

    def _relink_subtree(self, frn):
        # children = records whose parent == frn
        children = [f for f, lst in self.by_frn.items() if lst and lst[0].parent == frn]
        for e in self.by_frn.get(frn, []):
            e.path = self._path_of(e)
        for c in children:
            self._relink_subtree(c)

    # ---- journal -------------------------------------------------------------
    def start_journal(self, create=True):
        q = self.vol.query_usn_journal()
        if q is None:
            if create:
                if not self.vol.create_usn_journal():
                    return False
                q = self.vol.query_usn_journal()
            if q is None:
                return False
        self.journal = q
        return True

    def poll_usn(self, max_reads=8, reason_mask=0xFFFFFFFF):
        """Read new USN records; -> list of (frn, reason, name).

        Bounded to max_reads buffer passes per call so one poll cannot chase a
        busy journal forever; the next poll continues from the committed USN.
        """
        if not self.journal:
            return []
        jid, usn = self.journal
        touched = []
        reads = 0
        while reads < max_reads:
            reads += 1
            try:
                next_usn, recs = self.vol.read_usn_from(jid, usn)
            except Exception:
                # journal may have been recreated
                self.start_journal(create=False)
                return touched
            for r in recs:
                if r["reason"] & reason_mask:
                    touched.append((r["frn"], r["reason"], r["name"]))
            if next_usn == usn or not recs:
                usn = next_usn
                break
            usn = next_usn
        self.journal = (jid, usn)
        return touched

    # ---- fast substring search blob (Everything-style whole-index scan) ----
    def rebuild_blob(self):
        """Concatenate all lowercase names into one buffer for C-speed scanning."""
        import time as _t
        from itertools import accumulate
        flat = []
        parts = []
        path_map = {}
        for lst in self.by_frn.values():
            for e in lst:
                flat.append(e)
                path_map[e.path.lower()] = e
                parts.append(e.lbytes)
        lengths = [len(p) + 1 for p in parts]
        offsets = [0]
        offsets.extend(accumulate(lengths[:-1])) if len(lengths) > 1 else None
        self.flat_entries = flat
        self.flat_offsets = offsets
        self.path_map = path_map
        self.blob = b"\n".join(parts)
        self.blob_dirty = False
        self.blob_force = False
        self.blob_time = _t.time()

    def ensure_blob(self, min_age=1.0):
        import time as _t
        if self.blob is None:
            self.rebuild_blob()
        elif self.blob_force or (self.blob_dirty and
                                 (_t.time() - self.blob_time) >= min_age):
            self.rebuild_blob()

    def find_substring(self, needle_lower):
        """-> list of entries whose lowercase name contains needle."""
        self.ensure_blob()
        blob = self.blob
        offs = self.flat_offsets
        out = []
        pos = blob.find(needle_lower)
        import bisect
        while pos >= 0:
            idx = bisect.bisect_right(offs, pos) - 1
            out.append(self.flat_entries[idx])
            pos = blob.find(needle_lower, pos + 1)
        return out

    def entries(self):
        for lst in self.by_frn.values():
            for e in lst:
                yield e
