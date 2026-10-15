"""Exercise the release sidecar using only local fake BOLD and NCBI servers.

Usage: python tools/frozen_bold_check.py <sidecar> --output-dir <new-directory>
Requires openssl to generate a temporary certificate trusted only by the child.
"""
from __future__ import annotations

import argparse
import csv
from contextlib import contextmanager
import hashlib
import http.server
import json
import os
from pathlib import Path
import ssl
import subprocess
import threading
from urllib.parse import urlsplit, parse_qs

EXPECTED = {"COI-5P": "coi", "COII": "coii", "COXIII": "coiii", "ND4": "nd4", "ND4L": "nd4l"}
TAXON_XML = b'''<?xml version="1.0"?>
<!DOCTYPE TaxaSet PUBLIC "-//NCBI//NCBI Taxon, 20060124//EN" "https://www.ncbi.nlm.nih.gov/entrez/query/DTD/taxon.dtd">
<TaxaSet><Taxon><TaxId>999</TaxId><ScientificName>Testus alpha</ScientificName></Taxon></TaxaSet>'''


def ncbi_response(request):
    parsed = urlsplit(request.split()[1])
    params = parse_qs(parsed.query)
    if parsed.path.endswith("esearch.fcgi"):
        body = b'''<?xml version="1.0"?>
<!DOCTYPE eSearchResult PUBLIC "-//NLM//DTD esearch 20060628//EN" "https://eutils.ncbi.nlm.nih.gov/eutils/dtd/20060628/esearch.dtd">
<eSearchResult><Count>2</Count><RetMax>2</RetMax><RetStart>0</RetStart><IdList><Id>1</Id><Id>2</Id></IdList><QueryKey>1</QueryKey><WebEnv>local</WebEnv></eSearchResult>'''
        return body, "text/xml"
    assert parsed.path.endswith("efetch.fcgi"), request
    if params.get("db") == ["taxonomy"]:
        return TAXON_XML, "text/xml"
    return (Path(__file__).resolve().parents[1] / "tests/fixtures/sample.gb").read_bytes(), "text/plain"


def fake_handler(tls_context, hits):
    class Handler(http.server.BaseHTTPRequestHandler):
        download_status = None
        def log_message(self, *_args):
            pass

        def do_CONNECT(self):
            # HTTPS requests must go to our fake NCBI, never an external host.
            assert self.path == "eutils.ncbi.nlm.nih.gov:443", self.path
            self.send_response(200)
            self.end_headers()
            with tls_context.wrap_socket(self.connection, server_side=True) as peer:
                with peer.makefile("rb") as reader:
                    request = reader.readline().decode().strip()
                    hits.append({"ncbi": request})
                    body, content_type = ncbi_response(request)
                    while reader.readline().strip():
                        pass
                    peer.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Type: " + content_type.encode() + b"\r\nContent-Length: "
                        + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body
                    )

        def do_GET(self):
            path = urlsplit(self.path).path
            hits.append({"bold": self.path, "user_agent": self.headers.get("User-Agent")})
            assert self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"
            if path == "/api/query/preprocessor":
                body = {"successful_terms": ["tax:species:Testus alpha"]}
            elif path == "/api/summary":
                body = {"counts": {"specimens": len(EXPECTED)}}
            elif path == "/api/query":
                body = {"query_id": "local-token"}
            elif path == "/api/documents/local-token/download":
                if self.download_status:
                    return self.respond(b"local BOLD failure", "text/plain", self.download_status)
                rows = ["marker_code\tnuc\tprocessid\tspecies"]
                rows.extend(f"{code}\tACGTACGTACGT\tP{i}\tTestus alpha" for i, code in enumerate(EXPECTED))
                return self.respond(("\n".join(rows) + "\n").encode(), "text/tab-separated-values")
            else:
                raise AssertionError(f"unexpected local request: {self.path}")
            self.respond(json.dumps(body).encode(), "application/json")

        def respond(self, body, content_type, status=200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def check_case(sidecar, output_dir, config, env, selected):
    name = "-".join(selected)
    output = output_dir / f"{name}.fasta"
    command = [str(sidecar), "build", "-c", str(config), "-t", "999", "--source", "bold", "--out", str(output)]
    for key in selected:
        command.extend(["-m", key])
    proc = subprocess.run(command, env=env, capture_output=True, text=True, timeout=120)
    (output_dir / f"{name}.stdout.txt").write_text(proc.stdout + proc.stderr)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    with Path(str(output) + ".source_merge.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    actual = {row["processid"]: row["marker_key"] for row in rows}
    expected = {f"P{i}": key for i, key in enumerate(EXPECTED.values()) if key in selected}
    assert actual == expected, (actual, expected)
    assert all(row["emitted_to_fasta"] == "true" for row in rows)
    assert sum(line.startswith(">") for line in output.read_text().splitlines()) == len(expected)
    return {"selected": selected, "markers": actual, "exit": proc.returncode}


@contextmanager
def local_api(output_dir):
    output_dir.mkdir(parents=True, exist_ok=False)
    cert, key = output_dir / "cert.pem", output_dir / "key.pem"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-keyout", str(key), "-out", str(cert), "-subj", "/CN=eutils.ncbi.nlm.nih.gov",
        "-addext", "subjectAltName=DNS:eutils.ncbi.nlm.nih.gov",
    ], check=True, capture_output=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    hits = []
    handler = fake_handler(context, hits)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    proxy = f"http://127.0.0.1:{server.server_port}"
    env = {**os.environ, "HTTPS_PROXY": proxy, "https_proxy": proxy, "HTTP_PROXY": proxy,
           "http_proxy": proxy, "ALL_PROXY": proxy, "all_proxy": proxy,
           "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
           "SSL_CERT_FILE": str(cert)}
    try:
        yield proxy, env, hits, handler
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def check_sidecar(sidecar, output_dir):
    with local_api(output_dir) as (proxy, env, hits, _handler):
        return _check_marker_cases(sidecar, output_dir, proxy, env, hits)


def _check_marker_cases(sidecar, output_dir, proxy, env, hits):
    config = output_dir / "db.toml"
    markers = Path(__file__).resolve().parents[1] / "configs/markers_mitogenome.toml"
    config.write_text(
        '[ncbi]\nemail = "test@example.invalid"\n'
        f'[bold]\nbase_url = "{proxy}/api"\nretries = 0\n'
        '[output]\ndefault_header_format = "{acc_id}|{marker}"\n'
        '[output.header_formats]\nmifish_pipeline = "{acc_id}|{marker}"\n'
        f'[markers]\nfile = {json.dumps(str(markers))}\n'
    )
    results = [check_case(sidecar, output_dir, config, env, selected)
               for selected in (list(EXPECTED.values()), ["coi"], ["nd4"])]
    user_agents = {hit["user_agent"] for hit in hits if "bold" in hit}
    assert len(hits) == 15, hits  # NCBI lookup + four BOLD stages, three runs.
    version = (Path(__file__).resolve().parents[1] / "VERSION").read_text().strip()
    assert user_agents == {f"TaxonDBBuilder/{version} (+https://github.com/NaokiShibata/TaxonDBBuilder)"}
    report = {"sidecar": str(sidecar), "sidecar_sha256": hashlib.sha256(sidecar.read_bytes()).hexdigest(),
              "cases": results, "requests": hits, "external_requests": 0}
    (output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sidecar", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check_sidecar(args.sidecar.resolve(), args.output_dir.resolve()), indent=2))


if __name__ == "__main__":
    main()
