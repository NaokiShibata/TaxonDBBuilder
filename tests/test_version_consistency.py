"""Every version declaration must say the same thing.

The version is repeated across the Python package, pixi, npm, Cargo and the
Tauri bundle config. A release where one of them lags behind ships an AppImage
whose window title, sidecar and package metadata disagree.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _toml_version(path: Path) -> str:
    match = re.search(r'^version = "([^"]+)"', path.read_text(encoding="utf-8"), re.M)
    assert match, f"no version in {path}"
    return match.group(1)


def _cargo_lock_version(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(
        r'\[\[package\]\]\nname = "taxondbbuilder-gui"\nversion = "([^"]+)"', text
    )
    assert match, f"no taxondbbuilder-gui entry in {path}"
    return match.group(1)


def declared_versions() -> dict[str, str]:
    package_json = json.loads((ROOT / "tauri-gui" / "package.json").read_text(encoding="utf-8"))
    lock = json.loads(
        (ROOT / "tauri-gui" / "package-lock.json").read_text(encoding="utf-8")
    )
    tauri_conf = json.loads(
        (ROOT / "tauri-gui" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
    )
    return {
        "VERSION": (ROOT / "VERSION").read_text(encoding="utf-8").strip(),
        "pixi.toml": _toml_version(ROOT / "pixi.toml"),
        "tauri-gui/package.json": package_json["version"],
        "tauri-gui/package-lock.json (root)": lock["version"],
        "tauri-gui/package-lock.json (packages.)": lock["packages"][""]["version"],
        "tauri-gui/src-tauri/Cargo.toml": _toml_version(ROOT / "tauri-gui" / "src-tauri" / "Cargo.toml"),
        "tauri-gui/src-tauri/Cargo.lock": _cargo_lock_version(ROOT / "tauri-gui" / "src-tauri" / "Cargo.lock"),
        "tauri-gui/src-tauri/tauri.conf.json": tauri_conf["version"],
    }


def test_all_version_declarations_agree():
    found = declared_versions()
    assert len(found) == 8
    assert len(set(found.values())) == 1, found


def test_version_is_a_release_number():
    version = declared_versions()["VERSION"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), version
