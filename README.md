# TaxonDBBuilder

NCBI と BOLD Data Portal から任意の分類群・任意のマーカーの配列を取得し、DB用のFASTAを生成するツールです。分類群は **taxid / 学名** のどちらでも指定でき、マーカーは **TOMLで定義したフレーズ群** をprefix指定で呼び出せます。

CLI は `taxondbbuilder/` パッケージを実行します。リポジトリ直下の `taxondbbuilder.py` は既存の起動方法を維持する互換シムです。

GUI は Tauri アプリから Python sidecar を起動し、CLI と同じ Python パッケージで build と post-prep を実行します。GUI の詳細は[こちらの README](tauri-gui/README.md)を参照してください。

![](tauri-gui/figures/TaxonDBBuilderGUI.drawio.png)

## 動作環境

- Python 3.12+
- 主要依存: biopython, kalign-python, piqtree, rich, typer
- パッケージ管理: pixi (推奨)

### 環境構築 (pixi)

プロジェクト直下にpixiをインストールする例です。

```bash
export PIXI_VERSION="latest"
export PIXI_HOME="${PWD}/tools/pixi"
export PIXI_BIN_DIR="${PIXI_HOME}/bin"
export PIXI_CACHE_DIR="${PWD}/.cache/pixi"
export PIXI_NO_PATH_UPDATE=1

mkdir -p \
  "${PIXI_HOME}" \
  "${PIXI_BIN_DIR}" \
  "${PIXI_CACHE_DIR}"

curl -fsSL https://pixi.sh/install.sh | sh
# PATHへ追加 (bashの場合)
export PATH="${PIXI_BIN_DIR}:${PATH}"

# 動作確認
pixi --version
```

### ツールのインストール

リポジトリをクローンし、`pixi.toml` があるリポジトリルートへ移動します。

```bash
git clone https://github.com/NaokiShibata/TaxonDBBuilder.git
cd TaxonDBBuilder
```

実行に必要なパッケージをインストールします。

```bash
pixi install
```

開発用パッケージを含める場合は、`dev` 環境をインストールします。

```bash
pixi install -e dev
pixi run -e dev test
```

## クイックスタート

環境構築後から **GenBankキャッシュ付きの実行** までの手順です。

```bash
# 1) 設定（api_key / email を入力）
# configs/db.toml を編集

# 2) 実行（NCBI / GenBank キャッシュ保存）: Rhinogobiusを対象
pixi run python3 -m taxondbbuilder build -c configs/db.toml -t 63457 -m 12s --source ncbi --dump-gb Results/gb
```

再実行時にキャッシュを優先して使う場合

```bash
pixi run python3 -m taxondbbuilder build -c configs/db.toml -t 63457 -m 12s --source ncbi --dump-gb Results/gb --resume
```

BOLD のみ取得する場合

```bash
pixi run python3 -m taxondbbuilder build -c configs/db.toml -t "Rhinogobius" -m coi --source bold
```

NCBI と BOLD を統合する場合

```bash
pixi run python3 -m taxondbbuilder build -c configs/db.toml -t "Rhinogobius" -m coi --source both
```

## Source の選択

`build` では次の source を選べます。

- `--source ncbi`
  - NCBI / GenBank のみが対象
- `--source bold`
  - BOLD Data Portal のみが対象
- `--source both`
  - NCBI と BOLD の両方を取得し、BOLD `insdcacs` と NCBI accession の strict match のみで BOLD record をフィルタリング

## 設定ファイル (TOML形式)

`configs/db.toml` を編集して使います。

