"""Piped (GUI sidecar) output must not fold long paths mid-character."""

from taxondbbuilder.console import console


def test_long_path_prints_on_one_line_when_not_a_terminal(capsys):
    # pytest capture is not a terminal, so the module console uses its wide
    # piped width; rich's non-tty default of 80 would fold this path.
    long_path = "/" + "x" * 120 + "/donko.fasta.acc_organism.csv"
    console.print(f"Run manifest: {long_path}")
    out = capsys.readouterr().out
    assert f"Run manifest: {long_path}\n" in out
