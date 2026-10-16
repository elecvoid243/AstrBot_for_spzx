"""Persisted source notifications and human todos survive process termination."""

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.mark.parametrize("boundary", ["before_notices", "after_wait_notice"])
def test_chat_delivery_and_existing_todo_resume_after_process_death(tmp_path, boundary):
    """Kill the worker, rebuild it and observe only the missing external notices.

    Args:
        tmp_path: Isolated persistent files, separate from the external ledger.
        boundary: Before delivery or after accepted/waiting receipts are saved.
    """
    messages = []

    class Ledger(BaseHTTPRequestHandler):
        """External message sink owned by the parent process."""

        def log_message(self, format, *args):
            pass

        def do_POST(self):
            """Retain actual platform output independently of worker state.

            Returns:
                None after acknowledging a complete received message.
            """
            messages.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            self.send_response(204)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Ledger)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = Path(__file__).resolve().parents[2]
    command = [
        sys.executable,
        "-m",
        "tests.agent_teams.chat_delivery_worker",
        str(tmp_path),
        f"http://127.0.0.1:{server.server_port}",
    ]
    environment = {
        **os.environ,
        "PYTHONPATH": str(root),
        "ASTRBOT_ROOT": str(tmp_path / "runtime"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    worker = None
    try:
        with (tmp_path / "start.log").open("w", encoding="utf-8") as output:
            worker = subprocess.Popen(
                [*command, "start", boundary],
                cwd=root,
                env=environment,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
            for _ in range(600):
                if (tmp_path / "ready.json").exists() or worker.poll() is not None:
                    break
                time.sleep(0.05)
            assert (tmp_path / "ready.json").exists(), (
                tmp_path / "start.log"
            ).read_text(encoding="utf-8")
            worker.kill()
            assert worker.wait(timeout=15) != 0
        resumed = subprocess.run(
            [*command, "resume", boundary],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=45,
        )
        (tmp_path / "resume.log").write_text(
            resumed.stdout + resumed.stderr, encoding="utf-8"
        )
        assert resumed.returncode == 0, resumed.stdout + resumed.stderr
        projection = json.loads((tmp_path / "resumed.json").read_text(encoding="utf-8"))
        assert projection["result"] == {"tasks": {"question": {"text": "Asia"}}}
        assert len(messages) == 3
        assert "Accepted" in messages[0]["text"]
        assert "needs human input" in messages[1]["text"]
        assert "Asia" in messages[2]["text"]
        assert all(m["caller"]["source"] == "qq:FriendMessage:12345" for m in messages)
    finally:
        if worker is not None and worker.poll() is None:
            worker.kill()
            worker.wait(timeout=15)
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
