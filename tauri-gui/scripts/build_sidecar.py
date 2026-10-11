#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

def detect_target_triple(repo_root: Path) -> str:
    out = subprocess.check_output(["rustc", "-vV"], cwd=str(repo_root), text=True)
    for line in out.splitlines():
        if line.startswith("host:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError("failed to detect rust target triple from `rustc -vV`")


def sidecar_fingerprint(repo_root: Path, target_triple: str) -> str:
    inputs = [
        repo_root / "taxondbbuilder.py",
        repo_root / "taxondb_bold.py",
        repo_root / "taxondbbuilder.spec",
        repo_root / "VERSION",
        repo_root / "pixi.toml",
        repo_root / "pixi.lock",
        Path(__file__).resolve(),
        *sorted((repo_root / "taxondbbuilder").rglob("*.py")),
    ]
    digest = hashlib.sha256(f"{sys.version}|{target_triple}".encode())
    for path in inputs:
        digest.update(str(path.relative_to(repo_root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build TaxonDBBuilder sidecar with PyInstaller"
    )
    parser.add_argument("--repo-root", default="..", help="Repository root path")
    parser.add_argument(
        "--tauri-root", default=".", help="tauri-gui root path (contains src-tauri/)"
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    tauri_root = Path(args.tauri_root).resolve()
    script_path = repo_root / "taxondbbuilder.py"
    spec_path = repo_root / "taxondbbuilder.spec"

    if not script_path.exists():
        raise FileNotFoundError(f"taxondbbuilder.py not found: {script_path}")
    if not spec_path.exists():
        raise FileNotFoundError(f"taxondbbuilder.spec not found: {spec_path}")

    target_triple = detect_target_triple(repo_root)
    bin_name = "taxondbbuilder"
    bundled_name = f"taxondbbuilder-{target_triple}"
    target_dir = tauri_root / "src-tauri" / "bin"
    target_dir.mkdir(parents=True, exist_ok=True)
    dest_bin = target_dir / bundled_name

    fingerprint = sidecar_fingerprint(repo_root, target_triple)
    fingerprint_path = target_dir / f".{bundled_name}.sha256"
    if dest_bin.is_file() and fingerprint_path.is_file():
        if fingerprint_path.read_text(encoding="ascii").strip() == fingerprint:
            print(f"sidecar unchanged, reusing: {dest_bin}")
            return 0

    try:
        subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--version"],
            cwd=str(repo_root),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "PyInstaller is not available. Install it first."
        ) from exc

    cmd = [sys.executable, "-m", "PyInstaller", str(spec_path)]
    print("+", " ".join(cmd))
    subprocess.run(cmd, cwd=str(repo_root), check=True)

    dist_bin = repo_root / "dist" / bin_name
    if not dist_bin.exists():
        raise FileNotFoundError(f"built sidecar not found: {dist_bin}")

    shutil.copy2(dist_bin, dest_bin)
    fingerprint_path.write_text(fingerprint + "\n", encoding="ascii")
    print(f"built sidecar: {dest_bin}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
