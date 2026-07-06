# sgnlab リモート評価（同期 → 実行 → 進捗 → 回収）

ローカル Mac から Linux サーバ `sgnlab`（`~/.ssh/config` の Host 定義を使用）で
評価スイープを回し、結果だけ持ち帰るためのスクリプト群。

## 前提（sgnlab 側・初回のみ）

1. SUMO がインストール済みで `sumo --version` が通ること（`SUMO_HOME` はスクリプトが `/usr/share/sumo` を既定で設定。異なる場合は `SGNLAB_SUMO_HOME` で上書き）
2. uv がインストール済み（`curl -LsSf https://astral.sh/uv/install.sh | sh`）
3. Python 3.13（`uv python install 3.13`）

## 使い方（ローカルから）

```bash
# 1) コードを同期（結果は消さない・.gitignore 対象は送らない）
scripts/remote/sgnlab_push.sh

# 2) スイープを起動（ジョブ数を表示して確認プロンプト。--yes でスキップ）
scripts/remote/sgnlab_run.sh --suite proposed --quick    # まず動作確認
scripts/remote/sgnlab_run.sh --suite proposed --workers 20

# 3) 進捗確認（読み取りのみ）
scripts/remote/sgnlab_status.sh

# 4) 結果回収（ローカル既存ファイルは上書きしない）＋ manifest マージ
scripts/remote/sgnlab_fetch.sh

# 5) ローカルで集計 → 図 → Excel
uv run python scripts/eval/run_all.py --skip-sweep
```

## 安全設計

- `push` / `fetch` は **`--delete` を使わない**。リモートの結果・ローカルの結果とも消えない。
- `fetch` の raw CSV は `--ignore-existing`（同名はローカル優先）。
- manifest はキー単位マージで、マージ前に `manifest.backup.<日時>.json` を自動保存。
- `run` は投入ジョブ数を表示して確認を取る（大量ジョブの誤投入防止）。

## 設定の上書き

| 環境変数 | 既定値 | 意味 |
|---|---|---|
| `SGNLAB_HOST` | `sgnlab` | ssh の Host 名 |
| `SGNLAB_DIR` | `eval/high-way-branch-v2` | リモート作業ディレクトリ（$HOME 相対） |
| `SGNLAB_SUMO_HOME` | `/usr/share/sumo` | リモートの SUMO_HOME |
