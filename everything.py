#!/usr/bin/env python3
"""Everything clone CLI (es.exe-style) and server launcher.

Usage:
  python everything.py -s "search terms" [-n 100] [-o 0] [--sort name|path|size|dm|dc|ext]
                       [--desc] [--csv] [-i]
  python everything.py --server [port]          # start web UI + HTTP API
  python everything.py --stats                  # index statistics

Mirrors es.exe (Everything command line) basics and the service-daemon mode
observed in everything.exe strings (-start-service, HTTP server enable).
"""

import argparse
import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.app import App, ntfs_drive_letters
from engine.server import serve, attrs_str, fmt_size


def build_app(letters):
    print("Scanning NTFS volumes (%s) ..." % ", ".join(letters), file=sys.stderr)
    last = [0.0]

    def progress(n, t):
        now = time.time()
        if now - last[0] > 5:
            last[0] = now
            print("  %d MFT records..." % n, file=sys.stderr)

    app = App(letters=letters)
    app.start(progress)
    st = app.stats()
    print("Indexed %s objects (%s files, %s folders) in %.1fs" %
          (st["objects"], st["files"], st["folders"], st["scan_seconds"]), file=sys.stderr)
    return app


def main():
    ap = argparse.ArgumentParser(description="Everything clone (REA reverse-engineered)")
    ap.add_argument("query", nargs="*", help="search terms (Everything syntax)")
    ap.add_argument("-s", "--search", dest="s", help="search string")
    ap.add_argument("-n", "--count", type=int, default=100)
    ap.add_argument("-o", "--offset", type=int, default=0)
    ap.add_argument("--sort", default="name", help="name|path|size|dm|dc|ext")
    ap.add_argument("--desc", action="store_true", help="descending sort")
    ap.add_argument("--csv", action="store_true", help="CSV output")
    ap.add_argument("-i", "--path", action="store_true", help="show full paths")
    ap.add_argument("--server", nargs="?", const=8888, type=int, metavar="PORT",
                    help="start HTTP server + web UI")
    ap.add_argument("--etp", nargs="?", const=8899, type=int, metavar="PORT",
                    help="also start the ETP/FTP server")
    ap.add_argument("--ipc", nargs="?", const=1, type=int, metavar="0/1",
                    help="start the EVERYTHING_IPC server (WM_COPYDATA + pipe)")
    ap.add_argument("--install-service", action="store_true",
                    help="register the Windows service (EverythingClone)")
    ap.add_argument("--uninstall-service", action="store_true")
    ap.add_argument("--start-service", action="store_true")
    ap.add_argument("--stop-service", action="store_true")
    ap.add_argument("--service-run", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--file-list", action="append", metavar="EFU/CSV/TXT",
                    help="index an offline file list (repeatable)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--volume", action="append", help="restrict to volume letter(s)")
    args = ap.parse_args()

    if not ctypes.windll.shell32.IsUserAnAdmin():
        print("warning: no admin rights - NTFS raw access will fail", file=sys.stderr)

    letters = args.volume or ntfs_drive_letters()
    if not letters:
        print("no NTFS volumes found", file=sys.stderr)
        return 1

    # service management / SCM entry do not scan in this process
    if args.install_service:
        from engine.service import install_service
        install_service()
        return 0
    if args.uninstall_service:
        from engine.service import uninstall_service
        uninstall_service()
        return 0
    if args.start_service:
        from engine.service import start_service
        start_service()
        return 0
    if args.stop_service:
        from engine.service import stop_service
        stop_service()
        return 0
    if args.service_run:
        from engine.service import run_service_entry
        run_service_entry()
        return 0

    app = build_app([l.rstrip(':').upper() for l in letters])

    if args.file_list:
        from engine.filelist import load_file_list
        for p in args.file_list:
            label, n = load_file_list(app, p)
            print("file list %s: %d entries indexed" % (label, n), file=sys.stderr)

    if args.ipc:
        from engine.ipc import IpcServer
        IpcServer(app, log=lambda m: print("[ipc]", m, file=sys.stderr)).start()

    if args.stats:
        import json
        print(json.dumps(app.stats(), indent=2))
        return 0

    if args.server:
        if getattr(args, "etp", None):
            import threading
            from engine.etp import serve_etp
            threading.Thread(target=serve_etp, daemon=True,
                             kwargs={"app": app, "port": args.etp}).start()
        serve(app, host=args.host, port=args.server)
        return 0

    q = args.s if args.s else " ".join(args.query)
    if not q:
        print("no query given (use -s \"terms\" or positional terms)", file=sys.stderr)
        return 1
    t0 = time.time()
    page, total = app.search(q, sort=args.sort, ascending=not args.desc,
                             limit=args.count, offset=args.offset)
    dt = (time.time() - t0) * 1000
    if args.csv:
        print("Filename,Size,Date Modified,Date Created,Attributes,Path")
        for e in page:
            dm = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.modified)) if e.modified else ""
            dc = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.created)) if e.created else ""
            print('"%s",%d,"%s","%s",%s,"%s"' % (e.name, e.size, dm, dc, attrs_str(e.attrs), e.path))
    else:
        for e in page:
            if args.path:
                print(e.path + ("\\" if e.is_dir else ""))
            else:
                print(e.name)
    print("-- %d of %d results in %.1f ms" % (len(page), total, dt), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
