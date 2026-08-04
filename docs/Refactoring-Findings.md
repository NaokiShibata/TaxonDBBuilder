# Refactoring Findings

Phase 0 の特性化テストで確認した、リファクタリング前の挙動を記録する。

- `load_config()` は入力 TOML の `[post_prep]` をその場で正規化し、`_primer_forward`、`_primer_reverse` などの内部キーを追加する。
- `load_config(source=BuildSource.BOTH)` は `[bold]` が無い場合でも、戻り値に空の `bold` テーブルを追加する。
- `build_header()` はテンプレートに存在しないフィールドをエラーにせず空文字列へ置換する。
- `build_region_patterns()` は `region_patterns` が指定されている場合は正規表現としてそのまま返し、フォールバック時だけフィールド指定を除去してエスケープする。
- `resolve_orientation()` は両向きの順位が同じ場合、canonical 側を返しつつ ambiguous フラグを `True` にする。
- `normalize_bold_row()` は配列や区切り文字を含む入力から最初のスカラー値を採用し、配列塩基以外の ASCII 文字を除去して配列を正規化する。
- `build_bold_canonical_record()` は marker 側に `header_format` が無い場合、`output.header_formats` や default format ではなく `bold|{acc_id}|{organism}` を使う。
- Phase 0.5 の CLI 特性化テストで、`build()` の NCBI/BOLD/both の deterministic merge、`insdcacs` strict link 抑制、post_prep の実行順序、ログ文言と sidecar/report 出力を固定した。
- `build()` の `source=both` では、NCBI と BOLD の同一 accession を持つ BOLD 行が `source_merge.csv` には残るが、FASTA には出力されず、`skip_reason=linked_by_insdcacs` になる。
- `build()` の post_prep は primer trim → length filter → duplicate report の順で実行される。primer trim 前に作られた `source_merge.csv` と、trim 後の FASTA／duplicate report が併存する。
- `apply_post_prep_primer_trim()` は vsearch が無くても endpoint の純 Python 経路で FASTA と TSV sidecar を生成する。`keep_retained_fasta=true` の場合、trim 前 FASTA が別ファイルに残る。
- `fetch_genbank()` は history fetch の HTTP 400 を ID paging に fallback し、HTTPError／RemoteDisconnected を retry する。`--resume` 相当の cache hit では efetch を呼ばず、初回 esearch だけは実行する。
