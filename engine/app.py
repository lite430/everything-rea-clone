"""Application model: multi-volume index, USN live monitoring, search API.

Mirrors everything.exe behavior observed via REA evidence:
  - per-volume monitor threads ("%d: add ntfs monitor %s", "%d ntfs monitors started")
  - instant search over the whole index ("Allocating file indexes...")
  - status counts shown in the status bar (objects / results)
"""

import ctypes
import os
import threading
import time

from .index import VolumeIndex, ROOT_FRN
from .query import Query

SORT_KEYS = {
    "name": lambda e: (e.lname, e.path),
    "path": lambda e: (e.path.lower(), e.lname),
    "size": lambda e: e.size,
    "dm": lambda e: e.modified,
    "dc": lambda e: e.created,
    "ext": lambda e: (e.ext, e.lname),
    "attrib": lambda e: e.attrs,
}


def ntfs_drive_letters():
    letters = []
    bitmask = ctypes.windll.kernel32.GetLogicalDrives()
    for i in range(26):
        if bitmask & (1 << i):
            letter = chr(ord("A") + i)
            fs = ctypes.create_unicode_buffer(32)
            ok = ctypes.windll.kernel32.GetVolumeInformationW(
                ctypes.c_wchar_p(letter + ":\\"), None, 0, None, None, None, fs, 32)
            if ok and fs.value == "NTFS":
                letters.append(letter)
    return letters


# only structural changes matter to a filename index; skipping frequent
# data-overwrite/extend/truncate reasons keeps the monitor from falling
# behind on busy systems (log files churn constantly)
USN_REASON_STRUCTURAL = (0x100 | 0x200 | 0x1000 | 0x2000 | 0x8000 | 0x10000)


class UsnMonitor(threading.Thread):
    def __init__(self, vol_index, app, interval=1.0):
        super().__init__(daemon=True)
        self.vi = vol_index
        self.app = app
        self.interval = interval
        self.stop_flag = threading.Event()
        self.last_events = []   # recent change log for the UI
        self.ok = False

    def run(self):
        if not self.vi.start_journal(create=False):
            # Everything offers to create the journal; we create silently too
            if not self.vi.start_journal(create=True):
                return
        self.ok = True
        while not self.stop_flag.is_set():
            try:
                touched = self.vi.poll_usn(max_reads=8, reason_mask=USN_REASON_STRUCTURAL)
            except Exception:
                time.sleep(5)
                continue
            if touched:
                self._apply(touched)
            self.stop_flag.wait(self.interval)

    def _apply(self, touched):
        vi = self.vi
        # dedupe per batch, order preserved (reasons already filtered in poll)
        seen = set()
        batch = []
        for frn, reason, name in touched:
            if frn in seen:
                continue
            seen.add(frn)
            batch.append((frn, reason, name))
        # refresh records in small chunks so searches never wait long on the lock
        for chunk_start in range(0, len(batch), 32):
            chunk = batch[chunk_start:chunk_start + 32]
            with self.app.lock:
                for frn, reason, name in chunk:
                    if frn == ROOT_FRN or frn <= 23:
                        continue
                    before = vi.by_frn.get(frn)
                    b_names = [e.name for e in before] if before else []
                    try:
                        state = vi.refresh_frn(frn)
                    except Exception:
                        state = None
                    if state:
                        after = vi.by_frn.get(frn)
                        a_names = [e.name for e in after] if after else []
                        if b_names != a_names:
                            self.last_events.append(
                                (time.time(), vi.letter,
                                 a_names[0] if a_names else (b_names[0] if b_names else str(frn)),
                                 state))
                            self.last_events = self.last_events[-500:]


