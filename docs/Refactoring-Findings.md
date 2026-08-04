# Refactoring Findings

Phase 0 の特性化テストで確認した、リファクタリング前の挙動を記録する。

- `load_config()` は入力 TOML の `[post_prep]` をその場で正規化し、`_primer_forward`、`_primer_reverse` などの内部キーを追加する。
- `load_config(source=BuildSource.BOTH)` は `[bold]` が無い場合でも、戻り値に空の `bold` テーブルを追加する。
- `build_header()` はテンプレートに存在しないフィールドをエラーにせず空文字列へ置換する。
- `build_region_patterns()` は `region_patterns` が指定されている場合は正規表現としてそのまま返し、フォールバック時だけフィールド指定を除去してエスケープする。
- `resolve_orientation()` は両向きの順位が同じ場合、canonical 側を返しつつ ambiguous フラグを `True` にする。
- `normalize_bold_row()` は配列や区切り文字を含む入力から最初のスカラー値を採用し、配列塩基以外の ASCII 文字を除去して配列を正規化する。
- `build_bold_canonical_record()` は marker 側に `header_format` が無い場合、`output.header_formats` や default format ではなく `bold|{acc_id}|{organism}` を使う。
