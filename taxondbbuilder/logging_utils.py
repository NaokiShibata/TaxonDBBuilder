"""Run logging and console tee helpers."""

import io
import logging
import os
import re
import sys
import time
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from threading import RLock
from typing import TextIO


def setup_run_logger(log_stream: TextIO) -> logging.Logger:
    logger_name = f"taxondbbuilder.run.{os.getpid()}.{int(time.time() * 1_000_000)}"
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    handler = logging.StreamHandler(log_stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)

    return logger


def close_run_logger(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        handler.flush()
        handler.close()
        logger.removeHandler(handler)


class LockedTextStream:
    """Serialize writes to one shared text stream."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._lock = RLock()

    def write(self, data: str) -> int:
        with self._lock:
            written = self._stream.write(data)
            self._stream.flush()
            return written

    def flush(self) -> None:
        with self._lock:
            self._stream.flush()

    def isatty(self) -> bool:
        return False

    @property
    def encoding(self) -> str:
        return getattr(self._stream, "encoding", "utf-8")


class TeeStream:
    ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

    def __init__(
        self,
        primary_stream: TextIO,
        mirror_stream: TextIO | None = None,
    ) -> None:
        self._primary_stream = primary_stream
        self._mirror_stream = mirror_stream
        self._mirror_buffer = ""

    def write(self, data: str) -> int:
        self._primary_stream.write(data)

        if self._mirror_stream is not None:
            self._write_mirror(data)

        return len(data)

    def _write_mirror(self, data: str) -> None:
        if not data:
            return

        self._mirror_buffer += data

        while "\n" in self._mirror_buffer:
            line, self._mirror_buffer = self._mirror_buffer.split(
                "\n",
                1,
            )

            if "\r" in line:
                continue

            cleaned = self.ANSI_ESCAPE_RE.sub("", line)
            self._mirror_stream.write(cleaned + "\n")

    def flush(self) -> None:
        self._primary_stream.flush()

        if self._mirror_stream is None:
            return

        if self._mirror_buffer and "\r" not in self._mirror_buffer:
            cleaned = self.ANSI_ESCAPE_RE.sub(
                "",
                self._mirror_buffer,
            )
            self._mirror_stream.write(cleaned)

        self._mirror_buffer = ""
        self._mirror_stream.flush()

    def isatty(self) -> bool:
        return bool(
            getattr(
                self._primary_stream,
                "isatty",
                lambda: False,
            )()
        )

    def fileno(self) -> int:
        fileno = getattr(self._primary_stream, "fileno", None)
        if fileno is None:
            raise io.UnsupportedOperation("fileno")
        return fileno()

    @property
    def encoding(self) -> str:
        return getattr(
            self._primary_stream,
            "encoding",
            "utf-8",
        )


@contextmanager
def tee_console_output(log_path: Path):
    original_stdout = sys.stdout
    original_stderr = sys.stderr

    with log_path.open("w", encoding="utf-8") as log_file:
        shared_log_stream = LockedTextStream(log_file)

        tee_stdout = TeeStream(
            original_stdout,
            shared_log_stream,
        )
        tee_stderr = TeeStream(
            original_stderr,
            shared_log_stream,
        )

        with redirect_stdout(tee_stdout), redirect_stderr(tee_stderr):
            yield shared_log_stream
