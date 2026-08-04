# Refactoring Plan

TaxonDBBuilder のリファクタリング計画。実装は codex が担当し、Claude (PM) が各フェーズのレビューと受入判定を行う。

## 0. 大原則

**振る舞いを一切変えない。** 本作業は純粋なリファクタリングであり、機能追加・仕様変更・バグ修正は対象外。

- CLI の外部インターフェース (`build` / `list-markers` / `list-primer-sets` とその全オプション) を維持する
- 出力ファイル (FASTA, `.log`, `*.acc_organism.csv`, `*.source_merge.csv`, duplicate report) の**内容・列構成・並び順・ファイル名**を維持する
- コンソール出力 (rich table, ログ行のフォーマット) を維持する。GUI 側 (`tauri-gui/src-tauri/src/taxondb_runner.rs`) がログ行を正規表現でパースしているため、ログ文言の変更は GUI を壊す
- 既存の `configs/db.toml`, `configs/markers_mitogenome.toml`, `configs/primers.toml` をそのまま読めること
- バグを見つけた場合は**直さずに** `docs/Refactoring-Findings.md` に記録する

## 1. 現状

| 対象 | 行数 | 問題 |
|---|---|---|
| `taxondbbuilder.py` | 3,483 | CLI/設定/NCBI/BOLD/FASTA/post-prep/ログが単一モジュールに同居 |
| `taxondb_bold.py` | 625 | BOLD API クライアント。比較的まとも |
| `tauri-gui/src-tauri/src/taxondb_runner.rs` | 2,601 | sidecar 実行・ログパース・進捗管理が混在 |
| `tauri-gui/src-tauri/src/main.rs` | 1,895 | Tauri command 群・状態管理・設定 I/O が混在 |
| `tauri-gui/src-tauri/src/taxondb_post_prep.rs` | 1,650 | post-prep ロジック |
| `tauri-gui/src/main.js` | 697 | フロントエンド |

巨大関数 (Python):

| 関数 | 概算行数 |
|---|---|
| `build()` | 620 |
| `apply_post_prep_primer_trim()` | 375 |
| `load_config()` | 264 |
| `run_vsearch_endpoint_recheck()` | 177 |
| `write_duplicate_acc_reports_csv()` | 135 |
| `fetch_genbank()` | 153 |

**テストが存在しない。** これが最大のリスク。

## 2. 制約

- PyInstaller (`taxondbbuilder.spec`) が `taxondbbuilder.py` を単一エントリとしてビルドし、その成果物を `tauri-gui/scripts/build_sidecar.py` が Tauri sidecar として配置する。package 化する場合は両方の追従が必須
- 依存は `requirements.txt` (biopython, rich, typer) のみ。**新規依存を追加しない**
- 開発用 venv: `.venv/bin/python` (pytest 導入済み)
- BOLD/NCBI への実ネットワークアクセスはテストで行わない

## 3. フェーズ

### Phase 0 — 特性化テスト (safety net)

`tests/` に pytest スイートを作る。目的はリファクタ前後で出力が同一であることを機械的に保証すること。

対象:
- `load_config()` — 正常系 + source (ncbi/bold/both) ごとの validation 分岐 + エラーメッセージ
- `normalize_marker_map()` / `resolve_marker_key()` / `build_marker_query()` / `build_region_patterns()`
- `build_header()` / `sanitize_header()` / `resolve_header_format()` / `compile_header_extractors()` / `extract_header_fields_from_header()`
- `extract_ncbi_records_from_genbank_chunk()` — 小さな GenBank fixture を同梱して `CanonicalRecord` の列を固定
- `build_bold_canonical_record()` / `normalize_bold_row()` / `parse_accession_tokens()`
- `emit_records_to_fasta()` / `write_acc_organism_mapping_csv()` / `write_source_merge_csv()` / `write_duplicate_acc_reports_csv()` — 出力を golden ファイルと突き合わせる
- `apply_post_prep_primer_trim()` の純粋部分 (`find_best_prefix_match`, `find_best_suffix_match`, `resolve_orientation`, `count_mismatches`, `required_overlap_bp`, `compute_trim_lengths_from_row`, `confidence_label`)
- `apply_post_prep_length_filter()`
- `build_query()` / `build_filter_terms()` / `build_output_path()`
- CLI レベル: `list-markers`, `list-primer-sets` を `typer.testing.CliRunner` で叩き、標準出力を golden 比較

