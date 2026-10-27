"""Windows service mode (pywin32), registerable with `sc create` / net start.

Mirrors everything.exe's service behavior observed via REA evidence:
  - ADVAPI32 imports: CreateServiceW, StartServiceCtrlDispatcherW,
    RegisterServiceCtrlHandlerW, SetServiceStatus, ControlService
  - CLI strings: -install-service, -uninstall-service, -start-service,
    -stop-service, "-install-service-pipe-name <name>"

The service runs the whole engine (MFT scan + USN monitors) and serves IPC:
  * WM_COPYDATA window "EVERYTHING" (SDK clients)
  * named pipe \\\\.\\pipe\\EverythingClone (JSON line protocol)

Management (as Administrator):
  python everything.py --install-service [PORT]   -> sc create EverythingClone ...
  python everything.py --start-service            -> sc start EverythingClone
  python everything.py --stop-service             -> sc stop  EverythingClone
  python everything.py --uninstall-service        -> sc delete EverythingClone
  python everything.py --service-run              -> SCM entry point
"""

import os
import socket
import subprocess
import sys
import threading
import time

import servicemanager
import win32event
import win32service
import win32serviceutil

SVC_NAME = "EverythingClone"
SVC_DISPLAY = "Everything Clone (REA reverse-engineered)"
SVC_DESC = ("Filename index service cloned from Everything 1.4.1 via REA "
            "reverse engineering: NTFS MFT scan, USN live updates, IPC pipe "
            "and WM_COPYDATA query server.")

DEFAULT_PIPE = r"\\.\pipe\EverythingClone"


class EverythingCloneService(win32serviceutil.ServiceFramework):
    _svc_name_ = SVC_NAME
    _svc_display_name_ = SVC_DISPLAY
    _svc_description_ = SVC_DESC
    _svc_deps_ = []

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.hWaitStop = self.stop_event

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)

    def SvcRun(self):
        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                              servicemanager.PYS_SERVICE_STARTED, (self._svc_name_, ""))
        self.ReportServiceStatus(win32service.SERVICE_RUNNING)
        try:
            self._work()
        except Exception as exc:
            servicemanager.LogErrorMsg("EverythingClone service crashed: %r" % exc)
        win32event.WaitForSingleObject(self.stop_event, 0)

    def _work(self):
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from engine.app import App
        from engine.ipc import IpcServer

        state = {"ready": False}

        def log(msg):
            try:
                servicemanager.LogInfoMsg("EverythingClone: %s" % msg)
            except Exception:
                pass

        app = App()
        app.start()
        state["ready"] = True
        log("index ready: %d objects" % app.stats()["objects"])

        ipc = IpcServer(app, pipe_name=DEFAULT_PIPE, log=log).start()
        log("ipc started (pipe %s)" % DEFAULT_PIPE)

        # keep the service alive until SCM asks us to stop
        while win32event.WaitForSingleObject(self.stop_event, 3000) == win32event.WAIT_TIMEOUT:
            if not any(m.is_alive() for m in app.monitors) and app.monitors:
                log("monitors died; restarting scan")
                app = App()
                app.start()
        ipc.stop()


def run_service_entry():
    """SCM dispatcher entry: `python everything.py --service-run`."""
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(EverythingCloneService)
    servicemanager.StartServiceCtrlDispatcher()


def _sc(*args):
    r = subprocess.run(["sc"] + list(args), capture_output=True, text=True)
    return (r.stdout or "") + (r.stderr or "")


def install_service():
    script = os.path.abspath(os.path.join(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))), "everything.py"))
    python = sys.executable
    binpath = '"%s" "%s" --service-run' % (python, script)
    out = _sc("create", SVC_NAME, "binpath=", binpath, "start=", "demand",
              "DisplayName=", SVC_DISPLAY)
    _sc("description", SVC_NAME, SVC_DESC)
    print(out.strip())
    print("Service installed. Start it with:  python everything.py --start-service")
    print("IPC pipe will be: %s  (plus WM_COPYDATA window EVERYTHING)" % DEFAULT_PIPE)


def start_service():
    print(_sc("start", SVC_NAME).strip())


def stop_service():
    print(_sc("stop", SVC_NAME).strip())


def uninstall_service():
    print(_sc("stop", SVC_NAME).strip())
    time.sleep(2)
    print(_sc("delete", SVC_NAME).strip())
