# TaxonDBBuilder v3.3.6

公開日: 2026-10-15

### Scope
- 実データで確認できた BOLD 検索とマーカー判定の不具合に限定する。Cloudflare の 403 を回避する実装、`source=both` の部分成功、BOLD 取得の新しい件数制限は含めない。
- バージョン宣言は 8 箇所すべて 3.3.6 に更新した (一致テストで検証)。

### Fixed
- BOLDマーカー判定とキャッシュ監査を責務ごとに分割し、判定結果を変えずにcognitive complexityを削減した。`resolve_marker_from_record()`は25→4、`compare_cache()`は30→0、本体と監査ツール、GUIを含む最大値は30→23となった。
- BOLDマーカーの完全一致を全定義で優先し、部分一致を遺伝子名の境界に制限した。COII→coiとND4L→nd4の誤判定を修正し、複数遺伝子に一致する場合は未確定として除外する。宣言順序に依存せず、COXIII、COI-5P-like、16S ribosome、ND5-0の照合を維持する。840件のキャッシュでは20件を修正し、新規除外は0件だった。
- BOLDの分類群応答で要求名の同一性と種レベルの階級を確認するようにした。種から属への拡張、別名への変更、分類群を含まない応答は理由付きの未解決として検索を止める。同一属への正規化と空白、大文字小文字の揺れは許可する。同義語の関係を証明できない別名は曖昧として保持し、不正な学名とは断定しない。taxとidsが混在する場合はidsを検索条件から除く。詳細と検証結果は`docs/bold-final-review-v3.3.6.md`に記録した。
- **学名が BOLD の分類群として解決できなかった場合を、0 件とは別の状態として扱うようにした。** preprocessor は解決できない学名を `ids:processid:<学名>;ids:sampleid:<学名>;ids:insdcacs:<学名>` に変換する (2026-10-12 の `Silurus tomodai`、2026-10-14 の `Tachysurus nudiceps` で実測)。これは ID 検索なので 0 件になり、従来は「その学名に標本が無い」と同じ `specimens=0` と報告していた。`prepare_bold_query()` は `ids:` 語だけ返った場合を含め、分類群の同一性を確認できない応答を未解決 (`resolved=false`) とし、**summary・query・download へは送信しない**。実行ログには `taxon_unresolved reason=<preprocessor が返した語>` を残し、manifest の `queries.bold[]` に `resolved: false` を追記する (`specimen_count` は `null` のまま。既存キーの意味は変えていないので後方互換)。真に 0 件の学名は従来どおりのメッセージを出す。HTTP エラーは未解決として扱わない — `BoldApiError` は従来どおりそのまま上昇する。
- **`coiii` に BOLD の実コード `COXIII` を追加した。** BOLD は `marker_code=COXIII` と書くが、設定は `aliases = ["coiii", "cox3"]` と `phrases = ["COIII", "COX3", ...]` のみで、`marker_codes` は完全一致、aliases / phrases は部分一致という照合規則では `COXIII` はどれとも一致しなかった。キャッシュ済み `Silurus asotus` 117 行のうち 3 行 (例 `processid=CYTC5134-12`、781 bp) がミトコンドリア全遺伝子の実行でも落ちていた。`COIII` / `COX3` の既存照合はそのまま通る。
- **BOLD へのリクエストの `User-Agent` が実行中のバージョンを反映するようにした。** 従来 `TaxonDBBuilder/0.1` 固定で、3.3.0 系以降すべてのリリースで同じ値を送っていた。実行時バージョンは実行 manifest と同じ解決経路 (`VERSION` ファイル、PyInstaller 同梱版はバンドル内の `VERSION`) を使う。PyInstallerでfreezeしたv3.3.6のsidecarで`TaxonDBBuilder/3.3.6`が送られることをローカルの偽BOLDサーバーで確認した。アクセス制限の回避を目的とした偽装ではない。

### Added
- `tools/bold_cache_audit.py`: BOLD ダウンロードキャッシュを読み取り専用で集計し、`marker_code` の出現頻度・配列の有無・`normalize_bold_row()` の除外理由を出力する調査ツール。
- `docs/source-both-partial-failure.md`: BOLD 失敗時に NCBI のみで継続する設計案 (未実装)。既定は fail-fast 維持、`--on-bold-failure` の明示指定時だけ継続、manifest へ `requested` / `completed` / `failed` を記録、部分成果物は別名で区別、GUI は実行前の宣言的オプションへ写す。

### Upgrade guidance
- 影響を受けるマーカーの既存参照配列は再生成を推奨する。BOLD由来のCOI、COII、COIII、ND4、ND4Lを含む参照データでは誤分類が残っている可能性がある。COXIIIの取りこぼしを解消するためにも、該当するBOLDデータを再取り込みする。

### Distribution
- GitHub Releaseでは自動生成のソースアーカイブとRelease Notesのみを公開する。AppImageとSHA256ファイルは添付しない。

### Known issues
- BOLD の 403 は未解消のまま。観測は 2 種類あり (download だけ 403 / preprocessor から 403)、`docs/bold-api-diagnostics.md` §7 に併記し、単一原因は断定していない。`quote(query_id, safe="")` は変更していない。
- 個々のBOLD標本の分類同定精度を保証する変更ではない。検索対象の分類群スコープとマーカー判定の誤りを防ぐ対策であり、標本自体の誤同定は別途確認が必要である。