class App:
    def __init__(self, letters=None):
        self.volumes = []            # list[VolumeIndex]
        self.monitors = []           # list[UsnMonitor]
        self.file_lists = []         # list[(label, [Entry, ...])]
        self.lock = threading.RLock()
        self.started_at = None
        self.scan_seconds = None
        self.letters = letters

    def start(self, progress=None):
        self.started_at = time.time()
        letters = self.letters or ntfs_drive_letters()
        for letter in letters:
            try:
                vi = VolumeIndex(letter)
                vi.scan(progress)
                self.volumes.append(vi)
            except Exception as exc:
                print("volume %s skipped: %s" % (letter, exc))
        self.scan_seconds = time.time() - self.started_at
        for vi in self.volumes:
            m = UsnMonitor(vi, self)
            m.start()
            self.monitors.append(m)
        return self

    # ---- search ------------------------------------------------------------
    def search(self, query_text, sort=None, ascending=True, limit=None, offset=0,
               count_only=False):
        q = Query(query_text) if query_text.strip() else None
        with self.lock:
            if q:
                terms, filters = q._plan(q.tree)
                dupe = q._has_dupe
                dupe_name_only = q._dupe_name_only
            else:
                terms, filters, dupe, dupe_name_only = [], [], False, False

            # fast path: plain lowercase substring terms resolved through the
            # per-volume name blob (C-speed whole-buffer scans, like Everything)
            plain = []
            rest_terms = []
            for t in terms:
                cap = getattr(t, "fast_blob", None)
                if cap:
                    plain.append(cap)
                else:
                    rest_terms.append(t)

            if plain:
                results = []
                for vi in self.volumes:
                    lists = [vi.find_substring(needle) for needle in plain]
                    if len(lists) == 1:
                        results.extend(lists[0])
                    else:
                        lists.sort(key=len)
                        small = lists[0]
                        sets = [set(map(id, l)) for l in lists[1:]]
                        results.extend(e for e in small
                                       if all(id(e) in s for s in sets))
            else:
                results = None

            if rest_terms or filters or results is None or not plain:
                def iter_all():
                    for vi in self.volumes:
                        for lst in vi.by_frn.values():
                            for e in lst:
                                yield e
                base = results if results is not None else iter_all()
                volume_hits = []
                for e in base:
                    ok = True
                    for t in rest_terms:
                        if not t(e):
                            ok = False
                            break
                    if ok:
                        for f in filters:
                            if not f(e):
                                ok = False
                                break
                    if ok:
                        volume_hits.append(e)
                results = volume_hits

            # offline file lists are always scanned with the predicate path
            if self.file_lists:
                fl_hits = []
                for _label, fl_entries in self.file_lists:
                    for e in fl_entries:
                        ok = True
                        for t in terms:
                            if not t(e):
                                ok = False
                                break
                        if ok:
                            for f in filters:
                                if not f(e):
                                    ok = False
                                    break
                        if ok:
                            fl_hits.append(e)
                results = (results or []) + fl_hits
            if dupe:
                results = Query._dupes(results, dupe_name_only)
        if count_only:
            return None, len(results)
        if sort and sort in SORT_KEYS:
            key = SORT_KEYS[sort]
            try:
                results.sort(key=key, reverse=not ascending)
            except TypeError:
                results.sort(key=lambda e: (e.lname,), reverse=not ascending)
        else:
            results.sort(key=lambda e: e.lname, reverse=not ascending)
        total = len(results)
        page = results[offset:] if limit is None else results[offset:offset + limit]
        return page, total

    # ---- stats -------------------------------------------------------------
    def stats(self):
        with self.lock:
            files = folders = 0
            for vi in self.volumes:
                if vi.blob_dirty:
                    f = sum(1 for lst in vi.by_frn.values() for e in lst if not e.is_dir)
                    d = sum(1 for lst in vi.by_frn.values() for e in lst if e.is_dir)
                    vi.counts = {"files": f, "folders": d}
                files += vi.counts["files"]
                folders += vi.counts["folders"]
            vols = [{"letter": vi.letter,
                     "files": vi.counts["files"],
                     "folders": vi.counts["folders"],
                     "usn_active": m.is_alive() and m.ok} for vi, m in zip(self.volumes, self.monitors)]
            fl_files = sum(1 for _l, es in self.file_lists for e in es if not e.is_dir)
            fl_folders = sum(1 for _l, es in self.file_lists for e in es if e.is_dir)
        return {"objects": files + folders + fl_files + fl_folders,
                "files": files + fl_files, "folders": folders + fl_folders,
                "volumes": vols,
                "file_lists": [{"name": l, "count": len(es)} for l, es in self.file_lists],
                "scan_seconds": self.scan_seconds}

    def recent_changes(self, n=50):
        events = []
        for m in self.monitors:
            events.extend(m.last_events)
        events.sort(reverse=True)
        return [{"time": t, "volume": v, "name": name, "state": state}
                for t, v, name, state in events[:n]]

    # ---- thumbnails / preview ---------------------------------------------
    # Only paths that exist in the index are served (no arbitrary file reads),
    # mirroring how Everything's thumbnail views operate on its own results.
    _THUMB_CACHE = {}
    _THUMB_CACHE_MAX = 400

    def _find_by_path(self, path):
        low = path.lower().rstrip("\\")
        with self.lock:
            for vi in self.volumes:
                vi.ensure_blob(min_age=30.0)
                e = vi.path_map.get(low)
                if e is not None:
                    return e
            return None

    def thumbnail(self, path, size=96):
        """-> JPEG bytes for an indexed image file, or None."""
        from .query import PIC_EXTS
        e = self._find_by_path(path)
        if e is None or e.is_dir or e.ext not in PIC_EXTS:
            return None
        try:
            st = os.stat(e.path)
        except OSError:
            return None
        key = (e.path.lower(), st.st_mtime_ns, size)
        hit = App._THUMB_CACHE.get(key)
        if hit:
            return hit
        from PIL import Image
        try:
            with Image.open(e.path) as im:
                im = im.convert("RGB")
                im.thumbnail((size, size))
                import io
                out = io.BytesIO()
                im.save(out, "JPEG", quality=82)
                data = out.getvalue()
        except Exception:
            return None
        if len(App._THUMB_CACHE) >= App._THUMB_CACHE_MAX:
            App._THUMB_CACHE.clear()
        App._THUMB_CACHE[key] = data
        return data

    def preview(self, path, max_bytes=262144):
        """-> (mime, bytes) preview for small text files, else None."""
        from .query import DOC_EXTS
        e = self._find_by_path(path)
        if e is None or e.is_dir or e.ext not in DOC_EXTS or e.size > max_bytes:
            return None
        try:
            with open(e.path, "rb") as f:
                return ("text/plain; charset=utf-8", f.read(max_bytes))
        except OSError:
            return None
