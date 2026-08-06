# Plan.md

## 1. ゴール

`TaxonDBBuilder` を、既存の **NCBI/GenBank ベースの FASTA 生成フローを壊さず** に、BOLD Data Portal からも同じ marker 指定で配列取得できるよう拡張する。

今回の実装方針は次の 5 点に要約する。

1. **marker 指定は source 非依存** にする  
   `-m coi` / `-m 12s` などの指定は NCBI / BOLD の両方に適用する。
2. **source 間統合は registration strict** にする  
   `BOLD.insdcacs` と `NCBI.accession` が直接一致する場合のみ link とみなす。
3. **配列一致や taxon 名一致だけでは統合しない**  
   sequence hash / species name / length 一致は統合根拠に使わない。
4. **既存の NCBI only ワークフローを維持** する  
   現行の `build`, `list-markers`, `list-primer-sets`, `--dump-gb`, `--resume`, markers TOML, GUI 前提の出力を壊さない。
5. **BOLD は taxon で取得し、marker は client-side で絞る**  
   BOLD 側は marker を NCBI のような feature 抽出で扱わず、record の `marker_code` 等を見て採否判定する。

---

## 2. 現状理解

### 2.1 現行実装の中心

- 中心実装は `taxondbbuilder.py`
- 主要コマンドは `build`, `list-markers`, `list-primer-sets`
- 設定は `configs/db.toml` + 外部 markers TOML
- 現在の主フローは NCBI 前提で、検索と GenBank feature 抽出が強く結びついている

### 2.2 現行の重要な前提

- marker 定義は基本的に NCBI 向け (`phrases`, `terms`, `region_patterns`, `feature_types`, `feature_fields`)
- `build` は NCBI 取得・GenBank 抽出・FASTA 出力を一気通貫で行う
- `--dump-gb`, `--from-gb`, `--resume` は NCBI キャッシュ機構

### 2.3 守るべき互換性

- `build`, `list-markers`, `list-primer-sets` を維持
- 既存 `db.toml` / `markers_mitogenome.toml` をそのまま読める
- `--source ncbi` では既存出力・既存ログ・既存 post-prep を極力維持
- GUI が `--source` を渡さなくても従来通り動く

---

## 3. 実装の基本戦略

### 3.1 方針

- NCBI path は温存
- BOLD path は追加
- source 共通の内部 record 表現を導入
- source ごとの取得は分ける
- 最終出力直前に strict registration link 判定だけ行う

### 3.2 重要な設計判断

- **NCBI は検索 + feature 抽出**
- **BOLD は taxon query + record filter**
- 両者の差は `CanonicalRecord` で吸収する

### 3.3 今回やらないこと

- sequence hash ベース統合
- taxon 名だけを根拠にした統合
- BOLD 用の独立 marker CLI
- BOLD キャッシュ機構 (`--dump-bold`, `--from-bold`) の新設
- mixed-source duplicate レポートの全面再設計
- 大規模 package 分割

---

## 4. 追加 CLI 仕様

### 4.1 `build` への追加オプション

```text
--source [ncbi|bold|both]
```

デフォルトは `ncbi` とする。

### 4.2 期待動作

- `--source ncbi`
  - 現在と同じ
- `--source bold`
  - BOLD のみ取得して FASTA を生成
- `--source both`
  - 同じ taxon / marker 指定を NCBI と BOLD に適用
  - BOLD `insdcacs` と NCBI accession の strict match のみで BOLD record を抑制

### 4.3 既存 NCBI cache オプションの扱い

- `--source ncbi`: 現在通り
- `--source both`: `--dump-gb`, `--from-gb`, `--resume` は **NCBI 側にのみ適用**
- `--source bold`: `--dump-gb`, `--from-gb`, `--resume` を指定したら **明示的にエラー** にする

---

## 5. source-aware validation を追加する

### 5.1 config validation

`load_config()` は source を見て検証を分岐させる。

- `source=ncbi`
  - 現在と同じく `[ncbi]` 必須
- `source=bold`
  - `[ncbi]` は不要
  - `[bold]` があれば読む
- `source=both`
  - `[ncbi]` 必須
  - `[bold]` は省略時デフォルト値で補う

### 5.2 marker validation

marker 定義検証は source-aware にする。

- NCBI を使う場合
  - `phrases` or `terms` が必要
  - feature 抽出に必要な `region_patterns` / fallback が必要
- BOLD を使う場合
  - `[markers.<id>.bold].marker_codes` を優先
  - 無ければ `aliases` / `phrases` / marker key を fallback として使う
- `both`
  - NCBI 用にも BOLD 用にも成立することを確認

---

## 6. 内部モデル

### 6.1 ResolvedTaxon

