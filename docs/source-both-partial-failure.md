# source=both で BOLD が失敗したときの部分成功 (設計案)

**状態: 未実装。** v3.3.6 では実装しない。実装は v3.3.7 以降の判断。

## 1. 問題

`--source both` の実行は NCBI → BOLD の順に走り、BOLD が失敗すると
`typer.BadParameter` で終了コード 2 になる (`_run_source_stages` → `_run_bold_stage`)。
NCBI が完走していたとしても、FASTA・`source_merge.csv`・manifest は merge 以降なので
1 つも書かれない。spool は `tempfile.TemporaryDirectory` にあるため、NCBI の**解析済み
レコードは例外時に捨てられる**。残るのは実行ログと、`--dump-gb` の GenBank キャッシュと
BOLD ダウンロードキャッシュだけで、`--resume` は後者を利用できる。

BOLD が 403 で全滅するような期間、`both` のビルドは 1 本も成果物を出さない。

## 2. 決定

- **既定は現状の fail-fast を維持する。** 何もしない実行の挙動は変わらない。
- **明示オプションを指定した実行だけ**、BOLD 失敗後に NCBI の結果で継続する。
- **BOLD を黙ってスキップする経路を作らない。** 継続した事実は manifest とログに残る。

## 3. オプション

```
--on-bold-failure fail-fast|continue-ncbi     (既定: fail-fast)
```

- 列挙値は既存の `--export-format` などと同じ typer の `Enum` パターンに揃える
- GUI はチェックボックスを持たず、この 1 つの宣言的オプションへ 1 対 1 で写す
  (`continue-ncbi` を選ぶか否か)。sidecar が stdin で止まる設計にはしない
- 終了コード: `continue-ncbi` で BOLD が落ちた場合は **0 ではない別コード** (例 3) を返し、
  かつ成果物は書かれている。失敗の合図と成果物の有無を分離する

## 4. manifest への記録

既存の `schema_version: 1` を上げず、`sources` テーブルを**追加**する
(既存キーの意味は変えない。manifest を読んでいるのは Python 側のみで、Rust は読まない):

```json
"sources": {
  "requested": ["ncbi", "bold"],
  "completed": ["ncbi"],
  "failed":    [{"source": "bold", "stage": "download", "status": 403,
                 "reason": "BoldApiError"}]
}
```

- `requested` は `--source` の値そのもの。`completed` に無い source は成果物に寄与していない
- `failed[].reason` には `taxon_unresolved` は入れない (あれは失敗ではなく未解決であり、
  学名ごとに `queries.bold[].resolved = false` へ既に記録されている)
- 不完全な成果物はファイル名でも区別する。`<prefix>__partial-nobold.fasta` のようにし、
  通常の `both` 成功と同一パスを上書きしない

## 5. source_merge.csv と resume の整合性

- `source_merge.csv` はアクセス番号と種の対応表なので、NCBI 分だけを書く。BOLD 由来的な
  行は生成しない (空の BOLD 列を作らない)
- resume はキャッシュ単位で効く。BOLD のダウンロードキャッシュは正規化クエリ鍵なので、
  次回 `fail-fast` で実行すれば NCBI を再取得せずに BOLD だけ再試行になる
- 継続実行の成果物を resume の入力にしないこと。部分成果物を正規の `both` 出力として
  扱う経路を作ると、後から BOLD が揃ったときに区別できなくなる

## 6. 各方式の評価

| | データ完全性 | 再現性 | 部分成果物 | resume | 実装コスト |
|---|---|---|---|---|---|
| A 現状維持 | 最強 (完全 or 無) | 完全 | 出さない | 良好 | — |
| **B 明示オプション** | 良好 (要ラベル) | 良好 (旗が宣言的) | 明示的に区別 | 良好 | 小 (CLI 1 値 + manifest 追記) |
| C GUI で都度選択 | B と同じ | 弱い (対話依存) | B と同じ | B と同じ | 中 (GUI + sidecar 通信) |

C 単独は採らない。sidecar は対話で止められないため、選択は実行**前**の宣言になる。
B を CLI プリミティブとして作り、GUI はその旗へ写すのが最小構成。

## 7. 実装時に必要になるもの (今回やらない)

- `--on-bold-failure` の enum と引数検証
- `_run_source_stages` の分割 (source ごとに成否を収集して merge 可否を判定)
- manifest の `sources` キーと、`partial-nobold` の命名規則
- 終了コード 3 のドキュメント化 (GUI の失敗表示が終了コード 2 前提なら同時に確認)
- e2e: BOLD 失敗時に Results 画面が「NCBI のみ」を正しく表示すること
