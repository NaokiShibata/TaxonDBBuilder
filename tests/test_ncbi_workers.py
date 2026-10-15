"""Regression coverage for the 3.3.8 correctness audit."""
from threading import enumerate as threads
from types import SimpleNamespace
import pytest

@pytest.mark.parametrize("failure", [TimeoutError, KeyboardInterrupt])
def test_ncbi_workers_exit_when_fetch_iterator_fails(failure):
    from taxondbbuilder.cli import _process_ncbi_chunks

    before = set(threads())
    def failed_fetch():
        raise failure("fetch failed")
        yield
    with pytest.raises(failure, match="fetch failed"):
        _process_ncbi_chunks(SimpleNamespace(workers=2), "999", None, failed_fetch(), None, 0)
    assert not [thread for thread in threads() if thread not in before and thread.is_alive()]
