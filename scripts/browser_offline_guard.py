"""Process-local acceptance guard, loaded only in disposable test interpreters.

Python audit events reject non-loopback DNS/connect/sendto and package installers.
Child Chromium uses an unreachable proxy, loopback bypass and disabled QUIC/DNS.
This is test instrumentation, not an OS firewall or a physical offline claim.
"""

import ipaddress
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

LOG = Path(os.environ["BROWSER_OFFLINE_AUDIT"])
LOG.mkdir(parents=True, exist_ok=True)


def audit(event, args):
    """Reject external socket operations and retain credential-free evidence.

    Args:
        event: Python audit event name.
        args: Native event arguments, never written verbatim.

    Raises:
        OSError: External networking or dependency installation is attempted.
    """
    if event not in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
        return
    address = args[0] if event == "socket.getaddrinfo" else args[1]
    host = address[0] if isinstance(address, tuple) else address
    if not host:
        return
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="replace")
    try:
        allowed = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        allowed = False
    with (LOG / f"{os.getpid()}.jsonl").open("a", encoding="utf-8") as output:
        output.write(
            json.dumps({"event": event, "allowed": allowed, "host": host}) + "\n"
        )
    if not allowed:
        raise OSError("Offline acceptance blocks external networking")


class GuardedPopen(subprocess.Popen):
    """Apply test-only native-browser networking constraints to child processes."""

    def __init__(self, args, *positional, **kwargs):
        """Launch a child with no package installation or direct browser egress.

        Args:
            args: Subprocess argument sequence.
            *positional: Standard Popen arguments.
            **kwargs: Standard Popen keyword arguments.

        Raises:
            OSError: An installer is requested.
        """
        command = list(args) if isinstance(args, (list, tuple)) else [str(args)]
        executable = Path(command[0]).name.lower()
        if executable in {"pip", "pip.exe", "uv", "uv.exe", "npm", "npm.cmd"} or (
            "-m" in command and any(name in command for name in ("pip", "ensurepip"))
        ):
            raise OSError("Offline acceptance blocks dependency installers")
        if executable == "chrome.exe":
            command.extend(
                [
                    "--proxy-server=socks5://127.0.0.1:9",
                    "--proxy-bypass-list=localhost;127.0.0.1;[::1]",
                    "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost",
                    "--disable-quic",
                ]
            )
        with (LOG / f"{os.getpid()}.jsonl").open("a", encoding="utf-8") as output:
            output.write(
                json.dumps(
                    {
                        "event": "process",
                        "executable": executable,
                        "browser_proxy": executable == "chrome.exe",
                    }
                )
                + "\n"
            )
        super().__init__(command, *positional, **kwargs)


sys.addaudithook(audit)
subprocess.Popen = GuardedPopen
with (LOG / f"{os.getpid()}.jsonl").open("a", encoding="utf-8") as output:
    output.write(
        json.dumps({"event": "guard_loaded", "executable": sys.executable}) + "\n"
    )
# Prove that the guard rejects a real connection attempt before service startup.
try:
    with socket.socket() as probe:
        probe.connect(("192.0.2.1", 443))
except OSError:
    pass
else:
    raise RuntimeError("Offline guard failed its connection probe")