```toml
[ncbi]
email = "your.email@example.com"
api_key = "YOUR_API_KEY"

db = "nucleotide"
rettype = "gb"
retmode = "text"
per_query = 100
use_history = true

[bold]
# Optional. If omitted, built-in defaults are used.
# base_url = "https://portal.boldsystems.org/api"
# timeout_sec = 60
# retries = 3
# backoff_sec = 1.5
# user_agent = "TaxonDBBuilder/0.1"

[output]
default_header_format = "{acc_id}|{organism}|{marker}|{label}|{type}|{loc}|{strand}"

[output.header_formats]
simple = "{acc_id}|{marker}|{loc}"
verbose = "{acc_id}|{organism_raw}|{marker_raw}|{label_raw}|{type_raw}|{loc}|{strand}"
mifish_pipeline = "{db}|{acc_id}|{organism}"

# 必要な場合だけ、どちらか一方を追加生成。
# export_formats = ["qiime2"]  # qiime2 または dada2_species

[taxon]
noexp = false

[markers]
# 外部ファイルを参照 (必須)
file = "configs/markers_mitogenome.toml"

# 必要ならここに追記して外部定義を上書きできます
# [markers."mygene"]
# aliases = ["mygene", "mg"]
# phrases = ["MyGene"]
# region_patterns = ["MyGene"]
# [markers."mygene".bold]
# marker_codes = ["MYGENE"]

[filters]
# フィルタ無しがデフォルト。必要な場合だけ指定してください。
# filter = ["mitochondrion"]
# properties = ["PROPERTY_TERM"]
# sequence_length_min = 120
# sequence_length_max = 30000
# publication_date_from = "1990/01/01"
# publication_date_to = "2025/12/31"
# modification_date_from = "2024/01/01"
# modification_date_to = "2026/12/31"
# all_fields_include = ["12S"]
# all_fields_exclude = ["WGS"]
# raw = "complete[prop]"

[post_prep]
# build 実行時に --post-prep を指定すると適用されます
# パラメータは有効化するカテゴリに応じて指定します
# - length_filter: sequence_length_min または sequence_length_max
# - primer_trim: primer_file + primer_set
# - msa_tree: msa_tree_enable = true
# sequence_length_min = 120
# sequence_length_max = 300
# primer_file = "configs/primers.toml"
# primer_set = "mifish_12s"
# primer_set = ["mifish_12s", "mifish_ev2"]
# msa_tree_enable = false
# msa_tree_min_taxa = 3
# msa_tree_max_samples = 500
# msa_tree_model = "GTR+G"
# msa_tree_mode = "combined" # or "per_taxid"
# msa_tree_bootstrap_replicates = 1000
```

### 検索・抽出の考え方

- `phrases`: **検索用**の簡易フレーズ。自動的に `"..."[All Fields]` として扱われます。
- `terms`: **検索用**の生クエリ。`rrnS[Gene]` のようにフィールド指定をそのまま書けます。
- `region_patterns`: **GenBank feature抽出用**の正規表現。`gene/product/note/standard_name` などの注釈に対してマッチします。
- `[markers.<id>.bold].marker_codes`: **BOLD record 判定用**の marker code 一覧です。

`region_patterns` 未指定の場合は、`phrases/terms` から**リテラル**として自動生成します。
抽出はGenBankのfeature注釈に依存するため、目的の領域が出ない場合は `region_patterns` と `feature_types/feature_fields` を調整してください。
一方 BOLD では taxon で広く取得し、`marker_code` を client-side で絞り込みます。

### markers.file について

- `[markers].file` でマーカー定義を外部TOMLへ分離できます。
- 外部定義は `[markers]` テーブルを持つ必要があります。
- `db.toml` 側にも `[markers]` セクションが必要です（`file` のみでもOK）。
- トップレベルの `markers_file` はサポートしていません。
- `file` のパス解決は以下の順です
  1. 絶対パス
  2. `db.toml` のある場所からの相対
  3. 実行ディレクトリからの相対
  4. `taxondbbuilder` パッケージのある場所からの相対
- `db.toml` 側に書いた `[markers.<id>]` は外部ファイル定義を**上書き**します。

### taxon.noexp について

- `[taxon].noexp = false` (デフォルト): `txid{taxid}[Organism]` を使って検索します。
- `[taxon].noexp = true`: `txid{taxid}[Organism:noexp]` を使い、taxid 展開なしで検索します。
- まずは `false` のまま使い、検索対象を taxid 直下に絞りたい場合に `true` を検討してください。

## 設定ガイド

このツールの設定は「検索 / 取得」と「抽出 / 出力」を分けて考えると整理しやすいです。

### 設定ファイルの役割

- `db.toml` は「**共通設定** (NCBI / BOLD / 出力 / filters / markers.file)」を持ちます。
- `markers` 外部ファイルは「**マーカー定義**」だけを持ちます ([markers] テーブル)。
- これにより、用途ごとにマーカー定義を差し替える運用ができます。
- `[markers]` セクションは必須で、`file` 指定とインライン定義を併用できます。

