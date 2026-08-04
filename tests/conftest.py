from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def fixture_dir() -> Path:
    return ROOT / "tests" / "fixtures"


@pytest.fixture
def golden_dir() -> Path:
    return ROOT / "tests" / "golden"


def read_golden(golden_dir: Path, name: str) -> str:
    return (golden_dir / name).read_text(encoding="utf-8")


def json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


@pytest.fixture(autouse=True)
def forbid_external_network(monkeypatch):
    """Fail loudly if a Phase 0 test accidentally reaches Entrez or BOLD."""
    import taxondbbuilder as builder
    import taxondb_bold as bold

    def no_network(*args, **kwargs):
        raise AssertionError("Phase 0 tests must not access the network")

    monkeypatch.setattr(builder.Entrez, "esearch", no_network)
    monkeypatch.setattr(builder.Entrez, "efetch", no_network)
    monkeypatch.setattr(builder.Entrez, "epost", no_network)
    monkeypatch.setattr(bold, "urlopen", no_network)
