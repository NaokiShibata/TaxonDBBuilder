"""CLI acceptance tests against local NCBI and BOLD HTTP peers."""
import csv
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen, Request

from Bio import Entrez
import pytest
from typer.testing import CliRunner
from taxondbbuilder.cli import app

ORIGINAL_ESEARCH = Entrez.esearch
ORIGINAL_EFETCH = Entrez.efetch

ROOT = Path(__file__).resolve().parents[1]
SEARCH_XML = '''<?xml version="1.0"?>
<!DOCTYPE eSearchResult PUBLIC "-//NLM//DTD esearch 20060628//EN" "https://eutils.ncbi.nlm.nih.gov/eutils/dtd/20060628/esearch.dtd">
<eSearchResult><Count>{count}</Count><RetMax>1</RetMax><RetStart>0</RetStart><IdList><Id>1</Id></IdList><QueryKey>1</QueryKey><WebEnv>local</WebEnv></eSearchResult>'''
TAX_XML = '''<?xml version="1.0"?>
<!DOCTYPE TaxaSet PUBLIC "-//NCBI//NCBI Taxon, 20060124//EN" "https://www.ncbi.nlm.nih.gov/entrez/query/DTD/taxon.dtd">
<TaxaSet><Taxon><TaxId>{taxid}</TaxId><ScientificName>Testus {name}</ScientificName></Taxon></TaxaSet>'''