```python
@dataclass(frozen=True)
class ResolvedTaxon:
    input_value: str
    taxid: str
    scientific_name: str
    warning: Optional[str] = None
```

用途:
- numeric taxid 入力でも scientific name を取得
- BOLD query に scientific name を使う
- 既存 warning も保持する

### 6.2 CanonicalRecord

```python
@dataclass
class CanonicalRecord:
    source: str  # "ncbi" | "bold"
    source_record_id: str  # NCBI accession / BOLD processid
    accession: Optional[str]  # raw accession for matching
    processid: Optional[str]
    sampleid: Optional[str]
    taxon_name: Optional[str]
    marker_key: str
    marker_label: Optional[str]  # matched label / marker_code
    sequence: str
    header_values: Dict[str, str]
    metadata: Dict[str, str]
    linked_to_ncbi: bool = False
    emitted_to_fasta: bool = True
    skip_reason: Optional[str] = None
```

### 6.3 BuildSource

```python
class BuildSource(str, Enum):
    NCBI = "ncbi"
    BOLD = "bold"
    BOTH = "both"
```

---

## 7. BOLD 取得方針

### 7.1 API 利用フロー

BOLD は次の流れで扱う。

1. taxon query を組み立てる
2. `/api/query/preprocessor` で正規化
3. `/api/query` で `query_id` を取得
4. `/api/documents/<query_id>/download` で records を取得
5. record ごとに `marker_code` 等を見て marker 判定
6. `CanonicalRecord` に変換

### 7.2 重要な前提

- BOLD では marker を NCBI のように query 主体にしない
- **taxon で広く取得し、marker は client-side で絞る**
- query 構築の主軸は `scientific_name`
- strict link 判定には `insdcacs` を raw 値として保持する

### 7.3 BOLD query の初期方針

まずは以下を採用する。

- generic tax query:
  - `tax:<scientific_name>`
- 必要に応じて将来 rank 指定を拡張
- BOLD taxon query が 0 件のときは warning を出す
- NCBI scientific name と BOLD taxonomy の差異で 0 件になり得ることを log に残す

### 7.4 BOLD download format

第 1 段階では BCDM JSON を優先する。  
ただし大規模取得時にメモリ圧迫が大きい場合は TSV streaming へ切替可能な設計にする。

### 7.5 通信実装

新規依存は増やさず標準ライブラリで実装する。

- `urllib.request`
- `json`
- `gzip`

最低限入れるべき処理:
- timeout
- HTTP error handling
- retry with backoff
- gzip response 対応
- User-Agent 設定
- 1 query 1,000,000 件上限超過時の明示的エラー

---

## 8. marker 指定仕様

### 8.1 source 非依存 marker

`-m coi`, `-m 12s` などは source 共通で受ける。

### 8.2 NCBI での解釈

既存通り:
- `phrases`
- `terms`
- `region_patterns`
- `feature_types`
- `feature_fields`

### 8.3 BOLD での解釈

`[markers.<id>.bold]` を追加できるようにする。

例:

```toml
[markers."coi"]
aliases = ["coi", "co1", "cox1"]
phrases = ["COI", "CO1", "COX1", "cytochrome c oxidase subunit I"]
region_patterns = ["COI", "CO1", "COX1", "cytochrome c oxidase subunit I"]
feature_types = ["CDS", "gene"]
feature_fields = ["gene", "product", "note", "standard_name"]
header_format = "mifish_pipeline"

[markers."coi".bold]
marker_codes = ["COI-5P", "COI-3P", "COI"]

[markers."12s".bold]
marker_codes = ["12S"]
```

### 8.4 BOLD marker 判定順序

1. `marker_codes`
2. `aliases`
3. `phrases`
4. marker key

---

## 9. NCBI 側の変更

### 9.1 直接書き込み責務を分離

現状の `process_genbank_chunk()` はその場で FASTA に書く。  
これを次のように分割する。

- `extract_ncbi_records_from_genbank_chunk()`
  - GenBank chunk -> `CanonicalRecord` の list
- `emit_records_to_fasta()`
  - `CanonicalRecord` -> FASTA

### 9.2 目的

- BOLD と統合判定してから出力できる
- source 非依存 post-process がしやすい
- NCBI only path も保ちやすい

---

## 10. source 間統合ルール

### 10.1 strict rule

統合根拠として採用するのは **これだけ** とする。

```python
if bold.accession and bold.accession in ncbi_accessions:
    bold.linked_to_ncbi = True
    bold.emitted_to_fasta = False
    bold.skip_reason = "linked_by_insdcacs"
```

### 10.2 採用しない根拠

- sequence hash 一致
- taxon_name 一致
- marker 一致
- 配列長一致

### 10.3 出力順

再現性のため、最終出力順は固定する。

1. NCBI kept records
2. BOLD kept records

