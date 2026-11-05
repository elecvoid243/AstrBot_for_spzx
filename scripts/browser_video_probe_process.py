"""Own a Windows probe process tree independently of readiness and parent exit."""

from __future__ import annotations

import contextlib
import ctypes
import os
import subprocess
import threading
import time
from ctypes import wintypes
from pathlib import Path

import psutil


class OwnedProbeProcess:
    """Use a private kill-on-close Job Object, with PID/create-time evidence."""

    def __init__(self, command, folder: Path, env):
        """Assign the runtime before opening its browser startup gate.

        Args:
            command: Disposable runtime command, supporting --start-gate.
            folder: Isolated case directory containing logs and the gate.
            env: Child-only environment, including isolated temp directories.

        Raises:
            OSError: Windows cannot create or assign the ownership Job Object.
        """
        if os.name != "nt":
            raise OSError("This ownership probe requires Windows Job Objects.")
        gate = folder.resolve() / "start-gate"
        if gate.exists():
            raise FileExistsError("Use a new case directory for the startup gate.")
        api = self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        api.CreateJobObjectW.restype = wintypes.HANDLE
        api.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        api.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        api.CloseHandle.argtypes = [wintypes.HANDLE]

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_longlong),
                ("job_time", ctypes.c_longlong),
                ("flags", wintypes.DWORD),
                ("minimum_ws", ctypes.c_size_t),
                ("maximum_ws", ctypes.c_size_t),
                ("active_processes", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("basic", BasicLimits),
                ("io_counters", ctypes.c_ulonglong * 6),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t),
                ("peak_job_memory", ctypes.c_size_t),
            ]

        self.job = api.CreateJobObjectW(None, None)
        if not self.job:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not api.SetInformationJobObject(
            self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            api.CloseHandle(self.job)
            raise ctypes.WinError(ctypes.get_last_error())
        self.known = {}
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.samples = []
        self.record = None
        self.folder = folder
        try:
            with (folder / "runtime.log").open("w", encoding="utf-8") as log:
                self.process = subprocess.Popen(
                    [*command, "--start-gate", str(gate)],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    env=env,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
        except BaseException:
            api.CloseHandle(self.job)
            raise
        if not api.AssignProcessToJobObject(self.job, int(self.process._handle)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.process.terminate()
            self.process.wait(timeout=10)
            api.CloseHandle(self.job)
            raise error
        self.thread = threading.Thread(target=self._track, daemon=True)
        try:
            self.thread.start()
            gate.write_text(
                "Job ownership established before browser startup.", encoding="ascii"
            )
        except BaseException:
            self.stop.set()
            if self.thread.is_alive():
                self.thread.join(timeout=2)
            api.TerminateJobObject(self.job, 88)
            api.CloseHandle(self.job)
            self.process.wait(timeout=10)
            raise

    def _job_pids(self):
        """Enumerate this exact job, including children orphaned by runtime exit.

        Returns:
            Process IDs currently assigned to this private Job Object.

        Raises:
            OSError: The bounded job membership query fails.
        """

        class ProcessList(ctypes.Structure):
            _fields_ = [
                ("assigned", wintypes.DWORD),
                ("count", wintypes.DWORD),
                ("ids", ctypes.c_size_t * 1024),
            ]

        members = ProcessList()
        if not self.api.QueryInformationJobObject(
            self.job, 3, ctypes.byref(members), ctypes.sizeof(members), None
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return list(members.ids[: members.count])

    def _track(self):
        """Track members from launch, before ready.json or any viewer exists."""
        known = {}
        next_sample = 0
        while not self.stop.is_set():
            rows = []
            for pid in self._job_pids():
                with contextlib.suppress(psutil.Error):
                    proc = psutil.Process(pid)
                    identity = (pid, proc.create_time())
                    current = known.setdefault(identity, proc)
                    record = {
                        "pid": pid,
                        "create_time": identity[1],
                        "ppid": proc.ppid(),
                        "name": proc.name(),
                    }
                    with self.lock:
                        self.known[identity] = record
                    if time.monotonic() >= next_sample:
                        rows.append(
                            {
                                **record,
                                "cpu": current.cpu_percent(),
                                "rss": current.memory_info().rss,
                            }
                        )
            if time.monotonic() >= next_sample:
                self.samples.append({"epoch_ms": time.time() * 1000, "processes": rows})
                next_sample = time.monotonic() + 1
            self.stop.wait(0.05)

    def close(self, graceful_timeout: float = 20):
        """Bound shutdown and kill only this job's remaining owned processes.

        Args:
            graceful_timeout: Seconds to allow the runtime to finish cleanup.

        Returns:
            Cleanup evidence with PID/create-time identities and surviving members.
        """
        if self.record is not None:
            return self.record
        timed_out = False
        try:
            self.process.wait(timeout=graceful_timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        self.stop.set()
        self.thread.join(timeout=2)
        members = self._job_pids()
        # Query the job after runtime exit as well: descendants can outlive it.
        for pid in members:
            with contextlib.suppress(psutil.Error):
                proc = psutil.Process(pid)
                self.known[(pid, proc.create_time())] = {
                    "pid": pid,
                    "create_time": proc.create_time(),
                    "ppid": proc.ppid(),
                    "name": proc.name(),
                }
        if members:
            if not self.api.TerminateJobObject(self.job, 88):
                self.api.CloseHandle(self.job)
                raise ctypes.WinError(ctypes.get_last_error())
        self.api.CloseHandle(self.job)
        self.process.wait(timeout=10)
        deadline = time.monotonic() + 5
        while True:
            remaining = []
            for (pid, created), record in self.known.items():
                with contextlib.suppress(psutil.Error):
                    proc = psutil.Process(pid)
                    if proc.is_running() and proc.create_time() == created:
                        remaining.append(record)
            if not remaining or time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        self.record = {
            "ownership": "Private Windows Job Object; kill-on-close; startup gate",
            "runtime_exit": self.process.returncode,
            "graceful_timeout": timed_out,
            "forced_job_termination": bool(members),
            "known_processes": list(self.known.values()),
            "remaining_owned_processes": remaining,
        }
        return self.record
