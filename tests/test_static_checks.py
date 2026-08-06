"""Static checks that guard against packaging regressions.

The package split introduced a defect where ``taxondbbuilder/bold.py`` imported
``build_bold_download_description`` from ``.console`` and then redefined it
locally without importing ``format_byte_count``. The shadowing definition raised
``NameError`` only once a BOLD download reported progress, so the whole test
suite stayed green while the sidecar crashed mid-run.

pyflakes reports both halves of that defect (the redefinition and the undefined
name), so run it over the package to catch the entire class.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

TARGETS = [
    "taxondbbuilder",
    "taxondbbuilder.py",
    "taxondb_bold.py",
    "tools",
    "tests",
]

# Unused imports are a style issue, not a defect. Only undefined names and
# shadowed definitions indicate a broken module.
#
# "unable to detect undefined names" is pyflakes reporting that a star import
# blinded it -- it contains the substring "undefined name" but is not a finding,
# so it must be excluded explicitly. See test_star_imports_are_not_expanded for
# the coverage gap those star imports leave behind.
FATAL_MARKERS = (
    "undefined name",
    "redefinition of unused",
)
IGNORED_MARKERS = ("unable to detect undefined names",)


def _run_pyflakes() -> str:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pyflakes", *TARGETS],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:  # pragma: no cover - depends on the environment
        pytest.skip("pyflakes is not installed")
    if "No module named" in result.stderr:
        pytest.skip("pyflakes is not installed")
    return result.stdout


def test_package_has_no_undefined_or_shadowed_names() -> None:
    findings = [
        line
        for line in _run_pyflakes().splitlines()
        if any(marker in line for marker in FATAL_MARKERS)
        and not any(marker in line for marker in IGNORED_MARKERS)
    ]
    assert not findings, "pyflakes reported fatal findings:\n" + "\n".join(findings)


def test_star_imports_do_not_grow() -> None:
    """Star imports blind pyflakes, so keep them from spreading.

    ``cli.py`` and ``__init__.py`` re-export via ``from .module import *``,
    which is why the ``format_byte_count`` defect was invisible to static
    analysis inside those files. Converting them to explicit imports is a
    follow-up; this test at least stops new modules from opting out.
    """
    blinded = {
        line.split(":", 1)[0]
        for line in _run_pyflakes().splitlines()
        if "unable to detect undefined names" in line
    }
    allowed = {
        "taxondbbuilder/__init__.py",
        "taxondbbuilder/cli.py",
        "taxondbbuilder.py",
    }
    assert blinded <= allowed, (
        "new modules use star imports and are excluded from static analysis: "
        f"{sorted(blinded - allowed)}"
    )