### 1. 最小構成 (必須)

- `source=ncbi` / `source=both` を使う場合
  - `ncbi` セクション: `email` / `api_key` / `db` / `rettype` など
- `source=bold` のみを使う場合
  - `ncbi` セクションは省略可能
  - 必要なら `bold` セクションで timeout / retry などを調整
- `[markers]` セクション: `file` もしくはインライン定義
- `output`: FASTAヘッダーの形式

### 2. マーカー定義の考え方

マーカー定義は以下の 4 要素で構成されます。

- **検索用 (phrases / terms)**
  NCBIの検索に使う語句。
  `phrases` は `"..."[All Fields]` に自動変換されます。
  `terms` は `rrnS[Gene]` のようにフィールド指定をそのまま書けます。

- **抽出用 (region_patterns)**
  GenBankのfeature注釈 (gene/product/note/standard_name)に対する正規表現。

- **対象feature (feature_types / feature_fields)**
  `feature_types` で対象のfeature型を限定できます (rRNA/gene/CDS など)。
  `feature_fields` で参照する注釈項目を制御します。

- **BOLD 用判定 (bold.marker_codes)**
  BOLD の `marker_code` と照合する候補です。
  未指定時は `aliases` / `phrases` / marker key にフォールバックします。

### 3. よくあるパターン

**rRNA系 (12S/16S など)**

```toml
[markers."12s"]
aliases = ["12", "12s"]
phrases = ["12S", "rrnS", "small subunit ribosomal RNA"]
region_patterns = ["12S", "rrnS", "small subunit ribosomal RNA"]
feature_types = ["rRNA", "gene"]
feature_fields = ["gene", "product", "note", "standard_name"]

[markers."12s".bold]
marker_codes = ["12S"]
```

**タンパク質コーディング (COI/ND1 など)**

```toml
[markers."coi"]
aliases = ["coi", "co1", "cox1"]
phrases = ["COI", "CO1", "COX1", "cytochrome c oxidase subunit I"]
region_patterns = ["COI", "CO1", "COX1", "cytochrome c oxidase subunit I"]
feature_types = ["CDS", "gene"]
feature_fields = ["gene", "product", "note", "standard_name"]

[markers."coi".bold]
marker_codes = ["COI-5P", "COI-3P", "COI"]
```

### 4. FASTAヘッダーの指定

- `[output.header_formats]` にテンプレートを定義
- `markers.<id>.header_format` でテンプレート名を選択
  (直接テンプレート文字列を書いてもOK)

> [!NOTE]
> `markers_mitogenome.toml`にデフォルトで設定している`header_format=mifish_pipeline`はPMiFishパイプラインとMiFishパイプラインのDBに対応するフォーマットになっています。

例

```toml
[output.header_formats]
simple = "{acc_id}|{marker}|{loc}"
mifish_pipeline = "{db}|{acc_id}|{organism}"

[markers."12s"]
header_format = "mifish_pipeline"
```

#### FASTAヘッダーで使える変数

| 変数             | 出典             | 意味                                                                  |
| ---------------- | ---------------- | --------------------------------------------------------------------- |
| `{acc}`          | GenBank レコード | accession (record.id)                                                 |
| `{acc_id}`       | 内部生成         | 出力用 accession。重複配列がある場合は `_dupN` 付与                   |
| `{organism}`     | GenBank レコード | `ORGANISM` のサニタイズ済み文字列                                     |
| `{organism_raw}` | GenBank レコード | `ORGANISM` の生文字列                                                 |
| `{marker}`       | 設定/内部        | マーカーIDのサニタイズ済み文字列                                      |
| `{marker_raw}`   | 設定/内部        | マーカーIDの生文字列                                                  |
| `{label}`        | GenBank feature  | `gene/product/note/standard_name` などから一致した値 (サニタイズ済み) |
| `{label_raw}`    | GenBank feature  | 一致した値の生文字列                                                  |
| `{type}`         | GenBank feature  | feature type (例: `rRNA`, `gene`, `CDS`) のサニタイズ済み             |
| `{type_raw}`     | GenBank feature  | feature type の生文字列                                               |
| `{start}`        | GenBank feature  | feature の開始位置 (1-based)                                          |
| `{end}`          | GenBank feature  | feature の終了位置                                                    |
| `{loc}`          | GenBank feature  | `start-end` 形式の位置                                                |
| `{strand}`       | GenBank feature  | strand (`1`, `-1`, もしくは `0`)                                      |
| `{dup}`          | 内部生成         | 重複配列のタグ (`dupN` or 空文字)                                     |
| `{source}`       | 内部生成         | `ncbi` または `bold`                                                  |
| `{source_id}`    | 内部生成         | source 側の record ID                                                 |

