"""Package an already built Dashboard with its matching AstrBot revision."""

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path


def main() -> None:
    """Write matched Dashboard/backend source ZIPs, metadata and SHA256 sidecars.

    Raises:
        FileNotFoundError: The production Dashboard build is missing.
        subprocess.CalledProcessError: Git revision discovery fails.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plugin-version", default="0.3.0")
    parser.add_argument("--plugin-revision", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if len(args.plugin_revision) != 40 or any(
        c not in "0123456789abcdef" for c in args.plugin_revision
    ):
        parser.error("--plugin-revision must be the matching 40-character Git SHA.")
    if subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=root
    ).strip():
        parser.error("Commit tracked changes before packaging the matching backend.")
    dist = root / "dashboard/dist"
    if not (dist / "index.html").is_file():
        raise FileNotFoundError("Run the Dashboard production build first.")
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    backend = args.output.with_name(args.output.stem + "-backend-source.zip")
    subprocess.run(
        ["git", "archive", "--format=zip", "--output", str(backend.resolve()), "HEAD"],
        cwd=root,
        check=True,
    )
    backend_digest = hashlib.sha256(backend.read_bytes()).hexdigest()
    backend.with_suffix(".zip.sha256").write_text(
        f"{backend_digest}  {backend.name}\n", encoding="utf-8"
    )
    version = next(
        line.split('"')[1]
        for line in (root / "astrbot/__init__.py")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.startswith("__version__")
    )
    (dist / "assets/version").write_text(version, encoding="utf-8")
    (dist / "assets/browser-observer-build.json").write_text(
        json.dumps(
            {
                "astrbot_version": version,
                "astrbot_revision": revision,
                "plugin_version": args.plugin_version,
                "plugin_revision": args.plugin_revision,
                "manual_control_version": 1,
                "keyboard_input_version": 1,
                "pointer_input_version": 1,
                "backend_source_file": backend.name,
                "backend_source_sha256": backend_digest,
                "pnpm_lock_sha256": hashlib.sha256(
                    (root / "dashboard/pnpm-lock.yaml").read_bytes()
                ).hexdigest(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(dist.rglob("*")):
            if path.is_file():
                archive.write(path, Path("dist") / path.relative_to(dist))
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    args.output.with_suffix(".zip.sha256").write_text(
        f"{digest}  {args.output.name}\n", encoding="utf-8"
    )
    print(f"{digest}  {args.output.resolve()}")


if __name__ == "__main__":
    main()
