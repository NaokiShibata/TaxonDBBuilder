# Python / Rust 実装 drift 調査

調査日: 2026-08-04

## 1. 要約

GUI の build は Python sidecar ではなく、`main.rs:1797` から `taxondb_runner::run_build` を直接呼び出している。
`tauri-gui/src-tauri/tauri.conf.json` にも `externalBin` はない。
したがって、Python CLI と Rust GUI は BOLD、Entrez、GenBank パース、marker 解決、header 生成、FASTA/CSV 出力、strict merge、post-prep を独立に実装している。

今回の調査では、単なる実装差ではなく、同一入力に対してレコード識別子、抽出対象、qualifier、query、sidecar schema、hash、post-prep 結果が変わり得る差を確認した。
特に GenBank パーサと sidecar/strict merge は、GUI と CLI の結果を同一 DB として扱えなくする可能性が高い。

最も重い問題は次のとおりである。

1. 手書き Rust GenBank パーサが `join(...)` を処理せず、qualifier の継続行を落とし、`ORGANISM` の継続行と accession version も失う。
2. Python が生成する NCBI query のうち、publication/modification date と all-fields include/exclude が Rust query では無視される。
3. Rust の `source_merge.csv` は Python と列順・列数が異なり、Rust は全フィールドを常に引用する。
4. strict merge のキーとなる accession が、Python の `TEST0001.1` と Rust の `TEST0001` のように異なるため、version 付き accession でリンク結果が変わる。
5. duplicate report の sequence hash が Python の SHA-1 と Rust の `DefaultHasher` で異なる。

この状態で Rust をモジュール分割するだけの (a) は、問題を見えなくしたまま二重実装を固定する。
(b) は当面の検出策として有効だが、差分の多くは既に実害があり、テストだけでは二重保守コストを解消しない。
最終方針としては (c) を推奨する。
ただし、sidecar 一本化の前に、下記の実測ケースを differential test として固定し、GUI の進捗・キャンセル・post-prep のイベント契約を sidecar に移す段階を設けるべきである。

## 2. 調査方法と実測範囲

コード読解の対象は計画書の対応表にある Python/Rust 実装一式である。
外部ネットワークには接続せず、次の比較を行った。

- `tests/fixtures/sample.gb` を Biopython `SeqIO.parse` と Rust の `split_genbank_records` / `parse_genbank_record` に通した。
- 複合 location (`join`, `complement(join)`, remote location)、継続 qualifier、`/translation` を含む合成 GenBank を両実装に通した。
- 同一 JSON row を Python の `normalize_bold_row` と Rust の `normalize_bold_row` に通した。
- Rust 比較用のテストは一時的に `taxondb_runner.rs` に追加して実行後に削除した。
  最終的な Rust/Python/テストの差分は残していない。
- Rust の既存テスト 4 件は green だった。
- Python の既存テストは `.venv/bin/python -m pytest -q` で 32 passed だった。

実ネットワークを必要とする BOLD/Entrez の実行、HTTP 障害時の retry 回数、GUI の実プロセス cancellation は未検証である。
それらはコード読解に基づく判定として明記した。

## 3. 乖離点一覧

深刻度順に記載する。
「実測」は同一入力を両実装に通した結果、「コード読解」は実装の静的比較を意味する。

### 3.1 GenBank の accession version と ORGANISM 継続行

- 分類: **実害あり**
- Python の挙動: Biopython の `record.id` は `VERSION` を反映し、fixture では `TEST0001.1` になる。
  `record.annotations["organism"]` も継続行を含み、`Testus alpha other.` になる。
- Rust の挙動: 手書きパーサは `ACCESSION` 行だけを読み、`TEST0001` とする。
  `  ORGANISM  ` の一行だけを読み、継続行 `other.` を落として `Testus alpha` とする。
- 根拠: Python `taxondbbuilder/ncbi.py:45-57, 168-170`、Rust `tauri-gui/src-tauri/src/taxondb_runner.rs:1729-1839`。
  `sample.gb` の実測値は Python が `TEST0001.1 / Testus alpha other.`、Rust が `TEST0001 / Testus alpha` だった。
- 影響: header、acc_organism CSV、source_merge CSV、duplicate report、strict merge の全てに波及する。
  BOLD の `insdcacs=TEST0001.1` を使うと、Python 側は一致し得るが Rust 側の `TEST0001` とは一致しない。

### 3.2 GenBank location の解釈

