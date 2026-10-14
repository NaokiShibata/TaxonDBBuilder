"""BOLD HTTP failure handling: retry policy, diagnostics, and log privacy.

The reported defect was `BOLD HTTP error 403: Forbidden` with no way to tell
which API stage failed, which endpoint answered, how often it was retried, or
what the response said. The 403 came from Cloudflare, so the message has to
carry enough to identify that without leaking the query token.
"""

from __future__ import annotations

import io
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

import taxondb_bold as bold


def _http_error(
    url: str, code: int, reason: str, headers: dict[str, str] | None = None, body: bytes = b""
) -> HTTPError:
    from email.message import Message

    hdrs = Message()
    for key, value in (headers or {}).items():
        hdrs[key] = value
    return HTTPError(url, code, reason, hdrs, io.BytesIO(body))


CLOUDFLARE_BODY = (
    b"<html><head><title>Attention Required! | Cloudflare</title></head>"
    b"<body><h1>Sorry, you have been blocked</h1></body></html>"
)

TOKEN = "eAFLysyzKi3KtHLy93GxcnTxNjM0M7HOSSxKTwUAcbwIQA=="


def _download_cfg(**overrides):
    cfg = {"retries": 3, "backoff_sec": 1.5}
    cfg.update(overrides)
    return bold.get_bold_runtime_config(cfg)


def test_403_is_not_retried_and_names_the_stage_endpoint_and_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A 403 is not transient: one attempt, and the details locate the failure."""
    calls = 0
    url = f"{bold.DEFAULT_BASE_URL}/documents/{TOKEN}/download?format=tsv"

    def boom(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise _http_error(url, 403, "Forbidden", {"server": "cloudflare", "cf-ray": "abc-KIX"}, CLOUDFLARE_BODY)

    sleeps: list[float] = []
    monkeypatch.setattr(bold, "urlopen", boom)
    monkeypatch.setattr(bold.time, "sleep", sleeps.append)
    dest = tmp_path / "documents.tsv"

    with pytest.raises(bold.BoldApiError, match="BOLD HTTP error 403: Forbidden") as excinfo:
        bold.download_documents_to_path(TOKEN, _download_cfg(), dest)

    assert calls == 1
    assert sleeps == []
    details = excinfo.value.details
    assert "stage=download" in details
    assert "status=403" in details
    assert "attempts=1" in details
    assert "server=cloudflare" in details
    assert "Attention Required" in details
    assert [path.name for path in tmp_path.iterdir()] == []


def test_error_details_redact_the_query_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The download URL carries the query token; it must not reach the logs."""
    url = f"{bold.DEFAULT_BASE_URL}/documents/{TOKEN}/download?format=tsv"

    def boom(*args, **kwargs):
        raise _http_error(url, 403, "Forbidden", {"server": "cloudflare"}, CLOUDFLARE_BODY)

    monkeypatch.setattr(bold, "urlopen", boom)
    monkeypatch.setattr(bold.time, "sleep", lambda _seconds: None)
    with pytest.raises(bold.BoldApiError) as excinfo:
        bold.download_documents_to_path(TOKEN, _download_cfg(retries=0, backoff_sec=0), tmp_path / "d.tsv")

    text = f"{excinfo.value} {excinfo.value.details}"
    assert TOKEN not in text
    assert TOKEN.replace("=", "%3D") not in text
    assert "redacted" in excinfo.value.details


def test_details_strip_html_and_cap_the_body_excerpt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    big = b"<html><body>" + b"x" * 5000 + b"</body></html>"

    def boom(*args, **kwargs):
        raise _http_error("https://portal.boldsystems.org/api/summary", 403, "Forbidden", {}, big)

    monkeypatch.setattr(bold, "urlopen", boom)
    monkeypatch.setattr(bold.time, "sleep", lambda _seconds: None)
    with pytest.raises(bold.BoldApiError) as excinfo:
        bold._request_json("https://portal.boldsystems.org/api/summary", _download_cfg(retries=0, backoff_sec=0), stage="summary")

    details = excinfo.value.details
    assert "stage=summary" in details
    assert "<html>" not in details
    assert len(details) < 600


def test_429_waits_for_retry_after_then_gives_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    url = f"{bold.DEFAULT_BASE_URL}/documents/{TOKEN}/download?format=tsv"

    def boom(*args, **kwargs):
        raise _http_error(url, 429, "Too Many Requests", {"Retry-After": "2"}, b"slow down")

    sleeps: list[float] = []
    monkeypatch.setattr(bold, "urlopen", boom)
    monkeypatch.setattr(bold.time, "sleep", sleeps.append)
    with pytest.raises(bold.BoldApiError, match="BOLD HTTP error 429") as excinfo:
        bold.download_documents_to_path(TOKEN, _download_cfg(retries=2, backoff_sec=1.5), tmp_path / "d.tsv")

    assert sleeps == [2.0, 2.0]
    assert "retry_after=2" in excinfo.value.details