各群内では安定ソートする。  
例:
- `taxon_name`
- `marker_key`
- `source_record_id`

---

## 11. 出力 ID / header / sidecar

### 11.1 acc_id の方針

BOLD は accession の有無に関わらず、**常に source-namespaced な acc_id** を使う。

例:
- NCBI: `AB123456`
- BOLD: `BOLD_ASDCA001-24`

raw accession (`insdcacs`) は `accession` フィールドに保持する。

### 11.2 header_values

既存 template を壊さない範囲で埋める。

BOLD 例:
- `acc`: `insdcacs` があればそれ
- `acc_id`: `BOLD_<processid>`
- `organism`, `organism_raw`: BOLD taxon name
- `marker`, `marker_raw`: marker key
- `label`, `label_raw`: BOLD marker code
- `type`, `type_raw`: `barcode`
- `loc`, `strand`: 空
- `source`: `bold`
- `source_id`: `processid`

### 11.3 sidecar

既存 `*.fasta.acc_organism.csv` は列拡張で対応する。

既存列:
- `acc_id`
- `accession`
- `organism_name`
- `header`

追加列:
- `source`
- `source_record_id`
- `processid`
- `sampleid`
- `marker_key`
- `linked_to_ncbi`
- `emitted_to_fasta`
- `skip_reason`

新規:
- `*.fasta.source_merge.csv`

---

## 12. post-prep の扱い

### 12.1 維持するもの

- `primer_trim`
- `length_filter`

### 12.2 duplicate_report

第 1 段階では以下とする。

- `source=ncbi`: 現在通り
- `source=bold`: header から `acc_id` と organism が読めるなら実行可
- `source=both`: デフォルトで skip、明示的指定時のみ実行可でもよい

少なくとも **bold/both 一律スキップ** にはしない。  
理由: BOLD-only まで機能低下させる必要はないため。

---

## 13. メモリ・スケール戦略

### 13.1 全 record をメモリに持たない

BOLD は最大 1,000,000 件まで取り得るため、`CanonicalRecord` 全保持は避ける。

### 13.2 実装方針

- source ごとに JSONL or TSV の temp spool を作る
- NCBI kept records の accession set は別途保持
- BOLD spool を逐次読んで strict link 判定
- その場で final FASTA と sidecar を出力

これにより both でもメモリ使用量を抑える。

---

## 14. 実装順序

### Phase 1: source-aware 土台

- `BuildSource` 追加
- `ResolvedTaxon` 追加
- `build()` に `--source` 追加
- `load_config()` / validation を source-aware に変更
- `setup_entrez()` を source 条件付きに変更

### Phase 2: NCBI 出力責務の分離

- `process_genbank_chunk()` の record-return 化
- `emit_records_to_fasta()` 追加
- NCBI only 回帰確認

### Phase 3: BOLD 単体

- `taxondb_bold.py` 追加
- taxon query -> preprocess -> query -> download
- BOLD marker 判定
- `--source bold` で FASTA 生成

### Phase 4: both

- NCBI accession set 構築
- BOLD `insdcacs` strict 判定
- `source_merge.csv` 追加
- deterministic merge 出力

### Phase 5: 仕上げ

- sidecar 拡張
- post-prep 条件整理
- README / sample config 更新
- GUI 非破壊確認

---

## 15. テスト計画

### 15.1 NCBI only 回帰

確認:
- FASTA 件数
- `.log`
- `.acc_organism.csv`
- `primer_trim`
- `length_filter`

### 15.2 BOLD only

ケース:
- `-m coi --source bold`
- `-m 12s --source bold`

確認:
- FASTA が生成される
- `acc_id` が source-namespaced
- accession 欠損 record でも出力可能
- `source_merge.csv` に記録される

### 15.3 both

確認:
- `insdcacs == accession` の BOLD record は skip
- accession 無し BOLD record は保持
- sequence 一致だけでは skip しない
- 出力順が安定

### 15.4 config / validation

確認:
- `source=bold` で `[ncbi]` なしでも動く
- `source=ncbi/both` では `[ncbi]` 必須
- marker validation が source-aware に動く

### 15.5 BOLD API robustness

確認:
- timeout
- retry
- query 0 件
- query 上限超過
- malformed document
- `insdcacs` 欠損 / 複数値

---

## 16. 完了条件

以下を満たしたら完了とする。

- `python3 taxondbbuilder.py build -c configs/db.toml -t 117570 -m coi --source both` が動く
- marker 指定が NCBI / BOLD に共通適用される
- `insdcacs == accession` の BOLD record は final FASTA から除外される
- sequence 一致だけでは除外しない
- `--source ncbi` は既存と実質互換
- `--source bold` では `[ncbi]` が不要
- README / sample config が更新されている
