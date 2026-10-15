# v3.3.6 BOLDデータ正確性レビュー

2026-10-15。基準コミットは`21dc64b`、対象は`fix/bold-query-validation-v3.3.6`。
実BOLD APIへのリクエストは送っていない。

## マーカーの誤分類

旧実装はmarker_codes、aliases、phrases、keyの順に走査し、最初に部分一致したキーを採用していた。
COIIではcoiの別名COIが先に一致するため、後続のcoii完全一致に到達しなかった。
同じ理由でND4Lはnd4となり、COIIIや長いsubunit III表記もcoiになった。
ND4もキーの宣言順を逆にするとnd4lになった。

修正後は全マーカーの完全一致を先に評価する。
完全一致がないときだけ、英数字の単語境界を守ってaliases、phrases、keyを照合する。
marker_codesは完全一致だけに使用する。
一致先が複数ある場合はNoneを返し、宣言順で推測しない。
COI-5P-like、16S ribosome、COX2、COX3、COXIII、ND5-0、長い遺伝子名を維持した。

## 840レコードの混同行列

7ファイルのうち2ファイルは空で、残る5ファイルに計840行があった。
全12種類のmarker_codeに期待する遺伝子を手動定義し、基準コミットの実装と修正後を同じ行で評価した。
表の各行が期待値、修正前の予測、修正後の予測の非ゼロセルを表す。
認識されたmarker_code別件数は前後で同じである。

| marker_code | 期待遺伝子 | 修正前 | 修正後 | 件数 |
|---|---|---|---|---:|
| COI-5P | coi | coi | coi | 742 |
| COII | coii | coi | coii | 11 |
| COXIII | coiii | coiii | coiii | 11 |
| CYTB | cytb | cytb | cytb | 11 |
| ND1 | nd1 | nd1 | nd1 | 9 |
| ND2 | nd2 | nd2 | nd2 | 9 |
| ND3 | nd3 | nd3 | nd3 | 9 |
| ND4 | nd4 | nd4 | nd4 | 9 |
| ND4L | nd4l | nd4 | nd4l | 9 |
| ND5-0 | nd5 | nd5 | nd5 | 9 |
| ND6 | nd6 | nd6 | nd6 | 9 |
| atp6 | atp6 | atp6 | atp6 | 2 |

marker_key別ではcoiが753→742、coiiが0→11、nd4が18→9、nd4lが0→9となった。
他のキーは表の件数で変化がない。
誤分類から正しい分類への移動は20件、新規除外と既認識から認識不能への移動はともに0件だった。
個々の選択キーに独立して照合し、複数キーに一致した行は29→0件だった。
修正前の29件はCOI-5Pのような通常の表記ではなく、COII 11件、ND4 9件、ND4L 9件である。
ND4は旧実装でnd4lの候補にも部分一致していた。

全ファイルのSHA-256とmtime_nsは比較前後で一致した。
保存した結果は[JSON](bold-cache-audit-v3.3.6.json)にある。
同じ比較は以下で再実行できる。

```sh
git show 21dc64b:taxondbbuilder/bold_api.py > /tmp/bold_api_before_336.py
.pixi/envs/dev/bin/python tools/bold_cache_audit.py \
  test/.taxondbbuilder-cache/gb/.cache/bold \
  --markers configs/markers_mitogenome.toml \
  --baseline-source /tmp/bold_api_before_336.py
```

`--baseline-source`は信頼できるローカルPythonソースを読み込んで実行する。
未知のmarker_codeがあれば比較を失敗させ、未評価の遺伝子を正解として数えない。

## 分類群スコープの確認

旧実装では種名入力に対するtax:genusや別種のtax:speciesを受け入れた。
geo語のみ、空、解釈不能の応答も元の検索語へのフォールバックでsummaryへ進んだ。
追加テストでこれらの経路を再現してから修正した。

| 入力と応答 | 修正後 |
|---|---|
| 種名→同一種のtax:species | 許可 |
| 種名→tax:genus | ambiguous_taxon_scopeとして停止 |
| 属名→同一属のtax:genus | 許可 |
| 種名→別種のtax:species | ambiguous_taxon_scopeとして停止 |
| 同一分類群のtaxとidsの混在 | idsを除いて検索 |
| idsのみ | 未解決として停止、元の語を理由に保持 |
| geoなど分類群を含まない応答 | ambiguous_taxon_scopeとして停止 |
| 空、解釈不能な応答 | ambiguous_taxon_scopeとして停止 |

