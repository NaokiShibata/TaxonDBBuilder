"""Every version declaration must say the same thing.

The version is repeated across the Python package, pixi, npm, Cargo and the
Tauri bundle config. A release where one of them lags behind ships an AppImage
whose window title, sidecar and package metadata disagree.
"""

from __future__ import annotations

import json
import re
import sys
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


def test_the_bold_user_agent_carries_the_running_version():
    """A server's access log must be able to tell which build called it.

    It said TaxonDBBuilder/0.1 for every release since 3.3.0, which is not a
    version anyone can act on.
    """
    from taxondbbuilder.bold_api import DEFAULT_USER_AGENT
    from taxondbbuilder.manifest import app_version

    version = declared_versions()["VERSION"]
    assert app_version() == version
    assert DEFAULT_USER_AGENT == (
        f"TaxonDBBuilder/{version} (+https://github.com/NaokiShibata/TaxonDBBuilder)"
    )


def test_app_version_prefers_the_checkout_and_falls_back_to_the_bundle(
    tmp_path, monkeypatch
):
    """A checkout reads VERSION next to the package; a frozen sidecar reads the
    copy taxondbbuilder.spec bundled into _MEIPASS."""
    from pathlib import Path

    from taxondbbuilder import manifest

    (tmp_path / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert manifest.app_version() == (ROOT / "VERSION").read_text(encoding="utf-8").strip()

    # Frozen: no checkout beside the module, only the bundle.
    class _FrozenPath(Path):
        def resolve(self):
            return tmp_path / "nonexistent" / "bold_api.py"

    monkeypatch.setattr(manifest, "Path", _FrozenPath)
    assert manifest.app_version() == "9.9.9"


def test_app_version_is_unknown_without_any_file(tmp_path, monkeypatch):
    """No VERSION file anywhere: 'unknown', never a stale hard-coded number."""
    from taxondbbuilder import manifest

    class _MissingPath(Path):
        def resolve(self):
            return tmp_path / "nonexistent" / "bold_api.py"

    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(manifest, "Path", _MissingPath)
    assert manifest.app_version() == "unknown"
