"""Validate partial output from a native or AppImage-bundled frozen sidecar.

All API requests terminate at local fake servers. Report/config files are
written only beneath the caller's new --output-dir.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from frozen_bold_check import local_api

VERSION = (Path(__file__).resolve().parents[1] / "VERSION").read_text().strip()


def run_case(sidecar, directory, config, env, name, source, policy=None, resume=False, post_prep=False):
    output = directory / (name + ".fasta")
    args = [str(sidecar), "build", "-c", str(config), "-t", "999", "-m", "coi",
            "--source", source, "--out", str(output), "--dump-gb", str(directory / "gb"), "--workers", "1"]
    if policy:
        args.extend(["--on-bold-failure", policy])
    if post_prep:
        args.extend(["--post-prep", "--post-prep-step", "msa_tree"])
    if resume:
        args.append("--resume")
    result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=120)
    (directory / (name + ("-resume" if resume else "") + ".stdout.txt")).write_text(result.stdout + result.stderr)
    return result, output


def check_manifest(output, completed):
    manifest = json.loads(Path(str(output) + ".manifest.json").read_text())
    assert manifest["taxondbbuilder_version"] == VERSION, manifest["taxondbbuilder_version"]
    assert manifest["sources"]["completed"] == completed
    for entry in manifest["outputs"]:
        file = Path(entry["path"])
        assert file.stat().st_size == entry["size"]
        assert hashlib.sha256(file.read_bytes()).hexdigest() == entry["sha256"]
    return manifest


def verify(sidecar, directory):
    with local_api(directory) as (proxy, env, hits, handler):
        config = directory / "db.toml"
        markers = Path(__file__).resolve().parents[1] / "configs/markers_mitogenome.toml"
        config.write_text(
            '[ncbi]\nemail = "test@example.invalid"\nrettype = "gb"\nretmode = "text"\ndelay_sec = 0\n'
            f'[bold]\nbase_url = "{proxy}/api"\nretries = 0\n'
            '[output]\ndefault_header_format = "{acc_id}|{marker}"\n'
            '[output.header_formats]\nmifish_pipeline = "{acc_id}|{marker}"\n'
            f'[markers]\nfile = {json.dumps(str(markers))}\n'
        )
        baseline, ncbi = run_case(sidecar, directory, config, env, "ncbi", "ncbi")
        assert baseline.returncode == 0, baseline.stdout + baseline.stderr
        check_manifest(ncbi, ["ncbi"])
        handler.download_status = 403
        failed, failed_path = run_case(sidecar, directory, config, env, "fail-fast", "both")
        assert failed.returncode == 2, failed.stdout + failed.stderr
        assert not failed_path.exists()
        partial, requested = run_case(sidecar, directory, config, env, "both", "both", "continue-ncbi")
        assert partial.returncode == 3, partial.stdout + partial.stderr
        output = requested.with_name(requested.stem + "__partial-nobold" + requested.suffix)
        manifest = check_manifest(output, ["ncbi"])
        assert manifest["sources"]["failed"] == [{"source": "bold", "stage": "download", "status": 403, "reason": "http_error"}]
        assert "NCBI only output:" in partial.stdout and str(output) in partial.stdout
        for suffix in ("", ".source_merge.csv", ".acc_organism.csv"):
            assert Path(str(output) + suffix).read_bytes() == Path(str(ncbi) + suffix).read_bytes()
        handler.download_status = None
        start = len(hits)
        complete, full_output = run_case(sidecar, directory, config, env, "both", "both", "continue-ncbi", resume=True)
        assert complete.returncode == 0, complete.stdout + complete.stderr
        assert full_output == requested
        check_manifest(full_output, ["ncbi", "bold"])
        assert not any("ncbi" in hit and "efetch" in hit["ncbi"] and "db=taxonomy" not in hit["ncbi"] for hit in hits[start:])
        assert "P0" in Path(str(full_output) + ".source_merge.csv").read_text()
        config.write_text(config.read_text() + '\n[post_prep]\nmsa_tree_enable = true\nmsa_tree_min_taxa = 1\nmsa_tree_model = "invalid-model"\n')
        handler.download_status = 403
        post_failed, post_output = run_case(sidecar, directory, config, env, "post-failed", "both", "continue-ncbi", post_prep=True)
        assert post_failed.returncode not in (0, 3), post_failed.stdout + post_failed.stderr
        assert "post-processing failed" in post_failed.stdout + post_failed.stderr
        assert not list(directory.glob("post-failed*.fasta*"))
        report = {"sidecar": str(sidecar), "sidecar_sha256": hashlib.sha256(sidecar.read_bytes()).hexdigest(),
                  "version": VERSION, "exit_codes": {"ncbi": baseline.returncode, "fail_fast": failed.returncode,
                  "partial": partial.returncode, "resumed_both": complete.returncode},
                  "postprep_failure_rejected": True, "ncbi_outputs_equal": True, "manifest_hashes_valid": True, "resume_cache_reused": True,
                  "partial_output": str(output), "requests": hits, "external_requests": 0}
        (directory / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sidecar", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.sidecar.resolve(), args.output_dir.resolve()), indent=2))


if __name__ == "__main__":
    main()