@pytest.fixture
def local_apis(tmp_path, monkeypatch):
    state = {"failure": None, "fail_taxon": "alpha", "hits": [], "ncbi_failure": False, "empty": False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_GET(self):
            parsed = urlsplit(self.path)
            params = parse_qs(parsed.query)
            state["hits"].append(self.path)
            if "esearch.fcgi" in parsed.path:
                if state["ncbi_failure"]:
                    return self.reply(b"NCBI failure", 400)
                return self.reply(SEARCH_XML.format(count=0 if state["empty"] else 1).encode(), kind="text/xml")
            if "efetch.fcgi" in parsed.path:
                if params.get("db") == ["taxonomy"]:
                    taxid = params["id"][0]
                    return self.reply(TAX_XML.format(taxid=taxid, name="alpha" if taxid == "999" else "beta").encode(), kind="text/xml")
                body = (ROOT / "tests/fixtures/sample.gb").read_bytes()
                if state.get("no_match"):
                    body = body.replace(b'/gene="12S"', b'/gene="unselected"')
                return self.reply(body, kind="text/plain")
            if state.get("fail_stage") and parsed.path.endswith(state["fail_stage"]):
                return self.reply(b"local stage failure", 403)
            query = params.get("query", [""])[0]
            taxon = "beta" if "beta" in query or "beta" in parsed.path else "alpha"
            failure = state["failure"] if taxon == state["fail_taxon"] else None
            if parsed.path.endswith("summary") and failure == "json":
                return self.reply(b"invalid JSON")
            if "/download" in parsed.path and failure:
                if failure == "json":
                    return self.reply(b"invalid JSON")
                if failure == "timeout":
                    import time
                    time.sleep(0.2)
                    return
                return self.reply(f"failure secret-token-{taxon}".encode(), int(failure))
            if parsed.path.endswith("preprocessor"):
                term = f"ids:processid:Testus {taxon}" if state.get("unresolved") else f"tax:species:Testus {taxon}"
                payload = {"successful_terms": [term]}
            elif parsed.path.endswith("summary"):
                payload = {"counts": {"specimens": 0 if state.get("zero_bold") else 1}}
            elif parsed.path.endswith("query"):
                payload = {"query_id": f"secret-token-{taxon}"}
            elif "/download" in parsed.path:
                rows = f"marker_code\tnuc\tprocessid\tspecies\tinsdcacs\n12S\tACGTACGT\tBOLD-{taxon}\tTestus {taxon}\t\n"
                if state.get("linked"):
                    rows += f"12S\tACGTACGT\tLINKED-{taxon}\tTestus {taxon}\tTEST0001.1\n"
                return self.reply(rows.encode(), kind="text/tab-separated-values")
            else:
                raise AssertionError(self.path)
            self.reply(json.dumps(payload).encode())
        def reply(self, body, status=200, kind="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    def ncbi_open(request):
        original = urlsplit(request.full_url)
        handle = urlopen(Request(base + original.path + "?" + original.query, data=request.data), timeout=2)
        if handle.headers.get_content_type() == "text/plain":
            import io
            handle = io.TextIOWrapper(handle, encoding="utf-8")
        return handle
    monkeypatch.setattr(Entrez, "_open", ncbi_open)
    monkeypatch.setattr(Entrez, "esearch", ORIGINAL_ESEARCH)
    monkeypatch.setattr(Entrez, "efetch", ORIGINAL_EFETCH)
    import taxondbbuilder.bold_api as api
    def local_open(request, **kwargs):
        assert urlsplit(request.full_url).hostname == "127.0.0.1"
        return urlopen(request, **kwargs)
    monkeypatch.setattr(api, "urlopen", local_open)
    (tmp_path / "minimal_primers.toml").write_bytes((ROOT / "tests/fixtures/minimal_primers.toml").read_bytes())
    config = tmp_path / "db.toml"
    config.write_text((ROOT / "tests/fixtures/minimal_config.toml").read_text().replace('[ncbi]\n', '[ncbi]\nrettype = "gb"\nretmode = "text"\ndelay_sec = 0\n').replace('file = "minimal_markers.toml"', f'file = "{ROOT}/tests/fixtures/minimal_markers.toml"') + f'\n[bold]\nbase_url = "{base}/api"\nretries = 1\nbackoff_sec = 0\ntimeout_sec = 0.05\n')
    def invoke(name="run", source="both", policy="continue-ncbi", taxa=("999",), extra=()):
        output = tmp_path / (name + ".fasta")
        args = ["build", "-c", str(config), "-m", "12s", "--source", source, "--out", str(output), "--workers", "1"]
        for taxid in taxa:
            args += ["-t", taxid]
        if policy is not None:
            args += ["--on-bold-failure", policy]
        return CliRunner().invoke(app, args + list(extra)), output
    state.update(invoke=invoke, config=config, tmp=tmp_path)
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def partial(output):
    return output.with_name(output.stem + "__partial-nobold" + output.suffix)


def manifest(path):
    payload = json.loads(Path(str(path) + ".manifest.json").read_text())
    for output in payload["outputs"]:
        file = Path(output["path"])
        assert file.stat().st_size == output["size"]
        assert hashlib.sha256(file.read_bytes()).hexdigest() == output["sha256"]
    return payload


def test_A_complete_both(local_apis):
    result, output = local_apis["invoke"]()
    assert result.exit_code == 0, result.output
    payload = manifest(output)
    assert payload["sources"]["completed"] == ["ncbi", "bold"]
    assert "BOLD-alpha" in Path(str(output) + ".source_merge.csv").read_text()


def test_B_default_fail_fast(local_apis):
    local_apis["failure"] = 403
    result, output = local_apis["invoke"](policy=None)
    assert result.exit_code == 2, result.output
    assert not output.exists() and not partial(output).exists()


@pytest.mark.parametrize("failure", [403, 429, 500, 502, 503, 504])
def test_C_F_transport_partial(local_apis, failure):
    local_apis["failure"] = failure
    result, output = local_apis["invoke"]()
    assert result.exit_code == 3, result.output
    output = partial(output)
    assert "secret-token-" not in Path(str(output) + ".log").read_text()
    payload = manifest(output)
    assert payload["sources"] == {"requested": ["ncbi", "bold"], "completed": ["ncbi"], "failed": [{"source": "bold", "stage": "download", "status": failure, "reason": "http_error"}]}
    with Path(str(output) + ".source_merge.csv").open() as fh:
        assert {row["source"] for row in csv.DictReader(fh)} == {"ncbi"}
    downloads = [hit for hit in local_apis["hits"] if "/download" in hit]
    assert len(downloads) == (1 if failure == 403 else 2)


def test_D_rollback_matches_ncbi_and_resume(local_apis):
    baseline, ncbi = local_apis["invoke"]("ncbi", source="ncbi", policy=None, taxa=("999", "1000"))
    assert baseline.exit_code == 0, baseline.output
    local_apis.update(failure=403, fail_taxon="beta", linked=True)
    cache = local_apis["tmp"] / "cache"
    result, output = local_apis["invoke"](taxa=("999", "1000"), extra=("--dump-gb", str(cache)))
    assert result.exit_code == 3, result.output
    for suffix in ("", ".source_merge.csv", ".acc_organism.csv"):
        assert Path(str(partial(output)) + suffix).read_bytes() == Path(str(ncbi) + suffix).read_bytes()
    assert manifest(partial(output))["queries"]["bold"] == []
    def counters(path):
        return [line for line in Path(str(path) + ".log").read_text().splitlines()
                if any(line.startswith("# " + label + ":") for label in ("total records", "matched records", "matched features", "kept records", "skipped duplicates (same accession+sequence)", "kept duplicates (same accession, different sequence)"))]
    assert counters(partial(output)) == counters(ncbi)
    assert counters(ncbi)
    local_apis.update(failure=None)
    start = len(local_apis["hits"])
    result, output = local_apis["invoke"](taxa=("999", "1000"), extra=("--dump-gb", str(cache), "--resume"))
    assert result.exit_code == 0, result.output
    assert manifest(output)["sources"]["completed"] == ["ncbi", "bold"]
    recent = local_apis["hits"][start:]
    assert not any("efetch" in hit and "taxonomy" not in hit for hit in recent)
    assert sum("/download" in hit for hit in recent) == 1


def test_E_ncbi_failure(local_apis):
    local_apis["ncbi_failure"] = True
    result, output = local_apis["invoke"]()
    assert result.exit_code not in (0, 3)
    assert not partial(output).exists()


def test_G_invalid_json_is_not_partial(local_apis):
    local_apis["failure"] = "json"
    result, output = local_apis["invoke"]()
    assert result.exit_code not in (0, 3)
    assert "JSON decode" in result.output
    assert not partial(output).exists()


@pytest.mark.parametrize("failure", [None, 403])
def test_phylogeny_failure_does_not_publish_success(local_apis, monkeypatch, failure):
    import taxondbbuilder.postprep.phylo as phylo

    config = local_apis["config"]
    config.write_text(config.read_text().replace(
        "[post_prep]\n", "[post_prep]\nmsa_tree_enable = true\nmsa_tree_min_taxa = 1\n"))
    local_apis["failure"] = failure
    monkeypatch.setattr(phylo, "run_msa", lambda records: (None, "msa_failed"))
    result, output = local_apis["invoke"](extra=("--post-prep", "--post-prep-step", "msa_tree"))
    assert result.exit_code not in (0, 3)
    assert "msa_failed" in result.output
    assert not output.exists() and not partial(output).exists()
    assert not list(output.parent.glob("*.manifest.json"))


def test_H_empty_ncbi(local_apis):
    local_apis.update(empty=True, failure=403)
    result, output = local_apis["invoke"]()
    assert result.exit_code not in (0, 3)
    assert not partial(output).exists()


def test_I_cancel_is_not_partial(local_apis, monkeypatch):
    import taxondbbuilder.cli as cli
    def cancel(*_a, **_kw):
        raise KeyboardInterrupt()
    monkeypatch.setattr(cli, "prepare_bold_query", cancel)
    result, output = local_apis["invoke"]()
    assert result.exit_code not in (0, 3)
    assert not partial(output).exists()


def test_J_preserve_existing_both(local_apis):
    result, output = local_apis["invoke"]()
    assert result.exit_code == 0, result.output
    before = {p: p.read_bytes() for p in output.parent.glob(output.name + "*")}
    local_apis["failure"] = 403
    result, _ = local_apis["invoke"]()
    assert result.exit_code == 3, result.output
    for file, content in before.items():
        assert file.read_bytes() == content
    result, _ = local_apis["invoke"]()
    assert result.exit_code not in (0, 3)  # Existing partial outputs are protected too.
    manifest(partial(output))


@pytest.mark.parametrize("source", ["ncbi", "bold"])
def test_continue_requires_both(local_apis, source):
    result, _ = local_apis["invoke"](source=source)
    assert result.exit_code == 2
    assert "requires --source both" in result.output


def test_F_timeout_is_partial_after_retries(local_apis):
    local_apis["failure"] = "timeout"
    result, output = local_apis["invoke"]()
    assert result.exit_code == 3, result.output
    failure = manifest(partial(output))["sources"]["failed"][0]
    assert failure == {"source": "bold", "stage": "download", "status": None, "reason": "timeout"}
    assert sum("/download" in hit for hit in local_apis["hits"]) == 2


@pytest.mark.parametrize("problem", ["internal", "disk", "config", "post-prep"])
def test_G_non_transport_failures_are_not_partial(local_apis, monkeypatch, problem):
    import taxondbbuilder.cli as cli
    import taxondbbuilder.bold_api as api
    def fail(*_args, **_kwargs):
        if problem == "disk":
            raise OSError("test disk failure")
        raise RuntimeError("test internal failure")
    if problem == "config":
        config = local_apis["config"]
        config.write_text(config.read_text().replace("retries = 1", "retries = -1"))
    elif problem == "disk":
        monkeypatch.setattr(api, "_stream_download_to_file", fail)
    elif problem == "internal":
        monkeypatch.setattr(cli, "process_bold_taxon_to_spool", fail)
    else:
        local_apis["failure"] = 403
        monkeypatch.setattr(cli, "_run_post_prep", fail)
    result, output = local_apis["invoke"]()
    assert result.exit_code not in (0, 3), result.output
    assert not partial(output).exists()
    assert not Path(str(partial(output)) + ".manifest.json").exists()


def test_publication_failure_removes_uncommitted_partial_files(local_apis, monkeypatch):
    import taxondbbuilder.partial_success as publication
    original = publication.os.link
    calls = []
    def fail_second(source, destination):
        calls.append(destination)
        if len(calls) == 2:
            raise OSError("simulated publication failure")
        original(source, destination)
    monkeypatch.setattr(publication.os, "link", fail_second)
    local_apis["failure"] = 403
    result, output = local_apis["invoke"]()
    assert result.exit_code not in (0, 3)
    assert calls and not any(path.exists() for path in calls)
    assert not partial(output).exists()


def test_post_prep_exports_and_hashes_use_partial_paths(local_apis):
    config = local_apis["config"]
    config.write_text(config.read_text().replace('[output]\n', '[output]\nexport_formats = ["qiime2"]\n').replace('[post_prep]\n', '[post_prep]\nsequence_length_min = 1\n'))
    local_apis["failure"] = 403
    result, output = local_apis["invoke"](extra=("--post-prep", "--post-prep-step", "length_filter"))
    assert result.exit_code == 3, result.output
    payload = manifest(partial(output))
    assert len(payload["outputs"]) > 3
    assert all("__partial-nobold" in Path(entry["path"]).name for entry in payload["outputs"])


def test_H_records_without_valid_ncbi_sequences_are_not_partial(local_apis):
    local_apis.update(no_match=True, failure=403)
    result, output = local_apis["invoke"]()
    assert result.exit_code not in (0, 3)
    assert not partial(output).exists()


def test_H_post_prep_removing_all_sequences_is_not_partial(local_apis):
    config = local_apis["config"]
    config.write_text(config.read_text().replace('[post_prep]\n', '[post_prep]\nsequence_length_min = 1000\n'))
    local_apis["failure"] = 403
    result, output = local_apis["invoke"](extra=("--post-prep", "--post-prep-step", "length_filter"))
    assert result.exit_code not in (0, 3), result.output
    assert not partial(output).exists()


@pytest.mark.parametrize("endpoint,stage", [("preprocessor", "preprocess"), ("summary", "summary"), ("query", "query")])
def test_transport_failure_retains_its_typed_stage(local_apis, endpoint, stage):
    local_apis["fail_stage"] = endpoint
    result, output = local_apis["invoke"]()
    assert result.exit_code == 3, result.output
    assert manifest(partial(output))["sources"]["failed"][0]["stage"] == stage


@pytest.mark.parametrize("state", ["unresolved", "zero_bold"])
def test_unresolved_and_zero_are_complete_not_transport_failures(local_apis, state):
    local_apis[state] = True
    result, output = local_apis["invoke"]()
    assert result.exit_code == 0, result.output
    payload = manifest(output)
    assert payload["sources"] == {"requested": ["ncbi", "bold"], "completed": ["ncbi", "bold"], "failed": []}
    assert not partial(output).exists()
    assert not any("/download" in hit for hit in local_apis["hits"])
