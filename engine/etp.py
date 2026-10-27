"""ETP/FTP server: lets remote FTP clients search and download files.

Mirrors everything.exe's ETP/FTP server observed via REA strings:
  - "&Enable ETP/FTP server", "Connect to ETP Server..."
  - FTP replies: "150 Data connection accepted; transfer starting.",
    "226 Transfer complete.", "426 Connection closed; transfer aborted.",
    "451 Transfer aborted: read error."
  - LIST line format: "--r--r--r-- 1 ftp ftp <size> <date> <name>"
"""

import os
import socket
import threading
import time

from .app import App


def _ls_line(path, name):
    full = os.path.join(path, name)
    try:
        st = os.stat(full)
        isdir = os.path.isdir(full)
        size = 0 if isdir else st.st_size
        t = time.localtime(st.st_mtime)
        mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][t.tm_mon - 1]
        perms = "dr-xr-xr-x" if isdir else "-r--r--r--"
        return "%s   1 ftp  ftp  %13d %s %2d %02d:%02d %s" % (
            perms, size, mon, t.tm_mday, t.tm_hour, t.tm_min, name)
    except OSError:
        return "-r--r--r--   1 ftp  ftp  0 Jan  1 00:00 " + name


class FtpSession(threading.Thread):
    def __init__(self, cmd_sock, addr, app, root="C:\\", allow_download=True):
        super().__init__(daemon=True)
        self.sock = cmd_sock
        self.addr = addr
        self.app = app
        self.root = root
        self.allow_download = allow_download
        self.cwd = root
        self.pasv_sock = None
        self.data_addr = None
        self.quit = False

    # ---- helpers -------------------------------------------------------
    def send(self, line):
        self.sock.sendall((line + "\r\n").encode("utf-8", "replace"))

    def _open_data(self):
        if self.pasv_sock:
            conn, _ = self.pasv_sock.accept()
            return conn
        if self.data_addr:
            s = socket.create_connection(self.data_addr, timeout=10)
            return s
        return None

    def _send_list(self, lines):
        conn = self._open_data()
        if not conn:
            self.send("425 Can't open data connection.")
            return
        self.send("150 Data connection accepted; transfer starting.")
        try:
            for ln in lines:
                conn.sendall((ln + "\r\n").encode("utf-8", "replace"))
            conn.close()
            self.send("226 Transfer complete.")
        except OSError:
            self.send("426 Connection closed; transfer aborted.")
        finally:
            self._clear_data()

    def _clear_data(self):
        if self.pasv_sock:
            try:
                self.pasv_sock.close()
            except OSError:
                pass
            self.pasv_sock = None
        self.data_addr = None

    def _winpath(self, ftp_path):
        p = ftp_path.replace("/", "\\").strip()
        if not p or p == "\\":
            return self.root
        if len(p) >= 2 and p[1] == ":":
            return p
        if p.startswith("\\"):
            base = self.root.rstrip("\\")
            return base + p
        return os.path.join(self.cwd, p)

    # ---- session -------------------------------------------------------
    def run(self):
        try:
            self.sock.settimeout(300)
            self.send("220 Everything-clone ETP/FTP server ready.")
            buf = b""
            while not self.quit:
                data = self.sock.recv(4096)
                if not data:
                    break
                buf += data
                while b"\r\n" in buf:
                    line, buf = buf.split(b"\r\n", 1)
                    self.dispatch(line.decode("utf-8", "replace").strip())
        except (OSError, socket.timeout):
            pass
        finally:
            try:
                self.sock.close()
            except OSError:
                pass

    def dispatch(self, line):
        if not line:
            return
        parts = line.split(" ", 1)
        cmd = parts[0].upper()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd in ("USER",):
            self.send("331 User name okay, need password.")
        elif cmd in ("PASS",):
            self.send("230 User logged in, proceed.")
        elif cmd == "SYST":
            self.send("215 UNIX Type: L8")
        elif cmd == "QUIT":
            self.send("221 Goodbye.")
            self.quit = True
        elif cmd == "PWD":
            cwd = self.cwd.replace("\\", "/")
            self.send('257 "%s" is current directory.' % cwd)
        elif cmd == "CWD":
            p = self._winpath(arg)
            if os.path.isdir(p):
                self.cwd = p
                self.send("250 Directory changed to %s." % p)
            else:
                self.send("550 Directory not found.")
        elif cmd == "TYPE":
            self.send("200 Type set to %s." % (arg or "A"))
        elif cmd in ("MODE", "STRU"):
            self.send("200 OK.")
        elif cmd == "NOOP":
            self.send("200 NOOP ok.")
        elif cmd == "PASV":
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]
            self.pasv_sock = srv
            self.send("227 Entering Passive Mode (127,0,0,1,%d,%d)." %
                      (port // 256, port % 256))
        elif cmd == "PORT":
            nums = [int(x) for x in arg.split(",")]
            self.data_addr = (".".join(str(x) for x in nums[:4]),
                              nums[4] * 256 + nums[5])
            self.send("200 PORT command successful.")
        elif cmd in ("LIST", "NLST"):
            p = self._winpath(arg or "")
            names = []
            try:
                names = sorted(os.listdir(p))
            except OSError:
                self.send("550 Failed to list directory.")
                return
            if cmd == "NLST":
                self._send_list(names)
            else:
                self._send_list([_ls_line(p, n) for n in names])
        elif cmd == "SIZE":
            p = self._winpath(arg)
            try:
                self.send("213 %d" % os.path.getsize(p))
            except OSError:
                self.send("550 File not found.")
        elif cmd == "MDTM":
            p = self._winpath(arg)
            try:
                t = time.localtime(os.path.getmtime(p))
                self.send("213 %04d%02d%02d%02d%02d%02d" %
                          (t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec))
            except OSError:
                self.send("550 File not found.")
        elif cmd == "RETR":
            if not self.allow_download:
                self.send("451 Transfer aborted: permission denied.")
                return
            p = self._winpath(arg)
            if not os.path.isfile(p):
                self.send("550 File not found.")
                return
            conn = self._open_data()
            if not conn:
                self.send("425 Can't open data connection.")
                return
            self.send("150 Data connection accepted; transfer starting.")
            try:
                with open(p, "rb") as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        conn.sendall(chunk)
                conn.close()
                self.send("226 Transfer complete.")
            except OSError:
                self.send("451 Transfer aborted: read error.")
            finally:
                self._clear_data()
        elif cmd == "DELE" or cmd == "RMD" or cmd == "MKD" or cmd == "STOR":
            self.send("550 Permission denied.")
        else:
            self.send("502 Command not implemented.")


def serve_etp(app, host="0.0.0.0", port=8899, root="C:\\", allow_download=True):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(8)
    print("ETP/FTP server on ftp://%s:%d/ (user: any, pass: any)" % (host, port))
    while True:
        conn, addr = srv.accept()
        FtpSession(conn, addr, app, root=root, allow_download=allow_download).start()