### 補助出力

- `*.fasta.acc_organism.csv`
  - FASTAに出たレコードとaccession、organism、record固有のTaxID、lineage、source情報の対応表です。
- `*.fasta.source_merge.csv`
  - source 統合時の keep / skip を記録します。
  - `skip_reason=linked_by_insdcacs` は BOLD `insdcacs` が NCBI accession と strict match したため抑制されたことを意味します。
- `*.fasta.manifest.json`
  - バージョン、実行時刻、入力Taxon、検索クエリ、設定ファイルと出力ファイルのSHA-256を記録します。

## 逆引き

### Q. 目的のマーカー情報が登録されていない

1. `markers.file` (例: `configs/markers_mitogenome.toml`)に新規マーカーを追加
   もしくは `db.toml` 側の `[markers.<id>]` で追加
2. `aliases` を付けてCLIから指定できるようにする
3. `phrases/terms` を検索用に、`region_patterns` を抽出用に設定

最小例

```toml
[markers."mygene"]
aliases = ["mygene", "mg"]
phrases = ["MyGene", "my gene product"]
region_patterns = ["MyGene", "my gene product"]
feature_types = ["gene", "CDS"]
feature_fields = ["gene", "product", "note", "standard_name"]
```

### Q. 検索はヒットするが抽出されない

- `region_patterns` がfeature注釈に合っていない可能性があります。
  → GenBankの該当レコードを確認し、`gene/product/note` の表記に合わせて調整してください。

### Q. ヒット数が多すぎる / 少なすぎる

- `terms` を使ってフィールド指定 (例: `rrnS[Gene]`)すると検索精度が上がります。
- 必要に応じて `[filters]` を追加して絞り込みます。

### Q. 12S/16S 以外 (ITS/18S など)を使いたい

- `markers.file` に追加でOKです。
  例: `ITS`, `18S`, `28S` は `feature_types = ["rRNA", "gene"]` で定義するケースが多いです。

## 使い方

### コマンドと設定ファイルの対応

以下の対応関係を押さえると、設定とコマンドが繋がって理解できます。

| コマンド引数             | 参照する設定                                   | 説明                                                                                                                |
| ------------------------ | ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `-c/--config`            | `db.toml`                                      | 共通設定 (NCBI/出力/filters/markers.file)                                                                           |
| `-m/--marker`            | `markers.file` / `db.toml` の `[markers.<id>]` | 使うマーカー定義 (aliases で選択)                                                                                   |
| `-t/--taxon`             | なし                                           | taxid/学名を指定 (学名はTaxonomyで解決)                                                                             |
| `--workers`              | なし                                           | 抽出処理の並列数                                                                                                    |
| `--out`                  | なし                                           | 出力先 (省略時は `Results/db/YYYYMMDD/`)                                                                            |
| `--output-prefix`        | なし                                           | 出力FASTAファイル名のプレフィックス (default: `taxondbbuilder_`)                                                    |
| `--export-format`        | `[output].export_formats`                      | 下流ツール向け副生成物を追加 (`qiime2` / `dada2_species`のどちらか一方)                                             |
| `--dump-gb`              | なし                                           | GenBankチャンクを保存 (キャッシュ)                                                                                  |
| `--from-gb`              | なし                                           | 保存済みGenBankチャンクから抽出                                                                                     |
| `--resume`               | なし                                           | キャッシュを優先して利用                                                                                            |
| `--dry-run`              | なし                                           | 実際の取得・抽出を行わず、生成されるNCBIクエリのみ表示                                                              |
| `--post-prep`            | `db.toml` の `[post_prep]`                     | 生成FASTAに後処理を有効化                                                                                           |
| `--post-prep-step`       | `db.toml` の `[post_prep]`                     | 実行する後処理カテゴリを選択 (`primer_trim` / `length_filter` / `quality_filter` / `duplicate_report` / `msa_tree`) |
| `--post-prep-primer-set` | `[post_prep].primer_file`                      | primer_trim で使う primer_set をCLIから指定 (複数可・config上書き)                                                  |

