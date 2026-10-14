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