def test_429_with_a_long_retry_after_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Waiting an hour inside a build is worse than failing with the reason."""
    calls = 0
    url = f"{bold.DEFAULT_BASE_URL}/documents/{TOKEN}/download?format=tsv"

    def boom(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise _http_error(url, 429, "Too Many Requests", {"Retry-After": "3600"}, b"slow down")

    sleeps: list[float] = []
    monkeypatch.setattr(bold, "urlopen", boom)
    monkeypatch.setattr(bold.time, "sleep", sleeps.append)
    with pytest.raises(bold.BoldApiError, match="BOLD HTTP error 429") as excinfo:
        bold.download_documents_to_path(TOKEN, _download_cfg(), tmp_path / "d.tsv")

    assert calls == 1
    assert sleeps == []
    assert "retry_after=3600" in excinfo.value.details


def test_503_is_retried_the_configured_number_of_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls = 0
    url = f"{bold.DEFAULT_BASE_URL}/documents/{TOKEN}/download?format=tsv"

    def boom(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise _http_error(url, 503, "unavailable", {}, b"maintenance")

    sleeps: list[float] = []
    monkeypatch.setattr(bold, "urlopen", boom)
    monkeypatch.setattr(bold.time, "sleep", sleeps.append)
    with pytest.raises(bold.BoldApiError, match="BOLD HTTP error 503: unavailable") as excinfo:
        bold.download_documents_to_path(TOKEN, _download_cfg(retries=2, backoff_sec=1.5), tmp_path / "d.tsv")

    assert calls == 3
    assert sleeps == [1.5, 3.0]
    assert "attempts=3" in excinfo.value.details


def test_400_and_404_are_not_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for code in (400, 401, 404):
        calls = 0

        def boom(*args, **kwargs):
            nonlocal calls
            calls += 1
            raise _http_error("https://portal.boldsystems.org/api/query", code, "bad", {}, b"nope")

        sleeps: list[float] = []
        monkeypatch.setattr(bold, "urlopen", boom)
        monkeypatch.setattr(bold.time, "sleep", sleeps.append)
        with pytest.raises(bold.BoldApiError, match=f"BOLD HTTP error {code}"):
            bold._request_json("https://portal.boldsystems.org/api/query", _download_cfg(), stage="query")
        assert calls == 1
        assert sleeps == []


def test_connection_failures_are_still_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The status policy must not swallow the transient case it exists for."""
    calls = 0

    def boom(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise URLError("connection reset")
        return _BoldResponseOK(b"marker_code\nCOI-5P\n")

    class _BoldResponseOK:
        def __init__(self, payload: bytes):
            self.payload = payload
            self.headers: dict[str, str] = {}

        def read(self, size: int = -1) -> bytes:
            payload, self.payload = self.payload, b""
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(bold, "urlopen", boom)
    monkeypatch.setattr(bold.time, "sleep", lambda _seconds: None)
    meta = bold.download_documents_to_path(
        TOKEN, _download_cfg(retries=3, backoff_sec=0), tmp_path / "d.tsv"
    )
    assert calls == 3
    assert meta["downloaded_bytes"] == len(b"marker_code\nCOI-5P\n")


def test_net_retry_keeps_its_old_behaviour_without_hooks(monkeypatch: pytest.MonkeyPatch):
    """Other callers must not need the new hooks; retry_if=None is today's code."""
    from taxondbbuilder.net_retry import retry_call

    calls = 0

    def boom():
        nonlocal calls
        calls += 1
        raise URLError("down")

    sleeps: list[float] = []
    monkeypatch.setattr(bold.time, "sleep", sleeps.append)
    with pytest.raises(RuntimeError):
        retry_call(
            boom,
            retries=2,
            backoff_sec=1.5,
            retry_on=(URLError,),
            wrap=lambda exc: RuntimeError(str(exc)),
        )
    assert calls == 3
    assert sleeps == [1.5, 3.0]


def test_retry_if_stops_a_rejected_status_immediately(monkeypatch: pytest.MonkeyPatch):
    from taxondbbuilder.net_retry import retry_call

    calls = 0

    def boom():
        nonlocal calls
        calls += 1
        raise HTTPError("https://x", 403, "Forbidden", {}, None)

    sleeps: list[float] = []
    monkeypatch.setattr(bold.time, "sleep", sleeps.append)
    with pytest.raises(RuntimeError):
        retry_call(
            boom,
            retries=3,
            backoff_sec=1.0,
            retry_on=(HTTPError,),
            wrap=lambda exc: RuntimeError(str(exc)),
            retry_if=lambda exc: False,
        )
    assert calls == 1
    assert sleeps == []


def test_run_bold_stage_logs_details_then_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The GUI shows the short message; the run log has the diagnosis."""
    import typer

    from taxondbbuilder.cli import _run_bold_stage

    logger_lines: list[str] = []

    class _Logger:
        def info(self, message: str) -> None:
            logger_lines.append(message)

    class _Ctx:
        uses_bold = True
        resolved_taxa = type("T", (), {"scientific_name": "Tachysurus ichikawai", "taxid": 1})()
        cfg: dict = {}
        bold_queries: list = []
        acc_to_seqs: dict = {}
        run_logger = _Logger()
        marker_keys: list = []
        marker_map: dict = {}
        output_cfg: dict = {}
        source: object = None
        progress: object = None
        lock: object = None
        sink: object = None
        source_merge_rows: list = []
        dump_gb = None
        resume = False

    ctx = _Ctx()
    ctx.resolved_taxa = [ctx.resolved_taxa]

    def explode(*_args, **_kwargs):
        raise bold.BoldApiError(
            "BOLD HTTP error 403: Forbidden",
            details="stage=download status=403 attempts=1 server=cloudflare",
        )

    monkeypatch.setattr("taxondbbuilder.cli.prepare_bold_query", explode)
    with tmp_path.joinpath("bold_spool.tsv").open("w", encoding="utf-8") as spool_f:
        with pytest.raises(typer.BadParameter, match="BOLD HTTP error 403: Forbidden"):
            _run_bold_stage(ctx, spool_f, tmp_path)

    assert any("stage=download" in line and "status=403" in line for line in logger_lines)
