"""Regression coverage for the 3.3.8 correctness audit."""
def test_export_ids_do_not_collide_with_generated_suffixes(tmp_path):
    from Bio import SeqIO
    from taxondbbuilder.exports import write_interoperability_exports

    path = tmp_path / "input.fasta"
    path.write_text(">one\nACGT\n>two\nTGCA\n>three\nCCCC\n")
    rows = [dict(header=header, acc_id=acc, organism_name="Testus alpha")
            for header, acc in zip(("one", "two", "three"), ("A", "A", "A__2"))]
    result = write_interoperability_exports(path, rows, ["qiime2"])[0]
    ids = [record.id for record in SeqIO.parse(result["paths"][0], "fasta")]
    assert len(set(ids)) == 3
    assert ids == [line.split("\t")[0] for line in result["paths"][1].read_text().splitlines()[1:]]