- 分類: **実害あり**
- Python の挙動: Biopython が `join(...)`、`complement(...)`、`<`/`>` の fuzzy boundary、remote location 等を location object として解釈し、`feature.extract(record.seq)` で抽出する。
- Rust の挙動: `parse_location` は単純な `a..b` と `complement(a..b)` のみを受け付ける。
  `join(1..5,10..15)`、`complement(join(...))`、`J00194.1:100..200` は `None` になり、その feature は skip される。
  `<1..>10` は境界記号を除去して単純範囲として扱う。
- 根拠: Python `taxondbbuilder/ncbi.py:49-61`、Rust `tauri-gui/src-tauri/src/taxondb_runner.rs:1662-1703, 1915-1920`。
  合成入力の実測で、Python は `join{[0:5], [9:15]}` を抽出して `AAAAACAAAAA`、Rust は location を保持するだけで `parse_location` が `None` だった。
- 影響: joined gene/rRNA、remote feature、single-base/between-base 等が GUI build から消える。

### 3.3 複数行 qualifier の欠落

- 分類: **実害あり**
- Python の挙動: Biopython は `/note="first line` と次行 `second line"` を一つの qualifier として `first line second line` にする。
  `/translation` も qualifier として保持する。
- Rust の挙動: feature 判定が「5 個の空白で始まる行」を先に判定するため、21 個の空白で始まる qualifier 継続行も feature 行として扱う。
  合成入力では `note` が `first line` だけになり、継続行以降の `/translation` も失われた。
- 根拠: Python `taxondbbuilder/ncbi.py:206-222`、Rust `tauri-gui/src-tauri/src/taxondb_runner.rs:1780-1823`。
  同一合成 GenBank の実測で確認した。
- 影響: `gene`、`product`、`note`、`standard_name` を複数行で返す NCBI record の marker matching が変わる。
  `/translation` 自体が ORIGIN に混入する差は確認されなかったが、qualifier 構造は一致しない。

### 3.4 NCBI filter term の未実装

- 分類: **実害あり**
- Python の挙動: `publication_date_from/to` を `[PDAT]`、`modification_date_from/to` を `[MDAT]`、`all_fields_include`/`all_fields_exclude` を `[All Fields]` として query に追加する。
- Rust の挙動: `filter`、`properties`、sequence length、`raw` だけを処理し、上記 4 種の設定を無視する。
- 根拠: Python `taxondbbuilder/ncbi.py:230-329`、Rust `tauri-gui/src-tauri/src/taxondb_runner.rs:1428-1510`。
- 影響: 同じ config でも Rust はより広い検索を行い、取得件数・入力 record・最終 FASTA が変わる。
  設定にこれらの filter がなければ一致するため、全 config で常に発生する差ではない。

### 3.5 strict merge と accession token の不一致

- 分類: **実害あり**
- Python の挙動: BOLD の `insdcacs` を `parse_accession_tokens` で分割し、Python NCBI record の accession（version 付きになり得る）と比較する。
- Rust の挙動: token 分割自体は同じだが、比較対象の NCBI accession は `ACCESSION` 行由来で version なしになり得る。
  `both` の場合に token が Rust の set にないと、BOLD row は `linked_by_insdcacs` にならず FASTA に出力される。
- 根拠: Python `taxondbbuilder/bold.py:136-146`、`taxondbbuilder/bold_api.py:451-461`、Rust `tauri-gui/src-tauri/src/taxondb_runner.rs:765-774, 2499-2525`。
  version の差は 3.1 の同一 fixture 実測で確認した。
- 影響: `both` の FASTA 件数、linked_to_ncbi、source_merge 内容が変わり、重複除去の意味が変わる。

### 3.6 source_merge.csv の schema と引用方式

- 分類: **実害あり**
- Python の挙動: 列は `source,source_record_id,acc_id,accession,processid,sampleid,organism_name,marker_key,marker_label,linked_to_ncbi,emitted_to_fasta,skip_reason,header` の 13 列。
  `csv.DictWriter` が必要な値だけを引用する。
- Rust の挙動: 列は `source,source_record_id,accession,processid,sampleid,organism_name,marker_key,acc_id,linked_to_ncbi,emitted_to_fasta,skip_reason,header` の 12 列で、`acc_id` の位置も `marker_label` も異なる。
  `write_source_merge_csv` は全フィールドを常に二重引用符で囲む。
- 根拠: Python `taxondbbuilder/models.py:161-190`、Rust `tauri-gui/src-tauri/src/taxondb_runner.rs:2061-2098`。
- 影響: 下流の列名依存処理はそのままでは Rust CSV を Python CSV と交換できない。
  byte-level golden 比較は値が単純でも失敗する。