### 具体例

1. `markers.file` に `12s` を定義しておく
2. `-m 12s` を指定 → `markers."12s"` の設定が適用される
3. `phrases/terms` で検索し、`region_patterns` で抽出する

設定例 (抜粋)

```toml
[markers."12s"]
aliases = ["12", "12s"]
phrases = ["12S", "rrnS"]
region_patterns = ["12S", "rrnS"]
```

実行例

```bash
pixi run python3 -m taxondbbuilder build -c configs/db.toml -t 63457 -m 12s
```

ファイル名にプレフィックスを付けたい場合

```bash
pixi run python3 -m taxondbbuilder build -c configs/db.toml -t 63457 -m 12s --output-prefix "rhinogobius"
```

GenBankを保存しつつ実行 (acc_idごとに `.gb` を保存)

```bash
pixi run python3 -m taxondbbuilder build -c configs/db.toml -t 63457 -m 12s --dump-gb Results/gb
```

保存済みGenBankから再抽出

```bash
pixi run python3 -m taxondbbuilder build -c configs/db.toml -t 63457 -m 12s --from-gb Results/gb
```

中断後の再開 (キャッシュ利用)

```bash
pixi run python3 -m taxondbbuilder build -c configs/db.toml -t 63457 -m 12s --dump-gb Results/gb --resume
```

生成されるNCBIクエリだけを確認 (`--dry-run`)

```bash
pixi run python3 -m taxondbbuilder build -c configs/db.toml -t 63457 -m 12s --dry-run
```

既存FASTAを保ったままQIIME 2用ファイルも生成

```bash
pixi run python3 -m taxondbbuilder build -c configs/db.toml \
  -t 63457 -m 12s --export-format qiime2
```

DADA2 `assignSpecies`用ファイルを生成

```bash
pixi run python3 -m taxondbbuilder build -c configs/db.toml \
  -t 63457 -m 12s \
  --export-format dada2_species
```

post-prep を有効化 (primer trim + 長さフィルタ + 重複ACCレポート)

```bash
pixi run python3 -m taxondbbuilder build -c configs/db.toml -t 63457 -m 12s --post-prep
```

post-prep のカテゴリを明示指定 (primer trim + 重複ACCレポートのみ)

```bash
pixi run python3 -m taxondbbuilder build -c configs/db.toml -t 63457 -m 12s --post-prep \
  --post-prep-step primer_trim \
  --post-prep-step duplicate_report
```

primer_set を複数指定して primer_trim を実行 (config の primer_set を上書き)

```bash
pixi run python3 -m taxondbbuilder build -c configs/db.toml -t 63457 -m 12s --post-prep \
  --post-prep-primer-set mifish_12s \
  --post-prep-primer-set mifish_ev2 \
  --post-prep-step primer_trim
```

primer_set の候補を一覧表示

```bash
pixi run python3 -m taxondbbuilder list-primer-sets -c configs/db.toml
```

TaxIDごとの系統樹を作成する場合は、`[post_prep]` に以下を設定して実行します。

```toml
msa_tree_enable = true
msa_tree_mode = "per_taxid"
msa_tree_bootstrap_replicates = 1000
```

```bash
pixi run python3 -m taxondbbuilder build -c configs/db.toml \
  -t 32443 -t 7777 -m 12s --post-prep --post-prep-step msa_tree
```

キャッシュは `Results/gb/.cache/` に保存されます。

GenBankのresumeキャッシュはTaxIDと検索クエリごとに`Results/gb/.cache/taxid{ID}/query-{HASH}/`へ分離されます。

### taxid指定

```bash
pixi run python3 -m taxondbbuilder build -c configs/db.toml -t 32443 -m 12s
```

### 学名指定

```bash
pixi run python3 -m taxondbbuilder build -c configs/db.toml -t "Rhinogobius" -m 12s
```

### 複数 taxon / marker

