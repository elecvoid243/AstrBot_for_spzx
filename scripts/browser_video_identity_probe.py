"""Run the bounded V02 capture-identity counterexample with owned processes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from uuid import uuid4

from browser_video_probe_process import OwnedProbeProcess


def main():
    """Preserve pixel evidence and fail when a barrier cannot establish identity.

    Raises:
        AssertionError: The campaign fails unexpectedly or leaks owned processes.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--chromium", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=3, choices=range(1, 7))
    parser.add_argument("--skip-transition-wait", action="store_true")
    parser.add_argument("--frame-contract", action="store_true")
    parser.add_argument("--contract-matrix", action="store_true")
    parser.add_argument("--repair-matrix", action="store_true")
    parser.add_argument("--expect-counterexample", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    out = args.out / ("run-" + uuid4().hex[:8])
    out.mkdir()
    temporary = out.resolve() / "temp"
    temporary.mkdir()
    command = [
        str(args.python),
        "-B",
        str(
            args.plugin
            / (
                "scripts/frame_shadow_probe.py"
                if args.repair_matrix
                else "scripts/frame_contract_probe.py"
                if args.contract_matrix
                else "scripts/video_identity_probe.py"
            )
        ),
        "--out",
        str(out.resolve()),
        "--chromium",
        str(args.chromium),
        "--rounds",
        str(args.rounds),
    ]
    if args.skip_transition_wait:
        command.append("--skip-transition-wait")
    if args.frame_contract:
        command.append("--frame-contract")
    files = [
        Path(__file__),
        args.plugin / "scripts/video_capture_barrier_candidate.py",
        args.plugin / "browser_capture.py",
        args.plugin / "frame_records.py",
        args.plugin / "scripts/video_identity_probe.py",
        args.plugin / "scripts/video_identity_fixture.html",
        args.plugin / "observer.py",
        args.plugin / "frame_coordinates.py",
        args.plugin / "frame_dom.py",
    ]
    if args.contract_matrix:
        files.append(args.plugin / "scripts/frame_contract_probe.py")
    if args.repair_matrix:
        files.append(args.plugin / "scripts/frame_shadow_probe.py")
    manifest = {
        "command": command,
        "workdir": str(Path.cwd()),
        "sources_sha256": {
            str(f.resolve()): hashlib.sha256(f.read_bytes()).hexdigest() for f in files
        },
        "timeout_s": 120,
        "expected_counterexample": args.expect_counterexample,
    }
    (out / "run.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    started = time.monotonic()
    owned = OwnedProbeProcess(
        command,
        out,
        {
            **os.environ,
            "TEMP": str(temporary),
            "TMP": str(temporary),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    try:
        owned.process.wait(timeout=120)
    finally:
        cleanup = owned.close(graceful_timeout=0.25)
        (out / "cleanup.json").write_text(
            json.dumps(cleanup, indent=2), encoding="utf-8"
        )
    assert not cleanup["remaining_owned_processes"], cleanup
    assert (out / "result.json").exists(), (
        f"Fixture exited before recording results; inspect {out.resolve() / 'runtime.log'}"
    )
    result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    violations = result["violations"]
    summary = {
        "folder": str(out.resolve()),
        "runtime_exit": owned.process.returncode,
        "elapsed_s": time.monotonic() - started,
        "cases": len(result["cases"]),
        "violations": len(violations),
        "scenarios": sorted({item["scenario"] for item in violations}),
        "remaining_processes": len(cleanup["remaining_owned_processes"]),
        "candidate_passed": result.get("passed"),
    }
    (out / "outcome.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    if args.expect_counterexample:
        expected = {"cross-origin-frame-scroll"}
        if args.skip_transition_wait:
            expected.add("cross-document-transition")
        assert owned.process.returncode == 1 and result.get("passed") is False, summary
        assert summary["scenarios"] == sorted(expected), summary
    else:
        assert owned.process.returncode == 0 and result.get("passed") is True, summary


if __name__ == "__main__":
    main()