### 3.7 duplicate report の sequence hash

- 分類: **実害あり**
- Python の挙動: uppercase sequence を UTF-8 にして SHA-1 を計算する。
- Rust の挙動: uppercase sequence を `DefaultHasher` に通し、16 進化する。
- 根拠: Python `taxondbbuilder/postprep/duplicates.py:98-153`、Rust `tauri-gui/src-tauri/src/taxondb_post_prep.rs:1489-1491`。
- 影響: 同一 duplicate group でも `sequence_hash` が必ず異なる。
  group_id や列名が同じでも、既存の Python golden や hash をキーにする downstream と互換にならない。

### 3.8 FASTA/CSV のレコード順

- 分類: **実害あり**
- Python の挙動: NCBI/BOLD spool を `(taxon_name, marker_key, source_record_id)` で sort してから FASTA に出す。
  source_merge rows も `(source, organism_name, marker_key, source_record_id)` で sort する。
- Rust の挙動: NCBI は fetch chunk と feature の出現順で直接書き、BOLD だけは taxon/marker/source_record_id で sort する。
  source_merge も insertion order で書く。
- 根拠: Python `taxondbbuilder/cli.py:404-428`、`taxondbbuilder/models.py:161-170`、Rust `tauri-gui/src-tauri/src/taxondb_runner.rs:1865-2017, 2493-2536`。
- 影響: 同じ record 集合でも FASTA の順序と sidecar の順序が変わる。
  `sample.gb` は入力順が Python の sort 順と一致するため、この観点の差は fixture だけでは顕在化しなかった。

### 3.9 post-prep: length filter の FASTA wrapping

- 分類: **実害あり**
- Python の挙動: Biopython `SeqIO.write(..., "fasta")` で保持 record を出力するため、長い sequence は FASTA 標準の折り返しになる。
- Rust の挙動: `write_fasta` が sequence を一行で出力する。
- 根拠: Python `taxondbbuilder/postprep/length_filter.py:17-27`、Rust `tauri-gui/src-tauri/src/taxondb_post_prep.rs:198-216, 223-249`。
- 影響: 配列文字列自体は同じでも、post-prep FASTA の改行と byte-level golden が変わる。
  primer trim 側は両実装とも一行出力である。

### 3.10 post-prep: primer match の tie-break と丸め

- 分類: **実害あり**
- Python の挙動: endpoint match の比較に `full_len_match` を含める。
  `required_overlap_bp` の `round` は Python の丸め規則に従う。
- Rust の挙動: endpoint match の比較に full-length 判定がなく、同じ overlap/score/mismatch の候補は出現順に残る。
  `f64::round()` は Python の `round()` と半端値で同じ規則にならない。
- 根拠: Python `taxondbbuilder/postprep/primer_trim.py:15-191`、Rust `tauri-gui/src-tauri/src/taxondb_post_prep.rs:312-469`。
- 影響: primer 名、trim sidecar の overlap/trim、境界付近の primer hit が変わり得る。
  通常の fixture では同一結果になるケースもあるが、低 overlap ratio や同率候補では差が発生する。

### 3.11 post-prep: rounds_run の報告値

- 分類: **実害あり**
- Python の挙動: iterative primer trim の早期終了時、実際に実行した round 数 `len(round_results)` を返す。
- Rust の挙動: early break しても `round_limit` を `rounds_run` として返す。
- 根拠: Python `taxondbbuilder/postprep/primer_trim.py:842-870, 909-950`、Rust `tauri-gui/src-tauri/src/taxondb_post_prep.rs:903-1025, 1164-1166`。
- 影響: 配列が同じでも GUI の進捗・ログ・統計が誤る。

### 3.12 BOLD row の fallback source_record_id

- 分類: **実害あり**
- Python の挙動: processid/sampleid/accession がない場合、`taxon|marker_key|sequence` の SHA-1 前半 16 桁を ID にする。
- Rust の挙動: `bold_{taxon}_{marker}_{sequence_length}` を sanitize した ID にする。
- 根拠: Python `taxondbbuilder/bold_api.py:479-576`、Rust `tauri-gui/src-tauri/src/taxondb_runner.rs:875-949`。
  同一 row `{"marker_code":"COI","nucleotides":"acgt","species":"Testus alpha"}` の実測は、Python が `bold_1127aee819b31baf`、Rust が `bold_Testus_alpha_coi_4` だった。
- 影響: BOLD header、acc_id、重複判定、再実行時の record identity が変わる。

### 3.13 header template の未定義 field / format 構文