```bash
pixi run python3 -m taxondbbuilder build -c configs/db.toml -t 32443 -t 7777 -m 12 -m coi
```

### マーカー一覧の確認

```bash
pixi run python3 -m taxondbbuilder list-markers -c configs/db.toml
```

### 並列抽出 (ダウンロードと変換の並列化)

```bash
pixi run python3 -m taxondbbuilder build -c configs/db.toml -t 63457 -m 12s --workers 2
```

## 抽出ロジック

- NCBIから**GenBank形式**で取得し、feature注釈から目的領域を抽出します。
- `region_patterns` が `gene/product/note/standard_name` などの注釈に一致したfeatureのみFASTAへ出力します。
- `feature_types` を指定すると対象feature型を限定できます (例: rRNA, gene, CDS)。

## FASTAヘッダーフォーマット

- `[output.header_formats]` にテンプレートを定義し、`markers.<id>.header_format` で選択できます。
- 直接テンプレート文字列を `header_format` に書くことも可能です。

使用できるプレースホルダ
`{acc}`, `{acc_id}`, `{db}`, `{organism}`, `{organism_raw}`, `{marker}`, `{marker_raw}`, `{label}`, `{label_raw}`, `{type}`, `{type_raw}`, `{start}`, `{end}`, `{loc}`, `{strand}`, `{dup}`

## 出力

- 出力先: `Results/db/YYYYMMDD/`
- ファイル名: `taxid{ID}__{marker}.fasta` (複数指定時は `multi_taxon` / `multi_marker`)
- 実行ログ: 出力FASTAと同名の `.log`
- ACCと生物種名の対応表: `*.fasta.acc_organism.csv`（`organism_taxid`と`taxonomy_lineage`を含む）
- source merge 対応表: `*.fasta.source_merge.csv`（NCBI/BOLDのsource、要求TaxID、record固有TaxID、lineage、accession、出力ヘッダーを記録）
- 実行manifest: `*.fasta.manifest.json`

`[output].export_formats` または `--export-format` 指定時

指定できる形式は一つだけです。

- `qiime2`
  - `*.fasta.qiime2.sequences.fasta`: 一意なFeature IDを持つ配列
  - `*.fasta.qiime2.taxonomy.tsv`: `Feature ID<TAB>Taxon`形式
  - Taxonには取得レコードのlineageと生物名を使い、lineageがない場合は生物名だけを使います。
- `dada2_species`
  - `*.fasta.dada2.species.fasta`: `>ID Genus species`形式
  - 二名法として判定できないレコードは出力せず、件数をログに記録します。

GUIではPMiFish、QIIME 2、DADA2から一つを選択でき、選択に合わせて`output.header_formats.mifish_pipeline`を`gb|{acc_id}|{organism}`、`{acc_id}`、または`{acc_id} {organism_raw}`へ切り替えます。

`--post-prep` 指定時

- デフォルトでは、設定が存在するカテゴリを実行
  - `primer_trim` (primer設定がある場合)
  - `length_filter` (length設定がある場合)
  - `quality_filter` (quality設定がある場合)
  - `duplicate_report` (常に実行)
  - `msa_tree` (`msa_tree_enable = true` の場合、最後に実行)
- `--post-prep-step` を指定した場合、指定カテゴリのみ実行
- `[post_prep].primer_file + primer_set` 指定時、`primer_trim` カテゴリで primer trim を適用
- `primer_set` は文字列または文字列配列で指定可能
- `--post-prep-primer-set` を使うと、実行時に primer_set を上書き可能 (複数指定可)
  - 5'末端: `forward` 候補
  - 3'末端: `reverse` の逆相補候補
  - 逆向き配列も考慮し、`reverse`(5') + `forward`逆相補(3') の組み合わせも判定
  - IUPAC塩基 (`R`, `Y`, `N` など) を利用可能