比較時は大文字小文字と連続空白を正規化する。
別名への変更は同義語かもしれないが、preprocessorの語だけではNCBI分類群との同一性を証明できない。
不正な学名と断定せず、元の応答を理由に含めて未解決としてログとmanifestへ残す。
NCBIでの学名解決は従来どおり先行するため、そこで正式名へ正規化できる同義語は引き続き正式名で検索できる。
BOLD側だけの同義語対応を自動推測する機能は追加していない。

`record.taxid = resolved_taxon.taxid`はNCBIで解決した検索対象のTaxIDをレコードへ付ける処理である。
BOLDからNCBI TaxIDを取得して検証する処理ではない。
属入力では属のTaxIDを検索スコープとして保持し、各レコードの種名を属名で上書きしない。
`organism_taxid`には行のtaxid等を別フィールドで保持するが、値の命名体系がNCBIかどうかを検証してはいない。
利用側は`taxid`を個体の種レベルTaxID、`organism_taxid`を検証済みNCBI IDとみなしてはならない。
今回の対策により、種入力を属検索へ広げて別種の配列に指定種TaxIDを付ける再現経路は止まる。
BOLD内の標本誤同定や応答と行の分類情報の不一致まで証明するものではない。

## テストと品質確認

修正前に新しいテストの14ケースが失敗した。
前回レビュー時のPythonテストは370件成功し、従来337件の対象も維持した。
指定されたマーカー10ケース、キーと定義の逆順、共有別名の曖昧性、遺伝子名の境界、既存の柔軟表記、分類群応答8種類と欠損応答、同義語の曖昧な扱い、キャッシュ比較の非変更性を検証した。
旧テストが固定していたCOII誤分類と、種入力をTestus属へ広げるfixtureは正しい期待値へ変更した。

前回Nodeテストは4ファイルを4件として報告していた。
リリース直前に同じコマンドをsandbox外で再実行し、実際の33ケースが成功することを確認した。
Rustテスト26件、Playwright 60件、Vite buildは成功した。
Playwrightはsandbox内でローカルポートのlistenがEPERMとなったため、同じテストをsandbox外で実行した。
Golden照合は全Pythonテストに含まれ、Goldenファイルの差分は0件だった。
BOLD GoldenのCOI-5Pとunknownは今回の修正で認識結果が変わらない。
実キャッシュの20件の修正はGolden fixtureに含まれておらず、独立した回帰テストと840件の比較で検証した。
バージョン8宣言とUser-Agentは3.3.6で一致した。
PyInstaller 6.21.0でfrozen sidecarを再ビルドし、--from-gbでオフライン実行した。
FASTAとsource_merge、acc_organismのCSVは既存Goldenと一致した。
CSVのCRLFは通常のテキスト読み込みでLFへ正規化して比較した。
今回のfrozen実行ではBOLD通信を行わず、既存のfrozenバージョン解決テストも維持した。

pyflakesは実行したが、既存のstar import、未使用import、ncbi.pyのfieldシャドーイングにより終了コードは1となる。
変更による新しい警告はなく、既存の静的検査テストで未定義名と再定義の致命的指摘がないことを確認した。
cccc 1.8.0は本体、tools、GUIのソース40ファイル828関数を解析し、解析エラー0件だった。
cognitive最大30、cyclomatic最大19で、閾値の指定はしていない。
変更関数のcognitive/cyclomaticはprepare_bold_queryが7/8→16/16、_marker_matchesが5/8→4/5、resolve_marker_from_recordが16/8→25/14となった。
追加したcompare_cacheは30/16である。

## 統合判断と未検証事項

オフラインで再現した誤分類の修正としてmainへの統合は可能と判断する。
この判断は実APIでの取得復旧や、全標本の同定精度の保証を意味しない。
実APIでのpreprocessorによる同義語対応、階級変換、検索条件の実際の解釈、返却行の分類一貫性、403の復旧とdownload成功は今回未検証である。
HTTP 403の伝播と診断、HTTP別再試行、未解決時の検索停止と記録、source=bothのfail-fastは既存テストで維持した。
mainへのマージ、push、タグ作成、Release公開は行わない。

## リリース直前の品質改善

基準コミットは`e0e4f58`。
マーカー判定の候補収集、照合パス、曖昧性判定をそれぞれの関数へ分離し、完全一致優先のアルゴリズムを維持した。
監査は信頼する基準ソースの読み込み、入力コードの検証、ファイル状態の記録、コード別比較と集計に分離した。
同じmarker_codeは出現件数を重みとして集計し、重複行を含むテストで件数を確認した。
未知のコードを拒否した場合も入力ファイルの内容とmtimeを変更しないテストを追加した。

