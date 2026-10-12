# Changelog

## 3.3.3 (planned)

### Scope
- `main` (`83a29a8`、タグ `v3.3.2`) から `build/appimage-only` を Pull Request でマージする差分を対象。

### Changed
- 出荷する成果物を AppImage のみにした。`bundle.targets` を `["appimage"]` に固定し、deb と rpm を生成しない。ビルドは 5m23s から 1m23s に短縮した。deb と rpm が約 4 分を占めていたが、リリース資産は一貫して AppImage 1 本のみだった。
- sidecar のビルドを Linux 専用にした。`build_sidecar.py` から `detect_os_key` と Windows 用の `.exe` 名前付けを削除した。pixi workspace は `linux-64` のみを対象としており、他の分岐は到達不能だった。

### Removed
- sidecar の stub 配置経路 (`build_sidecar.py --stub` と `write_stub_binary`)。CI が存在せず、リポジトリ内のどこからも呼ばれていなかった。
- `build-appimage-fast.sh` の `LDAI_RUNTIME_FILE` フック。設定する場所が存在しない未使用のオプションだった。
- `tauri-gui/src-tauri/bin/README.md`。「sidecar バイナリは不要で、GUI は統合 Rust ランナーを使う」と記述していたが、`externalBin: ["bin/taxondbbuilder"]` が実際に sidecar バイナリを要求しており、記述が実装と矛盾していた。ディレクトリ自体は `build_sidecar.py` が作成するため、git 上のプレースホルダも不要。
- `tauri-gui/package.json` の `preview` スクリプト。GUI は `tauri:dev` (devUrl 1420) もしくはバンドルで起動し、standalone の preview サーバは使わない。

### Known issues
- 系統樹の葉を検索でハイライトしたとき、`leafPalette` が設定する `stroke` (`#d76735`) と `stroke-width` (2.5) が画面に反映されない。`tauri-gui/src/styles.css` の `.tree-node-point { stroke: #fff; stroke-width: 1 }` が presentation attribute より優先されるため、実際に変わるのは `fill` と `font-weight` のみ。リファクタリング前から存在する CSS の問題で、今回は修正していない。Chromium (Playwright) と WebKitGTK 2.52.6 の両方で同じ挙動を実測済み。修正は CSS 1 行（`.tree-node-point` の stroke 指定を外す、もしくわ match 時だけ上書きする）だが、見た目の意図を確認して別途決める。現状の挙動は `tauri-gui/tests/e2e/tree-view.e2e.mjs` の "search highlights only the matching leaf" が固定している。

### Notes
- rpm を出荷しなくなったため、rpm ペイロードの zstd 化は取りやめた。計測結果は残している。164 MB の PyInstaller sidecar に対する rpm バンドリングは gzip 既定で 16m25s、zstd level 3 で 3m51s。ペイロードは deflate 済みで圧縮率は 1% 未満のため、圧縮方式は成果物サイズにほぼ影響しない。
- 新規 clone で sidecar をビルドせずに `cargo test --offline` を実行すると、`externalBin` の不足で失敗する。README の手順 (`pixi install -e dev` からビルド) どおりなら問題ない。CI を導入する場合は sidecar ビルドを前に置く。

## 3.3.2 (2026-09-21)

### Added
- 完全なミトゲノムレコードを取得するためのマーカーを GUI に追加した。

### Changed
- マーカー設定ファイルと GUI のマーカー表記を整形した。

### Notes
- タグ `v3.3.2` は `83a29a8` を指すが、そのコミットのバージョン宣言 (VERSION、`tauri.conf.json`、`package.json`、`Cargo.toml`) は 3.3.1 のままだった。3.3.2 への bump コミット `153bdc5` は `issue-11-complete-mitogenome` にのみ存在し、main に届いていない。
- 成果物を GitHub リリースに添付する運用ではない (`v3.3.0` と `v3.3.1` の資産はなし)。`v3.3.2` にのみ 233 MiB の `TaxonDBBuilder_3.3.2_amd64.AppImage` が添付されていたが、タグが指すソースから再現できずダウンロードも 0 回だったため削除した。これで全リリースの資産が空になった。タグとバージョン宣言の対応だけが信頼できる記録になるため、3.3.3 では bump を main 側の履歴に載せてからタグを切る。

## 3.3.1 (2026-09-21)

### Added
- ミトゲノム抽出を全長取得と領域取得に分離した。

### Changed
- Python 依存を pixi に移行し、`requirements.txt` を削除した。
- Vite を 8 に更新した。
- 再ビルド時に sidecar と AppDir を再利用するようにした。Python 側の入力の fingerprint が変わらなければ sidecar を再構築せず、既存 AppDir を使う `tauri:build:appimage:fast` 経路を追加した。
- GUI の TaxID 入力を数値として検証するようにした。
- `configs/db.toml` から `api_key` と `email` を削除した。
- リリースバージョンの宣言を 3.3.1 に統一した。

### Fixed
- Post Prep の明示的な enable フラグが無視されていた不具合を修正した。
- 実行時に `gstreamer1.0-plugins-base` が必要であることを README に記載した。

## 3.3.0 (2026-08-12)

### Added
- Post Prep に MSA ツリー工程 (`msa_tree`) を追加した。当初の MAFFT / IQ-TREE サブプロセス呼び出しは、kalign-python と piqtree のライブラリ呼び出しに置き換えた。
- TaxID 単位のツリー構築モードと bootstrap 支持率を追加した。
- GUI に MSA とツリーの同期ビュー (インタラクティブな結果表示) を追加した。
- 保存度による背景フィルタを追加した。
- 下流連携向けのデータベース出力形式を追加し、FASTA 出力形式を 1 種に統一した。
- 再現可能なビルドのための整合性チェックと manifest 出力を追加した。

### Changed
- セットアップフォームを再構成し、ヘルプ誘導・入力コントロール・長さ指定の対コントロールを整理した。
- ワーカー数を CPU の利用可能スレッド数で上限設定した。
- 未使用の BOLD 取得オプションと非対応の primer 定義を削除した。
- 進捗処理の重複を整理し、設定アセットを統合した。

### Fixed
- ツリーキャンバスをドラッグ中にページ全体が選択される不具合を修正した。
- ラップされたストリームディスクリプタがコンソールに転送されない不具合を修正した。
- GenBank キャッシュが TaxID を跨って混在する不具合を修正した。
- 結果ペインのスクロールがデスクトップ全体に波及する不具合を修正した。
- 任意依存の MSA インポートを遅延させるよう修正し、sidecar の起動失敗を回避した。

## 3.2.0

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

## 3.0.0

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
