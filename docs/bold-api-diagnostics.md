# BOLD API 疎通診断とサポート問い合わせ

`TaxonDBBuilder` の BOLD 取得で `BOLD HTTP error 403: Forbidden` が出た際の記録と、
アクセスが許可された後に実行する疎通確認の手順をまとめる。

## 1. 再現記録 (2026-10-14)

対象学名 `Tachysurus ichikawai`。アプリと同じリクエスト形状 (GET、`Accept: application/json`、
`Accept-Encoding: gzip`、`User-Agent: TaxonDBBuilder/0.1 (+https://github.com/NaokiShibata/TaxonDBBuilder)`) で実行した。

| UTC 時刻 | 要求 | 結果 |
|---|---|---|
| 2026-10-14 04:58:59 | `GET /api/query/preprocessor?query=tax%3ATachysurus+ichikawai` | 403 |
| 同 04:59 台 (同セッション) | `GET /api` (API ルート) | 403 |
| 同 04:59 台 | preprocessor、`User-Agent: Python-urllib/3.12` | 403 |
| 同 05:00 台 | preprocessor、数秒後に再試行 | 403 |

4 件すべての応答:

```
HTTP/1.1 403 Forbidden
server: cloudflare
cf-ray: a4a4031b1bb1d3f2-KIX / a4a404069f4d17bf-KIX / a4a4042caf4b006e-KIX / a4a40452ad6b511e-KIX
content-type: text/html; charset=UTF-8
content-encoding: gzip
Retry-After: (無し)

<title>Attention Required! | Cloudflare</title>
<h1>Sorry, you have been blocked</h1>
You are unable to access boldsystems.org
```

要点は 3 つ。**API ルートでも同じ** (特定エンドポイントではない)、**User-Agent を変えても同じ**、
**数秒後の再試行でも同じ**。エンドポイントとパラメータは BOLD の公開ドキュメント
(`/api/query/preprocessor`、`/api/summary`、`/api/query`、`/api/documents/<token>/download`) と一致している。

## 2. BOLD 公式サポートへの問い合わせ文 (送信しない)

`support@boldsystems.org` 宛の想定。実際の送信は行わない。CF-RAY は上の再現記録の値。

```text
Subject: HTTP 403 from Cloudflare on portal.boldsystems.org API (CF-RAY a4a4031b1bb1d3f2-KIX)

Dear BOLD Support,

We are calling the BOLD Portal API from TaxonDBBuilder, an open-source tool that
builds reference databases from public BOLD and NCBI data. We are using the
documented public endpoints only, with a single taxon per query and no
concurrent or repeated requests:

  GET /api/query/preprocessor?query=tax:Tachysurus%20ichikawai
  GET /api/summary?query=...&fields=specimens&reduce_operation=count
  GET /api/query?query=...&extent=full
  GET /api/documents/<query_id>/download?format=tsv

Since at least 2026-10-14 04:58:59 UTC, every request to portal.boldsystems.org
returns HTTP 403 Forbidden served by Cloudflare, with the "Attention Required! |
Cloudflare / Sorry, you have been blocked" page instead of an API response.
The block is not limited to one endpoint: a plain GET to /api is blocked the same
way. In an earlier run the same day, the first three stages succeeded and only
/api/documents/<query_id>/download returned 403, so the block does not always
appear at the same stage.

CF-RAY identifiers observed in the same session, all HTTP 403:

  a4a4031b1bb1d3f2-KIX   GET /api/query/preprocessor?query=tax:Tachysurus%20ichikawai
  a4a404069f4d17bf-KIX   GET /api
  a4a4042caf4b006e-KIX   GET /api/query/preprocessor (default Python user agent)
  a4a40452ad6b511e-KIX   GET /api/query/preprocessor (retry a few seconds later)

What we have already ruled out on our side:

  - The request shape matches your published API documentation.
  - The same 403 occurs with the default Python user agent, so the user agent
    string does not appear to be the trigger.
  - A retry a few seconds later is blocked identically, so this does not look
    like a short rate-limit window.
  - We are sending one request at a time, for one taxon, with no scraping
    behaviour.

Could you tell us:

  1. Whether our network or IP range is currently blocked by your Cloudflare
     configuration, and whether it can be reviewed for the CF-RAY values above.
  2. Whether the Portal API requires anything we are not sending (for example a
     specific header, an API key, or a session cookie) for the
     /api/documents/<query_id>/download endpoint in particular.
  3. The expected rate limits and request pattern for programmatic use of the
     Portal API, so we can stay inside them.

We would rather follow your guidance than work around the block, so we have not
attempted to bypass it. Thank you for your help.

Kind regards,
TaxonDBBuilder maintainers
https://github.com/NaokiShibata/TaxonDBBuilder
```