- `[post_prep].sequence_length_min/max` 指定時、`length_filter` カテゴリで配列長フィルタを適用
- `[post_prep].quality_max_ambiguous_fraction`で曖昧塩基の許容率を0から1で指定
- `[post_prep].quality_reject_invalid_iupac = true`で標準IUPAC DNA以外の塩基を除外
- `[post_prep].duplicate_sequence_policy`は`keep`、`representative`、`exclude_conflicts`から選択
- `quality_filter`の除外結果は`*.fasta.quality_rejected.csv`へ出力
- `[post_prep].msa_tree_enable = true` 指定時、`kalign-python`によるDNA MSA (`*.msa.fasta`) と`piqtree`によるNewick系統樹 (`*.tree.nwk`) を出力
- `[post_prep].msa_tree_mode = "combined"`（デフォルト）は全TaxIDをまとめて1本作成し、`"per_taxid"` はTaxIDごとに `*.taxid{ID}.msa.fasta` / `*.taxid{ID}.tree.nwk` を作成
- `msa_tree_bootstrap_replicates`（デフォルト1000）でBootstrapを実行し、支持値をNewickの内部ノードラベルとして保存
- GUIのResults画面では生成された系統樹をTaxIDごとのセレクタで切り替え、支持値を枝の上側に表示
- FASTAヘッダーテンプレートに `{acc_id}` と `{organism_raw}` (または `{organism}`) が含まれる場合、同一配列の重複情報を以下に出力
- `*.fasta.duplicate_acc.records.csv` (1レコード=1行の詳細)
- `*.fasta.duplicate_acc.groups.csv` (重複グループの集約。`cross_organism_duplicate` を含む)
- 条件を満たさないヘッダーテンプレートの場合、重複ACCレポートCSVはスキップされ、理由はコンソールと `.log` に出力

primer list ファイル例 (`configs/primers.toml`)

```toml
[primer_sets.mifish_12s]
forward = ["GTCGGTAAAACTCGTGCCAGC"]
reverse = ["CATAGTGGGGTATCTAATCCCAGTTTG"]
```

`*.fasta.duplicate_acc.records.csv` の主な列

- `group_id`
- `sequence_hash`
- `sequence_length`
- `records_in_group`
- `unique_accessions`
- `unique_organisms`
- `cross_organism_duplicate`
- `acc_id`
- `accession`
- `organism_name`
- `header`

`*.fasta.duplicate_acc.groups.csv` の主な列

- `group_id`
- `sequence_hash`
- `sequence_length`
- `records_in_group`
- `unique_accessions`
- `unique_organisms`
- `cross_organism_duplicate`
- `accessions` (`;`区切り)
- `organism_names` (`;`区切り)

## キャッシュと再抽出

- `--dump-gb` で **acc_idごとのGenBankファイル** を保存します。
- NCBIキャッシュはTaxIDと検索クエリのハッシュごとに`--dump-gb/.cache/`へ保存します。
- BOLDキャッシュは正規化した検索語と形式のハッシュごとに`--dump-gb/.cache/bold/`へ保存します。
- `--resume`はNCBIとBOLDのキャッシュを優先して使います（出力は毎回新規に作り直します）。
- `--from-gb` はネットワークを使わず、保存済みGenBankから抽出のみを実行します。

## 重複の扱い

- **同一アクセッション + 同一配列**: 重複として除外
- **同一アクセッション + 異なる配列**: 両方残し、IDに `_dupN` を付与して警告ログに記録

## フィルタについて

フィルタは**指定なしを受け付けます**。必要な場合のみTOMLの `[filters]` に追加してください。
`[filters]` は NCBI Nucleotide の Filtering 項目に合わせています (Advanced Search の index list を参照)。

- `filter`: NCBI "Filter" 項目の語彙。文字列 or 文字列配列 → `{term}[filter]`
- `properties`: NCBI "Properties" 項目の語彙。文字列 or 文字列配列 → `{term}[prop]`
- `sequence_length_min`, `sequence_length_max`: `{min}[SLEN] : {max}[SLEN]`
- `publication_date_from`, `publication_date_to`: `{from}[PDAT] : {to}[PDAT]`
- `modification_date_from`, `modification_date_to`: `{from}[MDAT] : {to}[MDAT]`
- `all_fields_include`, `all_fields_exclude` (文字列 or 文字列配列): `"..."[All Fields]` を OR / NOT で合成
- `raw`: 生クエリ文字列 (配列も可) をそのまま追加

## GUI

GUIの環境構築、sidecar作成、ビルド手順は[GUI README](tauri-gui/README.md)を参照してください。


## ミトコンドリア領域の注釈補完

