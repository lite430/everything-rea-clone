"""HTTP server exposing the index: a Web UI plus Everything-compatible APIs.

Mirrors everything.exe's built-in HTTP server observed in REA evidence:
  - "&Enable HTTP server", "HTTP server online.", HTTP/1.0 replies
  - query parameters: search=, json=1, count=, offset=, sort=, ascending=,
    path=1, path_column=1, size_column=1, date_modified_column=1, csv=1,
    case=1, wholeword=1, regex=1
  - CSV export ("Filename,Size,Date Modified,Date Created,Attributes")
"""

import csv
import io
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")

SORT_MAP = {
    "name": "name", "path": "path", "size": "size",
    "date_modified": "dm", "dm": "dm", "date_created": "dc", "dc": "dc",
    "extension": "ext", "ext": "ext", "attributes": "attrib", "attrib": "attrib",
}

ATTR_CHARS = "rhsadntplcoieb"


def attrs_str(a):
    out = []
    for bit, ch in zip((0x1, 0x2, 0x4, 0x10, 0x20, 0x80, 0x100, 0x200, 0x400,
                        0x800, 0x1000, 0x2000, 0x4000, 0x8000), ATTR_CHARS):
        out.append(ch if a & bit else ".")
    return "".join(out)


def fmt_size(n):
    if n < 1024:
        return str(n)
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024.0
        if n < 1024:
            return "%.1f %s" % (n, unit)
    return "%.1f PB" % n


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            pass

        def _send(self, body, ctype, code=200, headersonly=False):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not headersonly:
                self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            qs = {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
            try:
                if u.path == "/" and "search" in qs:
                    return self.everything_http_api(qs)
                if u.path in ("/", "/index.html"):
                    with open(os.path.join(APP_DIR, "index.html"), "rb") as f:
                        return self._send(f.read(), "text/html; charset=utf-8")
                if u.path.startswith("/static/"):
                    name = os.path.basename(u.path[len("/static/"):])
                    full = os.path.join(APP_DIR, name)
                    if os.path.isfile(full):
                        ctype = "text/css" if name.endswith(".css") else \
                            "application/javascript" if name.endswith(".js") else "application/octet-stream"
                        with open(full, "rb") as f:
                            return self._send(f.read(), ctype + "; charset=utf-8")
                    return self._send("not found", "text/plain", 404)
                if u.path == "/api/search":
                    return self.api_search(qs)
                if u.path == "/api/stats":
                    return self._send(json.dumps(app.stats()), "application/json")
                if u.path == "/api/changes":
                    return self._send(json.dumps(app.recent_changes()), "application/json")
                if u.path == "/thumb":
                    size = int(qs.get("size", 96))
                    size = 32 if size <= 48 else 256 if size >= 256 else 96
                    data = app.thumbnail(unquote(qs.get("path", "")), size)
                    if data is None:
                        return self._send(b"", "image/jpeg", 404)
                    return self._send(data, "image/jpeg")
                if u.path == "/preview":
                    r = app.preview(unquote(qs.get("path", "")))
                    if r is None:
                        return self._send("not previewable", "text/plain", 404)
                    return self._send(r[1], r[0])
                return self._send("not found", "text/plain", 404)
            except BrokenPipeError:
                pass
            except Exception as exc:
                try:
                    self._send(json.dumps({"error": str(exc)}), "application/json", 500)
                except Exception:
                    pass

        # ---- clone API (used by the bundled web UI) --------------------
        def api_search(self, qs):
            q = qs.get("q", "")
            sort = qs.get("sort")
            asc = qs.get("asc", "1") not in ("0", "false")
            offset = int(qs.get("offset", 0))
            count = int(qs.get("count", 200))
            t0 = time.time()
            page, total = app.search(q, sort=sort, ascending=asc,
                                     limit=count, offset=offset)
            dt = int((time.time() - t0) * 1000)
            items = [{
                "name": e.name, "path": e.path, "is_dir": e.is_dir,
                "size": e.size, "size_h": "" if e.is_dir else fmt_size(e.size),
                "created": e.created, "modified": e.modified,
                "attrs": attrs_str(e.attrs),
            } for e in page]
            self._send(json.dumps({"total": total, "time_ms": dt, "items": items}),
                       "application/json")

        # ---- Everything HTTP-server-compatible query API -----------------
        def everything_http_api(self, qs):
            search = unquote(qs.get("search", ""))
            as_json = qs.get("json") == "1"
            as_csv = qs.get("csv") == "1"
            count = int(qs.get("count", 10000))
            offset = int(qs.get("offset", 0))
            sort = SORT_MAP.get(qs.get("sort", "name"), "name")
            ascending = qs.get("ascending", "1") not in ("0", "false")
            page, total = app.search(search, sort=sort, ascending=ascending,
                                     limit=count, offset=offset)
            rows = []
            for e in page:
                r = {"name": e.name,
                     "type": "folder" if e.is_dir else "file",
                     "size": e.size,
                     "date_modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.modified))
                     if e.modified else "",
                     "date_created": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.created))
                     if e.created else "",
                     "attributes": attrs_str(e.attrs)}
                if qs.get("path") == "1" or qs.get("path_column") == "1":
                    r["path"] = e.path
                rows.append(r)
            if as_json:
                self._send(json.dumps({"totalResults": total, "results": rows}),
                           "application/json")
            elif as_csv:
                buf = io.StringIO()
                w = csv.writer(buf, lineterminator="\n")
                cols = ["Filename", "Size", "Date Modified", "Date Created", "Attributes"]
                if qs.get("path") == "1" or qs.get("path_column") == "1":
                    cols.insert(1, "Path")
                w.writerow(cols)
                for e in page:
                    dm = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.modified)) if e.modified else ""
                    dc = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.created)) if e.created else ""
                    row = [e.name, e.size, dm, dc, attrs_str(e.attrs)]
                    if "Path" in cols:
                        row.insert(1, e.path)
                    w.writerow(row)
                self._send(buf.getvalue(), "text/csv; charset=utf-8")
            else:
                lines = ["<html><head><meta charset='utf-8'><title>search: %s</title></head><body>" % search]
                for e in page[:1000]:
                    lines.append("<div>%s\\%s</div>" % (e.path, e.name))
                lines.append("</body></html>")
                self._send("\n".join(lines), "text/html; charset=utf-8")

    return Handler


def serve(app, host="127.0.0.1", port=8888):
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    print("Everything-clone HTTP server on http://%s:%d/ (Ctrl+C to stop)" % (host, port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