- 分類: **実害あり**
- Python の挙動: `SafeFormatDict.__missing__` により未定義 field は空文字になる。
  Python の `Formatter` なので format spec、conversion、escaped brace も解釈する。
- Rust の挙動: `{field}` という完全一致文字列を replace するだけで、未定義 field はそのまま残る。
  `{field:...}` や `{{` は Python と同じには展開されない。
- 根拠: Python `taxondbbuilder/headers.py:40-47`、Rust `tauri-gui/src-tauri/src/taxondb_runner.rs:1271-1285`。
- 影響: 現行の単純な built-in template では表面化しにくいが、ユーザー定義 header format と duplicate extractor の結果を変える。

### 3.14 BOLD/marker 正規化の限定的な一致と小差

- 分類: **一致**（基本経路）、**理論上のみ**（入力異常時の小差）
- Python の挙動: BOLD marker code → aliases → phrases → key の順で候補を探し、marker text を英数字小文字化して比較する。
  `parse_accession_tokens` は comma/空白/semicolon/pipe/slash で分割し、重複を除く。
- Rust の挙動: 同じ strategy 順と同等の英数字小文字化・token 分割・重複除去である。
- 根拠: Python `taxondbbuilder/bold_api.py:451-510`、Rust `tauri-gui/src-tauri/src/taxondb_runner.rs:765-866`。
  `COI-5P / acgt-123n / processid=P1` の同一 row の実測で、marker key `coi`、label `COI-5P`、sequence `ACGTN`、source id `P1` は一致した。
- 小差: marker alias の trim、marker input の trim、空文字 region pattern の扱い、Python `re` と Rust `regex` の構文差は一致しない可能性がある。
  これらの異常 config は未検証である。

### 3.15 header sanitize の基本 ASCII 経路

- 分類: **一致**（基本 ASCII 入力）
- Python の挙動: trim、連続 whitespace の `_` 化、英数字・`.`・`_`・`-` 以外を `_` 化する。
- Rust の挙動: trim、連続 whitespace の `_` 化、同じ ASCII 許可文字以外を `_` 化する。
- 根拠: Python `taxondbbuilder/headers.py:14-17`、Rust `tauri-gui/src-tauri/src/taxondb_runner.rs:1257-1269`。
  Python test の `Homo sapiens / COI` 相当の ASCII 経路はコード上同じである。
- 未検証: Unicode whitespace の全種類と Unicode を含む実データでの完全一致。

### 3.16 エラー処理・retry・timeout

- 分類: **実害あり**（設定・障害時）、**未検証**（実ネットワーク）
- Python の挙動: BOLD timeout の既定値は 60 秒、retries は設定可能な 3 回、gzip response を Python 側で展開する。
  NCBI fetch は `fetch_retries` の既定 4 回、HTTP 408/429/502/503/504 と network error を retry 対象とする。
  NCBI email/API key は config または `NCBI_EMAIL`/`NCBI_API_KEY` 環境変数から取得する。
- Rust の挙動: BOLD timeout の既定値は 900 秒、BOLD retry は設定可能だが、NCBI eutils は retries=3 固定。
  curl の retry 判定は特定の exit code/文字列に限られ、HTTP status を Python と同じ policy では扱わない。
  email/API key は config のみを読む。
- 根拠: Python `taxondbbuilder/bold_api.py:23-76`, `taxondbbuilder/ncbi.py:403-446, 497-528`、Rust `tauri-gui/src-tauri/src/taxondb_runner.rs:20-27, 227-280, 1528-1597`。
- 影響: 同じ一時障害で片方だけ成功・失敗する、または待ち時間と NCBI rate limit が変わる。
  実際の HTTP 障害・retry 回数は外部ネットワークを使わない方針のため未検証である。

### 3.17 GUI と sidecar の実行契約

- 分類: **実害あり**（現行アーキテクチャ）、**未検証**（sidecar 移行後の具体的な UX）
- Python の挙動: CLI は Rich progress と logger を持つが、Tauri の event stream や cancellation token を直接提供しない。
- Rust の挙動: `main.rs` が Rust runner を worker thread で起動し、log tail から進捗を parse し、`AtomicBool` と child slot を使う GUI 側の cancellation/進捗契約を持つ。
  build 完了後の post-prep も `run_post_prep_rust` から Rust 実装を呼ぶ。
- 根拠: `tauri-gui/src-tauri/src/main.rs:1797-1832, 785-978`、Python `taxondbbuilder/cli.py:430-632`。
- 影響: (c) では Rust の二重実装を削除するだけでは、GUI 固有の live progress、cancel、post-prep metrics が失われる。
  sidecar の stdout/log protocol、signal/termination、progress event の adapter が必要である。

