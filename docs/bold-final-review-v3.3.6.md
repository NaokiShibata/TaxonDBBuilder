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
最終Pythonテストは370件成功し、従来337件の対象も維持した。
指定されたマーカー10ケース、キーと定義の逆順、共有別名の曖昧性、遺伝子名の境界、既存の柔軟表記、分類群応答8種類と欠損応答、同義語の曖昧な扱い、キャッシュ比較の非変更性を検証した。
旧テストが固定していたCOII誤分類と、種入力をTestus属へ広げるfixtureは正しい期待値へ変更した。

Nodeテスト4件、Rustテスト26件、Playwright 60件、Vite buildは成功した。
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