## 3. アクセスが許可された後の疎通・取得確認

`tools/bold_api_check.py` を使う。**1 学名につき最大 4 リクエスト** (preprocessor /
summary / query が 3、download が 1) で、ダウンロード成果物は `--out` 以下にしか書かない。

```bash
.pixi/envs/dev/bin/python tools/bold_api_check.py --taxon "Tachysurus ichikawai" --format tsv --max-rows 50
```

診断中は `--retries 0` が既定 (失敗 1 回 = リクエスト 1 回)。JSON も確認する場合は
`--format json` で**別実行**する (同じトークンでの二重ダウンロードを避けるため)。

**転送量の上限**は `--max-specimens` (既定 2000) で決める。`--max-rows` は解析件数の
上限で、HTTP 転送は先に完了するため転送量は制限しない。ダウンロードエンドポイントには
件数を制限するパラメータがないため、`specimen_count` が `--max-specimens` を超える場合は
**ダウンロードを開始せずに中止**する (終了コード 2)。BOLD が `specimen_count` を返さなかった
場合も、転送量が読めないため既定では中止する。受け入れると判断した場合だけ
`--allow-unknown-count` を付ける。

期待される出力:

```
stages 1-3 ok: normalized='taxon:name:Tachysurus ichikawai' specimens=1234 token_len=44
stage 4 ok: bytes=987654 content_length=987654 path=/tmp/bold-api-check/check.tsv
stage 5 ok: rows_read=50 (capped at 50) markers={'COI-5P': 50}
```

各段階が証明すること:

| 段階 | 確認できること |
|---|---|
| 1 preprocessor | 検索語が BOLD の語彙で解決されるか (`normalized_query` が生クエリと違うはず) |
| 2 summary | 件数が返るか (`specimens`。0 なら NCBI 名と BOLD 名の不一致)。ここで得た件数が転送上限の判定に使われる |
| 3 query | トークンが取得できるか (`token_len`。ログには長さとホスト + パスしか出ない) |
| 4 download | TSV / JSON が降りるか、gzip 展開と進捗が成立するか |
| 5 解析 | ヘッダ行と `marker_code` が読めるか (TSV と JSON で行抽出経路が違う) |

失敗した段階ごとの切り分け:

- **1〜3 で 403 + `server=cloudflare`** → BOLD / Cloudflare 側のブロック。実行ログの `# bold error:` 行の `cf_ray` を §2 の文に添えて問い合わせる
- **4 だけで 403** → トークン関連の可能性。`endpoint=...<redacted:N>...` の `N` が §1 の 44 と一致するかを確認する (一致しないならトークン生成側の問題)。トークン内の `%3D` が WAF に影響する仮説は未検証のまま
- **`status=429`** → BOLD のレート制限。ログの `retry_after=` が待機秒数で、60 秒を超えると TaxonDBBuilder は待たずに失敗する。間隔を空けて再実行し、`--resume` でキャッシュを再利用する
- **`status=500/502/503/504`** → BOLD の障害。TaxonDBBuilder は 4 回まで再試行済みなので、失敗していれば時間を置いて再実行する
- **5 だけ失敗** → 通信ではなく解析側の問題。`--format` を変えて比較し、TSV ヘッダの変化を確認する

## 4. 種ごとの 403 切り分け (アクセス復旧後に実施)

目的は「Cloudflare がホスト全体を拒否しているのか、特定学名のクエリだけが拒否される
のか」を判定すること。**学名ごとに別実行**にする (1 学名 = 最大 4 リクエスト、
`--retries 0` なので失敗 1 回 = 1 リクエスト)。比較対象はキャッシュ実測で役割が違う 3 種:

| 学名 | 選ぶ理由 | 期待する観測 |
|---|---|---|
| `Silurus asotus` | preprocessor が `tax:species:` に解決でき、99 件取れた対照群 | 段階 1〜3 が通り、`marker_code` に COI-5P が並ぶ (12S は 0 件) |
| `Silurus biwaensis` | 2 件しかなく、転送量を気にせず段階 4・5 まで見られる | `specimens=2`、download と解析まで完走 |
| `Tachysurus ichikawai` | 403 を最初に再現した学名 | ここだけ失敗するなら学名依存、全種同じならホスト全体 |