| cccc 1.8.0のcognitive complexity | 変更前 | 変更後 |
|---|---:|---:|
| resolve_marker_from_record | 25 | 4 |
| 候補収集 | 同関数内 | 11 |
| 完全一致または境界付き部分一致のパス評価 | 同関数内 | 5 |
| 曖昧性判定 | 同関数内 | 1 |
| compare_cache | 30 | 0 |
| 入力コード検証と件数集計 | 同関数内 | 7 |
| コード別比較とレポート生成 | 同関数内 | 14 |
| 本体とGUIの最大 | 25 | 23 |
| toolsも含めた最大 | 30 | 23 |

以下の閾値付き実行は終了コード0で、41ファイル843関数、解析エラー0件だった。
cyclomatic最大は19である。

```sh
cccc taxondbbuilder tools tauri-gui/src tauri-gui/src-tauri/src \
  taxondbbuilder.py taxondb_bold.py --max-cognitive 23
```

Nodeのテストファイルとnpm testの定義はv3.3.5から変更されていない。
sandbox内の通常の`node --test`ではファイル単位の4件のみが報告されるが、sandbox外で同じ`npm test`を実行すると33ケースが成功した。
隔離を無効化したsandbox内の`node --test --test-isolation=none --test-reporter=tap`でも33ケースが成功した。
v3.3.5のsrc、tests、package.jsonを一時ディレクトリへ展開し、同じsandbox外の`npm test`で33件成功することも確認した。
前回の4件という報告はケース数として不適切だったため、33件に訂正する。
テスト対象の削除やnpm scriptの変更は行っていない。

AppImage用sidecarをPyInstaller 6.21.0で再ビルドした。
`dist/taxondbbuilder`と`tauri-gui/src-tauri/bin/taxondbbuilder-x86_64-unknown-linux-gnu`はバイト単位で同じである。
検証対象のSHA-256は`fd8c1d76d5c5966e05ca93bf295f2a6c8cf354417ceaa3116a37fed9edfa5078`。
`tools/frozen_bold_check.py`はローカル偽BOLDから5行のTSVを返し、ビルド済みバイナリの実際のFASTAとsource_merge.csvを照合する。
NCBIの学名解決もローカルHTTPSプロキシと子プロセスだけが信頼する一時証明書で代替する。
プロキシは外部へ中継せず、指定したNCBIホスト以外のCONNECTを拒否する。

| 偽BOLD marker_code | 一括選択のmarker_key | 単独選択時 |
|---|---|---|
| COI-5P | coi | coi選択で出力 |
| COII | coii | coi選択で除外 |
| COXIII | coiii | coi、nd4選択で除外 |
| ND4 | nd4 | nd4選択で出力 |
| ND4L | nd4l | nd4選択で除外 |

3回のCLI実行はすべて終了コード0で、一括選択5配列、coi選択1配列、nd4選択1配列が出力された。
ローカル通信は計15件で、外部リクエストは0件だった。
全BOLDリクエストのUser-AgentはTaxonDBBuilder/3.3.6だった。
機械可読な結果は[frozen sidecar検証JSON](frozen-bold-v3.3.6.json)にある。
既存の出力ディレクトリを上書きしないため、再実行時は未作成のパスを指定する。

```sh
.pixi/envs/dev/bin/python tools/frozen_bold_check.py \
  tauri-gui/src-tauri/bin/taxondbbuilder-x86_64-unknown-linux-gnu \
  --output-dir /tmp/taxondbbuilder-frozen-bold-check
```

最終品質確認ではpytest 371件、Node 33件、Rust 26件、Playwright 60件が成功し、Vite buildも成功した。
Goldenファイルの差分は0件、バージョン8箇所とUser-Agentは3.3.6で一致している。
pyflakesは既存警告による終了コード1のままだが、変更ファイルをe0e4f58と比較し、新規警告がないことを確認した。
840件の監査JSONは既存の保存結果と全項目で一致し、20件修正、新規除外0件、認識不能化0件、複数候補29→0件を維持した。
SHA-256とmtimeも全7ファイルで以前の保存値と一致している。

この結果からmainへの統合は可能と判断する。
実BOLD APIの同義語対応や403復旧、実ダウンロードは引き続き未検証である。
今回もマージ、push、タグ作成、Release公開は行っていない。