方針:
- ネットワークは呼ばない。Entrez/BOLD は monkeypatch でスタブ化
- fixture は `tests/fixtures/` に最小限を自作する (`test/taxdump.tar.gz` 等の巨大データは使わない)
- golden は `tests/golden/` に置き、`--update-golden` 相当の再生成手段は不要
- `pytest.ini` か `pyproject.toml` でテスト設定。**ランタイム依存は増やさない** (pytest は開発用のみ)

受入基準: `.venv/bin/python -m pytest` が全て green。カバレッジは行数より**出力の同一性**を優先する。

### Phase 1 — Python package 分割

`taxondbbuilder.py` を `taxondbbuilder/` パッケージへ。想定構成:

```
taxondbbuilder/
  __init__.py
  __main__.py          # python -m taxondbbuilder
  cli.py               # typer app, build/list-markers/list-primer-sets のコマンド定義
  config.py            # load_config, resolve_support_file_path, primer set 読込
  markers.py           # marker map 正規化・解決・query 構築・region pattern
  models.py            # BuildSource, PostPrepStep, ResolvedTaxon, CanonicalRecord + spool I/O
  ncbi.py              # Entrez setup, taxon 解決, fetch_genbank, GenBank -> CanonicalRecord
  bold.py              # BOLD spool 生成 (taxondb_bold.py の薄いラッパ)
  headers.py           # header template / extractor
  fasta.py             # emit_records_to_fasta, sidecar CSV 出力
  postprep/
    __init__.py
    length_filter.py
    primer_trim.py     # 375行関数をここで分解
    duplicates.py
    vsearch.py
  console.py           # rich table, print_header, byte format
  logging_utils.py     # setup_run_logger, TeeStream, tee_console_output
```

- `taxondbbuilder.py` は**互換シム**として残す (`from taxondbbuilder.cli import app` して `app()` を呼ぶだけ)。既存の `python3 taxondbbuilder.py build ...` を壊さない
- `taxondb_bold.py` は `taxondbbuilder/bold_api.py` へ移動し、トップレベルにシムを残すか、import 経路を整理する (判断は codex に委ねるが、後方互換を壊さないこと)
- 循環 import を作らない。`models.py` は他モジュールに依存しない葉ノードにする
- 巨大関数の分解:
  - `build()` → 引数検証 / 設定解決 / 実行計画組み立て / NCBI 取得 / BOLD 取得 / マージ・出力 / post-prep / 結果表示 に分ける
  - `apply_post_prep_primer_trim()` → 準備 / 候補探索 / vsearch recheck / 集計 / 書き出し に分ける
  - `load_config()` → source 別 validator に分ける
- モジュール分割時に振る舞いを変えない。関数の引数順・戻り値も原則維持する
- 各ステップで Phase 0 のテストを回し、green を維持したままコミットを刻む

受入基準: pytest 全 green + `list-markers` / `list-primer-sets` の出力が分割前と一致。

### Phase 2 — パッケージング追従

**注記:** 調査の結果、この sidecar バイナリは Tauri アプリからは使われていない (Phase 3a 参照)。ただし CLI 単体配布物としては有効なため、package 化の追従は行う。優先度は Phase 1 / 3a より低い。

- `taxondbbuilder.spec` を package 構成に合わせて更新 (エントリ、`hiddenimports`, `datas`)
- `tauri-gui/scripts/build_sidecar.py` が通ることを確認
- 実際に PyInstaller ビルドを実行し、生成バイナリで `list-markers` が動くことを確認する