```bash
for t in "Silurus asotus" "Silurus biwaensis" "Tachysurus ichikawai"; do
  .pixi/envs/dev/bin/python tools/bold_api_check.py --taxon "$t" \
      --format tsv --max-rows 20 --out "/tmp/bold-check-${t// /_}" \
      2>&1 | tee -a /tmp/bold-check-summary.txt
done
```

**1 種目の途中で失敗したらループを止める** (`set -e` を付ける、または手動で中止)。
段階 1 で 403 が出た時点で以降の種も同じである可能性が高く、リクエストを無駄にしない。

判定表:

| 観測 | 判定 | 次の行動 |
|---|---|---|
| 3 種とも段階 1 で `status=403` + `server=cloudflare` | ホスト全体のブロック (2026-10-14 の状態が続いている) | §2 の問い合わせ文に各 `cf_ray` を追記して送る |
| `Silurus` 2 種は通り、`Tachysurus ichikawai` だけ 403 | 学名依存。クエリ本文が WAF に触れている | 段階 1 の `normalized_query` と URL エンコードを種で突き合わせる |
| 段階 1〜3 は通るが段階 4 だけ 403 | トークン (URL 内 `%3D`) 依存 | §3 の `<redacted:N>` の長さを実測トークン長と比較 |
| 全種とも通る | ブロックは解消 | `cf_ray` を記録して解除を記録。ビルドを 1 本通して `matched` を確認 |

段階 1 の `normalized_query` は必ず控える。`tax:species:<学名>` ではなく
`ids:processid:...;ids:sampleid:...;ids:insdcacs:...` が返ってきた場合、BOLD がその学名を
分類群として解決できていない (2026-10-12 の `Silurus tomodai`、2026-10-14 の
`Tachysurus nudiceps` で実測)。これは 403 とは無関係な、**0 件になる別経路**である。

## 5. 調査結果 (2026-10-14、v3.3.5 / `investigate/bold-403-and-cache`)

`tools/bold_cache_audit.py` が `test/.taxondbbuilder-cache/gb/.cache/bold/` を読み取りで
集計した結果 (元ファイルは未変更)。

### 5.1 キャッシュされた BOLD データに 12S は存在しない

| キャッシュファイル | 行数 | 配列あり | marker_code |
|---|---|---|---|
| `query-8d219a9740d4127c.tsv` (Silurus asotus) | 117 | **117/117** | COI-5P 99, COII 3, COXIII 3, CYTB 3, atp6 2, ND1/2/3/4/4L/5-0/6 各 1 |
| `query-24710dc221c988b0.tsv` (2026-09 run) | 127 | 127/127 | COI-5P 77, COII/COXIII/CYTB/ND1/ND2/ND3/ND4/ND4L/ND5-0/ND6 各 5 |
| `query-a9adf226e126a1c6.tsv` (2026-08 run) | 590 | 590/590 | COI-5P 560, 上記の各ミトコンドリア遺伝子 3 |
| `query-cbf1aee251a02516.tsv` (Silurus lithophilus) | 4 | 4/4 | COI-5P 4 |
| `query-f2c779584d0fb775.tsv` (Silurus biwaensis) | 2 | 2/2 | COI-5P 2 |
| 0 バイト 3 ファイル | 0 | — | summary が 0 件だった学名 (`Silurus tomodai` など) |

**`marker_code` が空の行は 0 行、配列欠損の行も 0 行**。`12S` は 1 行も存在しない。
よって `Silurus asotus` の `downloaded=117 matched=0` は、12s を要求したときの**正しい
結果**であり、解析の欠陥ではない (除外理由は全行 `marker_code` 不一致)。

### 5.2 ただし COXIII は設定ギャップで落ちている

BOLD の実際のコードは `COXIII`。`markers_mitogenome.toml` の `coiii` は
`aliases = ["coiii", "cox3"]` / `phrases = ["COIII", "COX3"]` のみで、`marker_codes` を
持たない。照合は (a) `bold.marker_codes` は完全一致、(b) aliases / phrases は部分一致で、
`COXIII` は `COIII` とも `COX3` とも一致しない。実データ例:
`processid=CYTC5134-12`、`species=Silurus asotus`、配列 781 bp — ミトコンドリア全遺伝子
実行でも取りこぼし (3 行)。

### 5.3 preprocessor は学名を `ids:` 語に書き換えることがある

BOLD 公式ドキュメントのとおり preprocessor は略式語を正式 triplet に変換する段階
(`tax:Aves` → `tax:class:Aves`)。実測:

