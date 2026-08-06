# Changelog

## 3.2.0 (planned)

### Scope
- `main` から `refactor/modularize` を Pull Request でマージする差分を対象。

### Added
- `build` に `--source [ncbi|bold|both]` を追加。BOLD Data Portal から taxon 指定で配列を取得し、`insdcacs` と NCBI accession が完全一致する場合のみ NCBI 側を優先して統合する strict link 判定を実装。
- BOLD 由来レコード用の `*.fasta.source_merge.csv` を追加し、`*.acc_organism.csv` に `source` / `linked_to_ncbi` / `skip_reason` 等の列を追加。
- Post Prep 用の primer set を 8 種類 (`mifish_u`, `mideca`, `mtinsects_16s`, `mimammal_u`, `mibird_u`, `amph_16s`, `uceta`, `mifish_unity`) 追加し、Python 側にハードコードされた組み込み定義として持たせることで `primer_file` を指定せずに利用可能に。
- GUI の Post Prep カードに primer candidates のチェックボックス (複数選択可) を追加。選択すると `primer_set` 欄に自動反映される。

### Changed
- `taxondbbuilder.py` (3,483 行の単一ファイル) を `taxondbbuilder/` パッケージへ完全分割。`taxondbbuilder.py` / `taxondb_bold.py` は互換シムとして維持。
- GUI の build / post-prep 実行を Python sidecar (PyInstaller) 経由に一本化し、Rust 側の重複実装 (`taxondb_runner.rs` / `taxondb_post_prep.rs`) を削除。CLI と GUI が同一の Python パッケージから同一の結果を生成する。
- GUI の Run Monitor で、ログファイルを 100ms 間隔でポーリングする `tail_log_once` が読み取りチャンクの境界で行を分断し、進捗 (%) と Metrics が更新されなくなる不具合を修正。不完全な末尾行をバイト単位でバッファし、完全な行のみを解析するように変更。
- GUI のログ表示 (`#log-view`) の折り返しを `white-space: pre` から `pre-wrap` + `overflow-wrap: anywhere` に変更し、長い行を含め一貫して折り返すように統一。
- Post Prep の primer_trim に関する GUI の文言・"Ready to Build?" 判定・CLI エラーメッセージを、`primer_file` が必須ではなくなったことに合わせて更新。

### Fixed
- 実行ログの書き込みをスレッドセーフな共有ストリーム経由に変更し、コンソール出力とログファイルの内容を一致させた。

## 3.0.0 (planned)

### Scope
- `main` (`2effd40`) から `feat/tauri-gui-v1` を Pull Request でマージする差分を対象。

### Added
- Tauri ベースのクロスプラットフォーム GUI を追加 (`tauri-gui/`)。
- Sidecar 実行基盤 (Rust) を追加し、`taxondbbuilder` CLI のジョブ実行・進捗表示・ログ表示・結果表示を GUI から操作可能に。
- `db.toml` 読み込み機能を追加し、GUI の入力パラメータへ反映可能に。
- GitHub Actions に GUI ビルドワークフロー (`.github/workflows/tauri-build.yml`) を追加。

### Changed
- GUI の build / post-prep 実装を Python sidecar に統合し、CLI と GUI が同じ Python パッケージから同一の結果を生成するように変更。
  GUI の出力は従来と変わるため、従来の GUI で構築した DB は再構築を推奨。
  具体的には、`join(...)` location を持つ feature の欠落、accession version の欠落、`ORGANISM` 継続行の欠落、複数行 qualifier の途中切断、`[PDAT]` / `[MDAT]` / `[All Fields]` filter 設定の無視、duplicate report の hash 不一致を解消。
- Python CLI を `taxondbbuilder/` パッケージへ分割。
  既存の `python3 taxondbbuilder.py ...` による起動は互換シム経由で引き続き利用可能。
- Python の pytest テストスイートを新設し、CLI の出力と post-prep の結果を固定。
- Entrez 取得処理で HTTP 400 発生時のフォールバックを追加し、履歴ベース取得失敗時の取得継続性を改善。
- `.gitignore` を更新し、Tauri GUI のローカル生成物 (`node_modules`, `dist`, `target`, sidecar バイナリ, `__pycache__`) を除外。
- `tauri-gui/.gitignore` を追加し、GUI 開発・ビルドで生成されるローカルファイルを `tauri-gui` 配下で管理可能に。
- `tauri-gui/src-tauri/src/main.rs` の sidecar 解決処理を改善し、`TAXONDBBUILDER_BIN` がディレクトリを指す誤設定を明示エラー化。
- `README.md` と `tauri-gui/README.md` を更新し、`uv` 前提の実行環境構築手順と GUI/CLI 利用フローを整理。

## 1.0.0 (2026-02-01)

### Changed
- 旧β版 (分類群特異パイプライン) から、**汎用DB FASTA生成ツール**へ方針転換。
- 実行は `python3 taxondbbuilder.py build` を使用。
- 設定は `setting.txt` ではなく **TOML (`configs/db.toml`) ** を使用。
- 分類群は taxid / 学名を指定可能。マーカーはTOMLで定義したフレーズ群をprefix指定。
- 出力は **DB用の結合FASTA** とログのみ (解析・抽出・フィルタリングは対象外) 。
- 旧スクリプト群 (`scripts/`) と関連フォルダ (`GI_Folder/`, `ID/`, `MiFishDB/`) は削除。

### Usage (new)
```bash
python3 taxondbbuilder.py build -c configs/db.toml -t 32443 -m 12s
```

## 0.x (beta)
### Summary
- DLMiFishとして公開されていたβ版パイプライン。
- Teleostei/Chondrichthyes/Cyclostomataの**12S rRNA**に特化。
- `python3 DLMiFish.py` で一括実行し、複数スクリプトの連携で結果を生成。
- 設定は `setting.txt` を使用。