`-m Full`はcomplete mitochondrial genomeと明示されたレコードを全長配列で出力します。
`-m mitogenome`は注釈された各領域を個別に出力し、FASTAヘッダー末尾に`|region=<領域名>`を付けます。

同梱のmitogenomeマーカー設定では、D-loop、12S、16S、13種類のCDSの不十分なFeature注釈を補完します。
`-m mitogenome`でまとめて抽出する場合と、`-m nd4`などの個別指定で同じ処理を使います。
正常な領域は元の注釈を優先し、不十分な領域だけを評価します。

```toml
[markers."nd4"]
# 既存のphrases、feature_typesなどに追加
fallback = "mitogenome"
fallback_targets = ["ND4"]
```

`fallback_targets`は`12S`、`16S`、`COI`、`COII`、`COIII`、`CYTB`、`ATP6`、`ATP8`、`ND1`〜`ND6`、`ND4L`、`control_region`を指定できます。
tRNAを明示的に抽出する設定では、`tRNA-Phe`などの名前も指定できます。
LeuとSerは`tRNA-Leu(UUR)`、`tRNA-Leu(CUN)`、`tRNA-Ser(UCN)`、`tRNA-Ser(AGY)`で区別します。
設定を省略するか`fallback = "none"`にすると、そのマーカーは従来通り抽出します。
BOLDの取得には適用しません。

補完は次の順序で進みます。

1. 同じ座位の正常な別Featureがあれば、その境界を使用します。
2. ミトコンドリア由来で、脊椎動物の配置と整合する場合に両側の遺伝子から候補範囲を求めます。
3. RNAと非コード領域では遺伝子間の推定区間を使います。CDSでは翻訳情報、開始と終止、元の位置を検証し、一意に決まる候補だけを採用します。

単一塩基、10 bp以下、無効なLocation、部分境界、翻訳注釈との不一致が補完の契機になります。
対象Feature自体がない場合も候補を調べますが、CDSの位置もtranslationもない場合は推測でORFを選びません。
10 bpという値は明らかな短縮注釈の検出用で、各領域の正常長の下限ではありません。
原点越えは`topology=circular`の場合に限ります。
アンカーの欠損、曖昧な複数コピー、配置の不一致、十分な境界根拠がないCDSは理由付きで見送ります。

CDSの重複部分は検証した範囲で保持します。
不完全終止コドンのTまたはTAは、同じ鎖の隣接RNAとの境界および既存のTERM注釈がある場合はその位置を確認して扱い、塩基を人工的に追加しません。
`codon_start`が2または3のCDS補完、TERM以外の翻訳例外、fuzzyまたは原点を跨ぐ複合アンカーは現在の補完では扱わず、ログに理由を記録します。
正常な元Featureの抽出はこれらの制限の対象外です。

補完結果は`<output.fasta>.region_fallback.tsv`と既存の`.log`で確認できます。
TSVにはFASTAヘッダー、領域ID、補完方法、理由、元座標、採用座標、アンカー、鎖、翻訳表、重複塩基数を記録します。
`recovered`は別注釈による補完、`inferred`は境界推定、`partial_unresolved`は補完できず元の部分配列を保持した結果です。
構造不備の補完に失敗した1塩基などの配列は出力せず、見送り理由をログに残します。
補完が0件でも、有効な設定ではTSVの列名を出力します。

座標は1-based inclusiveで、原点を跨ぐ場合は`join(...)`、負鎖は`complement(...)`と表します。
補完結果のヘッダー変数`{loc}`にもこの表記を使い、`{start}`と`{end}`はレコード正方向の区間の入口と出口です。
原点越えでは`start > end`になる場合があります。
FASTAヘッダーの設定形式は維持しつつ領域suffixを付けるため、PMiFishなどの専用形式でも領域を識別できます。

TSVの`stage=extraction_before_post_prep`は、抽出時点の座標と配列長を表します。
後続のトリミングやフィルターで配列が変わってもTSVの元座標を更新しないため、最終FASTAへの残存状況はヘッダーで照合してください。
両側遺伝子からの推定区間は実験的に確定した境界を保証しません。

例としてOQ851455.1の`D-loop 15637`は、tRNA-Proの終端と原点にあるtRNA-Pheから`15637..17610`（1,974 bp）と推定されます。