## 4. 実測結果

### 4.1 `tests/fixtures/sample.gb`

Python の Biopython 出力:

```text
TEST0001.1  organism=Testus alpha other.
  source  1..60                    organism=Testus alpha
  gene    5..20                    gene=12S       extract=AAAAAAAAAAAAAAAA
  rRNA    complement(25..40)       product=16S ribosomal RNA
                                      extract=TTTTTTTTTTTTTTTT
TEST0002.1  organism=Testus beta other.
  gene    10..25                   gene=COI        extract=CCCCCCCCCCCCCCCC
```

Rust の一時 probe 出力:

```text
TEST0001  organism=Testus alpha
  source  1..60                    organism=Testus alpha
  gene    5..20                    gene=12S
  rRNA    complement(25..40)       product=16S ribosomal RNA
TEST0002  organism=Testus beta
  gene    10..25                   gene=COI
```

単純な `complement(25..40)` の location 形状は両方で認識できるが、record ID と organism は一致しない。
この fixture では `join` がないため、joined feature の差は次の合成入力で測った。

### 4.2 複合 GenBank probe

同一入力に以下を含めた。

```text
gene            join(1..5,10..15)
                /gene="COI"
                /note="first line
                second line"
                /translation="MPEPTIDE"
gene            <2..>8
                /gene="12S"
```

Python は `join{[0:5], [9:15]}` を解釈し、`AAAAACAAAAA` を抽出し、note を `first line second line`、translation を `MPEPTIDE` とした。
Rust は `join(1..5,10..15)` を raw location として保持したが `parse_location` は `None`。
また note は `first line` だけとなり、translation は feature qualifier から消えた。
`<2..>8` 自体は Rust が単純範囲として扱える形だった。

### 4.3 BOLD normalization probe

同一 row 1:

```json
{"marker_code":"COI-5P","nucleotides":"acgt-123n","processid":"P1","species":"Testus alpha"}
```

両実装で marker key=`coi`、marker label=`COI-5P`、sequence=`ACGTN`、source id=`P1` になった。

同一 row 2:

```json
{"marker_code":"COI","nucleotides":"acgt","species":"Testus alpha"}
```

Python は `bold_1127aee819b31baf`、Rust は `bold_Testus_alpha_coi_4` になった。
marker matching と sequence cleaning の基本経路は一致する一方、fallback identity は一致しない。

### 4.4 テスト実行

- Python: `.venv/bin/python -m pytest -q` → `32 passed`
- Rust: `cargo test --manifest-path tauri-gui/src-tauri/Cargo.toml` → `4 passed`
- 全実測で使用した Rust probe は実行後に削除し、Rust/Python/テストに差分を残していない。

## 5. 方針判断への提言

### 推奨: (c) GUI を sidecar 呼び出しに一本化

この調査では、drift は「Rust のファイルが大きい」問題ではなく、同じ入力を二つの意味論で処理している問題だった。
GenBank、query filter、strict merge、CSV schema、hash、post-prep の差が既に存在するため、(a) のモジュール分割だけではリスクは下がらない。

(b) は短期の安全策として有効である。
少なくとも今回の実測ケース、`source_merge.csv` の schema、strict merge の version ケース、duplicate hash、primer round 統計を differential test にしてから移行すれば、移行前後の回帰を検知できる。
しかし (b) だけでは、Python と Rust の両方に修正を反映する二重保守が残る。

したがって、PM の選択としては次を推奨する。

1. 短期: (b) の最小限の differential test を追加し、現行 GUI Rust の出力を固定する。
2. 本方針: (c) に移行し、build/post-prep の source of truth を Python package にする。
3. 移行後: Rust は Tauri adapter、進捗イベント、キャンセル、ファイル選択、設定 I/O に限定する。

sidecar 一本化で追加設計が必要な GUI 固有機能は次のとおりである。

- 現在 `main.rs` が log tail から作っている per-taxon/per-stage の live progress event。
- GUI の cancel 操作を Python process とその子 process に伝える signal/termination protocol。
- build 完了後の primer_trim、length_filter、duplicate report の統計と生成 path を GUI event に返す protocol。
- sidecar の stdout/stderr、exit code、部分出力、途中キャンセル時の cleanup を GUI が解釈する契約。

これは (c) の欠点というより移行時の境界設計である。
現行 Python 側には Rich progress と logger はあるため、Tauri が読む構造化 log または stdout protocol と cancellation signal を追加すれば対応可能だが、今回の調査範囲ではその移行実装は行っていない。