| 学名 | preprocessor が返した語 | summary |
|---|---|---|
| Silurus asotus | `tax:species:Silurus asotus` | 99 件 |
| Silurus biwaensis | `tax:species:Silurus biwaensis` | 2 件 |
| Silurus lithophilus | `tax:species:Silurus lithophilus` | 4 件 |
| **Silurus tomodai** | `ids:processid:...;ids:sampleid:...;ids:insdcacs:...` | **0 件** |
| **Tachysurus nudiceps** | `ids:processid:...;ids:sampleid:...;ids:insdcacs:...` | **0 件** |

学名が BOLD の分類群語彙で解決できないとき、preprocessor は同じ文字列を ID 検索語に
割り当てる。`prepare_bold_query()` は返された語をそのまま `normalized_query` に採用し、
`specimen_count != 0` の判定を通過すれば `/api/query` まで送信する。CRAN の公式クライアント
BOLDconnectR は (1) `ids:` 語を含んだ時点でエラー、(2) 語ごとの件数が 0 ならそこで中止、
(3) 0 件語を `/api/query` に送らない、の 3 つの防御を持つ。**TaxonDBBuilder は 3 つとも
持たない** (`tests/test_bold_query_normalization.py` の strict xfail 3 件がこれを固定)。

`tax:species:<学名>` を直接組めば `ids:` 化は防げるが、BOLD が genus ランクに解決してくれる
利点も失う (例: `tax:Aves` は `tax:class:Aves` になる)。推奨は略式語のまま、返ってきた語の
scope を検証する経路。

### 5.4 source=both で BOLD が 403 になるときの現状

`_run_source_stages()` は NCBI → BOLD の順で、BOLD の `BoldApiError` を
`typer.BadParameter` に変換して終了コード 2 で抜ける。spool は `tempfile.TemporaryDirectory`
の中にあるため **NCBI の解析済みレコードは例外時に捨てられる**が、`--dump-gb` の GenBank
キャッシュと BOLD ダウンロードキャッシュはディスクに残り、`--resume` で再利用できる。
FASTA・`source_merge.csv`・manifest は merge 以降なので 1 つも書かれない。

## 6. 「ホスト全体ブロック」は言い過ぎだった (2026-10-14 追記)

v3.3.5 の Known issues は「`portal.boldsystems.org` の全パスが Cloudflare にブロックされ」
と書いているが、**保存済み実行ログはその結論と矛盾する**。`# bold progress:` 行は各段階の
HTTP が 200 を返した後にしか出ないことを使うと、失敗した段階が復元できる:

| 実行 (2026-10) | 学名 | preprocess | summary | query | download | 完走 |
|---|---|---|---|---|---|---|
| 12 / job1・job5・job6 | Silurus 4 種 | 200 | 200 | 200 | 200 | **した** (manifest あり) |
| 14 / job2 (13:32) | + Tachysurus 3 種 | 200 | 200 | 200 | **失敗** | せず (manifest なし) |
| 14 / job3 (13:35) | 同上 | 200 | 200 | 200 | **失敗** | せず (manifest なし) |

10/14 の 2 実行は `Tachysurus ichikawai` の `phase=download` 行でログが終わり、`# bold query:`
締めの行がない。`phase=download` は `submit_query()` がトークンを取得した後にしか出ない
(`bold.py` の `# bold progress: ... phase=download` → `_fetch_bold_documents()`)。したがって
**preprocessor・summary・query は 200 で、落ちたのは `/api/documents/<token>/download` だけ**。

一方で 2026-10-14 の夕方以降、当方の診断実行 (`urllib`、既定 UA と TaxonDBBuilder UA の両方) は
`/api/query/preprocessor` でも API ルートでも 403 を受けた。つまり:

- ブロックは**時点によって変化する** (13:31〜13:35 は 4 段階中 3 段階が通った)
- 10/14 のユーザー実行で再現したのは **download 段階に限定された 403**
- download URL のトークンは `quote(query_id, safe="")` で `=` が `%3D` になる (§3 の仮説)。
  **この仮説が初めて実データと整合した** — 他の 3 段階はトークンを含まず、トークンを含む
  のは download だけ
- ただし「download 限定」を確定的とは呼べない: 10/14 の実行は 2 回だけで、その前後の
  ホスト全体の状態を同じクライアントから観測していない

**復旧後に最初に確認すべきこと**: §4 の 3 種比較で、403 がどの段階で出るかを種ごとに記録する。
`Silurus asotus` が段階 4 まで通るなら download 固有、段階 1 で止まるならホスト全体に悪化した
と判定できる。判定表の 3 行目 (段階 4 だけ 403) が最有力のケースになった。
