"""Exercise the release sidecar using only local fake BOLD and NCBI servers.

Usage: python tools/frozen_bold_check.py <sidecar> --output-dir <new-directory>
Requires openssl to generate a temporary certificate trusted only by the child.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import http.server
import json
import os
from pathlib import Path
import ssl
import subprocess
import threading
from urllib.parse import urlsplit

EXPECTED = {"COI-5P": "coi", "COII": "coii", "COXIII": "coiii", "ND4": "nd4", "ND4L": "nd4l"}
TAXON_XML = b'''<?xml version="1.0"?>
<!DOCTYPE TaxaSet PUBLIC "-//NCBI//NCBI Taxon, 20060124//EN" "https://www.ncbi.nlm.nih.gov/entrez/query/DTD/taxon.dtd">
<TaxaSet><Taxon><TaxId>999</TaxId><ScientificName>Testus alpha</ScientificName></Taxon></TaxaSet>'''


def fake_handler(tls_context, hits):
    class Handler(http.server.BaseHTTPRequestHandler):
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
                    assert "/efetch.fcgi?" in request, request
                    while reader.readline().strip():
                        pass
                    peer.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Type: text/xml\r\nContent-Length: "
                        + str(len(TAXON_XML)).encode() + b"\r\nConnection: close\r\n\r\n" + TAXON_XML
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
                rows = ["marker_code\tnuc\tprocessid\tspecies"]
                rows.extend(f"{code}\tACGTACGTACGT\tP{i}\tTestus alpha" for i, code in enumerate(EXPECTED))
                return self.respond(("\n".join(rows) + "\n").encode(), "text/tab-separated-values")
            else:
                raise AssertionError(f"unexpected local request: {self.path}")
            self.respond(json.dumps(body).encode(), "application/json")

        def respond(self, body, content_type):
            self.send_response(200)
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


def check_sidecar(sidecar, output_dir):
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
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), fake_handler(context, hits))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    proxy = f"http://127.0.0.1:{server.server_port}"
    env = {**os.environ, "HTTPS_PROXY": proxy, "https_proxy": proxy, "HTTP_PROXY": proxy,
           "http_proxy": proxy, "ALL_PROXY": proxy, "all_proxy": proxy,
           "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
           "SSL_CERT_FILE": str(cert)}
    config = output_dir / "db.toml"
    markers = Path(__file__).resolve().parents[1] / "configs/markers_mitogenome.toml"
    config.write_text(
        '[ncbi]\nemail = "test@example.invalid"\n'
        f'[bold]\nbase_url = "{proxy}/api"\nretries = 0\n'
        '[output]\ndefault_header_format = "{acc_id}|{marker}"\n'
        '[output.header_formats]\nmifish_pipeline = "{acc_id}|{marker}"\n'
        f'[markers]\nfile = {json.dumps(str(markers))}\n'
    )
    try:
        results = [check_case(sidecar, output_dir, config, env, selected)
                   for selected in (list(EXPECTED.values()), ["coi"], ["nd4"])]
        user_agents = {hit["user_agent"] for hit in hits if "bold" in hit}
        assert len(hits) == 15, hits  # NCBI lookup + four BOLD stages, three runs.
        assert user_agents == {"TaxonDBBuilder/3.3.6 (+https://github.com/NaokiShibata/TaxonDBBuilder)"}
        report = {"sidecar": str(sidecar), "sidecar_sha256": hashlib.sha256(sidecar.read_bytes()).hexdigest(),
                  "cases": results, "requests": hits, "external_requests": 0}
        (output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        return report
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sidecar", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check_sidecar(args.sidecar.resolve(), args.output_dir.resolve()), indent=2))


if __name__ == "__main__":
    main()
