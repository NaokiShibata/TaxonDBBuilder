"""BOLD isolation and no-overwrite publication for opt-in partial builds."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import os
from pathlib import Path
import tempfile

import typer

from .bold_api import BoldTransportError
from .models import BoldFailurePolicy


BOLD_STATE = ("counters", "acc_to_seqs", "dup_accessions", "source_merge_rows", "bold_queries")


def run_bold_with_policy(ctx, stage, spool, spool_dir):
    isolated = ctx
    if ctx.on_bold_failure == BoldFailurePolicy.CONTINUE_NCBI:
        isolated = replace(ctx, **{name: deepcopy(getattr(ctx, name)) for name in BOLD_STATE})
    try:
        stage(isolated, spool, spool_dir)
    except BoldTransportError as exc:
        if ctx.on_bold_failure == BoldFailurePolicy.FAIL_FAST:
            raise typer.BadParameter(str(exc)) from exc
        spool.seek(0)
        spool.truncate()
        ctx.partial_failure = exc.manifest_failure()
        ctx.out_path = ctx.out_path.with_name(ctx.out_path.stem + "__partial-nobold" + ctx.out_path.suffix)
        ctx.log_path = Path(str(ctx.out_path) + ".log")
        ctx.run_logger.info("# bold rollback: discarded=true")
        return
    if isolated is not ctx:
        for name in BOLD_STATE:
            setattr(ctx, name, getattr(isolated, name))
        ctx.sink = isolated.sink


def sources_manifest(ctx):
    requested = ["ncbi", "bold"] if ctx.source.value == "both" else [ctx.source.value]
    failed = [ctx.partial_failure] if ctx.partial_failure else []
    completed = [source for source in requested if source not in {item["source"] for item in failed}]
    return {"requested": requested, "completed": completed, "failed": failed}


def published_path(ctx, path):
    return ctx.publish_dir / path.name if ctx.publish_dir else path


def _publish_outputs(stage, destination):
    """Create new paths exclusively; publish the manifest last as the commit marker."""
    files = sorted(stage.rglob("*"), key=lambda path: (path.name.endswith(".manifest.json"), str(path)))
    pairs = [(file, destination / file.relative_to(stage)) for file in files if file.is_file()]
    for _, target in pairs:
        if target.exists() or target.is_symlink():
            raise typer.BadParameter(f"Output already exists; refusing to overwrite: {target}")
    created = []
    try:
        for source, target in pairs:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(source, target)
            created.append(target)
    except BaseException:
        for target in reversed(created):
            target.unlink()
        raise


@contextmanager
def staged_outputs(ctx):
    if ctx.on_bold_failure == BoldFailurePolicy.FAIL_FAST or ctx.dry_run:
        yield
        return
    original_output, original_log = ctx.out_path, ctx.log_path
    original_output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".taxondbbuilder-", dir=original_output.parent) as directory:
        stage = Path(directory)
        ctx.publish_dir = original_output.parent.resolve()
        ctx.out_path = stage / original_output.name
        ctx.log_path = stage / original_log.name
        try:
            yield
            initial_log = stage / original_log.name
            if initial_log != ctx.log_path:
                initial_log.rename(ctx.log_path)
            _publish_outputs(stage, ctx.publish_dir)
        finally:
            ctx.out_path = published_path(ctx, ctx.out_path)
            ctx.log_path = published_path(ctx, ctx.log_path)
