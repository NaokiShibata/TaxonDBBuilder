"""The audit must account for every row without modifying its input."""
import json
from pathlib import Path

from tools.bold_cache_audit import compare_cache


def test_cache_comparison_preserves_content_and_mtime(tmp_path):
    cache = tmp_path / "query-test.tsv"
    cache.write_text("marker_code\tnuc\nCOII\tACGT\nND4L\tACGT\n")
    source = tmp_path / "baseline.py"
    source.write_text(
        "def resolve_marker_from_record(code, keys, markers):\n"
        "    key = {'COII': 'coi', 'ND4L': 'nd4'}[code]\n"
        "    return (key, code) if key in keys else None\n"
    )
    import tomllib
    root = Path(__file__).resolve().parents[1]
    markers = tomllib.loads((root / "configs/markers_mitogenome.toml").read_text())["markers"]
    state = cache.stat().st_mtime_ns, cache.read_bytes()
    report = compare_cache([cache], markers, source)
    assert report["changes"]["corrected"] == 2
    assert report["changes"]["newly_excluded"] == 0
    assert report["changes"]["multiple_candidates_after"] == 0
    assert report["recognized_by_key"]["after"] == {"coii": 1, "nd4l": 1}
    assert (cache.stat().st_mtime_ns, cache.read_bytes()) == state
    assert json.loads(json.dumps(report))["cache_unchanged"] is True