受入基準: sidecar バイナリがビルドでき、`--help` と `list-markers` が動作する。

### Phase 3a — Python / Rust 実装 drift の実態調査 (調査のみ、コード変更なし)

**背景 (2026-08-04 判明):** GUI は Python sidecar を起動していない。`main.rs:1797` が `taxondb_runner::run_build` (ネイティブ Rust) を呼んでおり、`tauri.conf.json` に `externalBin` 宣言もない。つまり build パイプライン全体が Python と Rust で**独立に二重実装**されている。

Rust 側が再実装しているもの:

| 機能 | Python | Rust (`taxondb_runner.rs`) |
|---|---|---|
| BOLD API クライアント | `taxondb_bold.py` | `prepare_bold_query` / `download_bold_documents_to_path` |
| Entrez クライアント | `fetch_genbank` (Biopython) | `eutils_*` (`curl` サブプロセス) |
| GenBank パーサ | Biopython `SeqIO` | 手書き `parse_genbank_record` |
| marker 解決 / query 構築 | `resolve_marker_key` / `build_query` | 同名関数 |
| header 生成 | `build_header` | `build_header` |
| FASTA / sidecar CSV 出力 | `emit_records_to_fasta` 等 | `write_acc_organism_csv` / `write_source_merge_csv` |
| strict merge 判定 | `insdcacs` 照合 | 同ロジック |

これがリポジトリ最大の技術的負債。ファイル分割では解決しない。

**Phase 3a のタスク:** 両実装の振る舞いがどこでどれだけ乖離しているかを調査し、`docs/Python-Rust-Drift.md` にまとめる。**コードは変更しない。**

調査観点:
- GenBank パース: location 構文 (`join(...)`, `complement(...)`, `<`/`>` 部分配列, 複数行 qualifier, `/translation` 混入)、Biopython と手書きパーサの解釈差
- marker 解決 / alias 正規化 / region pattern の正規表現構築の差
- header テンプレート展開・`sanitize_header` の差 (禁止文字、空値の扱い)
- FASTA 行折り返し・改行コード・レコード順序
- sidecar CSV の列順・引用・空値表現
- BOLD row 正規化 (`normalize_bold_row`)・`parse_accession_tokens`・marker code マッチングの差
- filter term / query 文字列の組み立ての差
- エラー処理・リトライ・タイムアウトのポリシー差

成果物: 乖離点の一覧を「実害あり / 理論上のみ / 一致」に分類し、深刻度順に並べた `docs/Python-Rust-Drift.md`。可能なものは同一入力を両実装に通した実測結果を添える。

この結果を見て PM が Phase 3b の方針 (分割のみ / 差分検証テスト追加 / sidecar 一本化) を決定する。

### Phase 3b — Rust GUI モジュール分割

- `taxondb_runner.rs` → プロセス起動 / ログパース / 進捗集計 / リトライ にサブモジュール分割
- `main.rs` → Tauri command 群 / アプリ状態 / 設定 I/O に分割
- `taxondb_post_prep.rs` → 機能単位に分割
- ログパース部分には Rust の unit test を追加する (現行フォーマットの固定)
- `cargo fmt` / `cargo clippy` / `cargo test` を通す
- Tauri の `#[tauri::command]` 名と `invoke` の引数名は**変更しない** (`tauri-gui/src/main.js` が依存)

受入基準: `cargo test` green、`cargo clippy -- -D warnings` (可能な範囲で)、`cargo build` 成功。

### Phase 4 — 統合検証

- 全テスト実行
- CLI スモークテスト (ネットワーク不要な範囲)
- `README.md` の構成説明・`CHANGELOG.md` を更新
- 差分レビューとコミット整理

## 4. 進め方

- ブランチ: `refactor/modularize`
- フェーズごとに小さくコミットする。`git bisect` で回帰を追える粒度を保つ
- 各フェーズ完了時に PM (Claude) がレビューし、次フェーズを承認する
- 判断に迷ったら「振る舞いを変えない側」に倒す
