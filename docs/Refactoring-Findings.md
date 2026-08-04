# Refactoring Findings

Phase 0 の特性化テストで確認した挙動を記録する。
リファクタリング後も Python package の回帰基準として扱う。

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

## 現在の状態

### 今も残っている挙動

上記の挙動は Python package に移動した後も維持されている。
Python の pytest 32 件と golden 出力がこの状態を検証する。

- Python CLI の `build`、`list-markers`、`list-primer-sets` と既存の全オプションは維持されている。
- `taxondbbuilder.py` と `taxondb_bold.py` は互換シムとして残っている。
- GUI 固有の Rust code は Tauri command、設定 I/O、taxonomy 検索、sidecar 起動、進捗イベント、キャンセルに限定されている。
- Unix の sidecar キャンセルは process group の子孫まで終了させる。
- Windows の process tree 終了は実装済みだが、Windows 実機では検証していない。

### GUI について解消された挙動差

Phase 3a で確認した Rust 独自 build / post-prep 実装との次の差は、GUI がその実装を実行しなくなったことで GUI の出力差としては解消された。
Rust 側の処理を Python と同じ意味論へ修正したわけではない。

- `join(...)` location を持つ feature の欠落。
- accession version の欠落。
- `ORGANISM` の継続行の欠落。
- 複数行 qualifier の途中切断。
- `[PDAT]`、`[MDAT]`、`[All Fields]` filter 設定の無視。
- BOLD / NCBI strict merge における accession version の不一致。
- `source_merge.csv` の列順、列数、引用方式の不一致。
- duplicate report の sequence hash の不一致。
- FASTA / sidecar の record order と FASTA wrapping の不一致。
- primer match の tie-break、`rounds_run`、BOLD fallback identity、header format、retry / timeout の不一致。

GUI の build と post-prep は PyInstaller sidecar 経由で Python package を実行するため、これらの処理の source of truth は CLI と共通である。

### 未検証の範囲

- BOLD / NCBI に接続する実ネットワークの取得結果、HTTP 障害時の実 retry 回数、rate limit 下の動作。
- GUI の手動起動と画面上の進捗表示。
- Windows 実機での process tree cancellation。
- PyInstaller sidecar の Windows / macOS 実機ビルド。
