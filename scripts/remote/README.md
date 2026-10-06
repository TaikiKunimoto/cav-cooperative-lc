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
#    スイープ実行中は拒否される（コード差し替えで新旧結果が混在するため）
scripts/remote/sgnlab_push.sh

# 2) スイープを起動（ジョブ数を表示して確認プロンプト。--yes でスキップ）
scripts/remote/sgnlab_run.sh --suite proposed --quick    # まず動作確認
scripts/remote/sgnlab_run.sh --suite proposed --workers 20

# 3) 進捗確認（読み取りのみ）
scripts/remote/sgnlab_status.sh

# 4) 結果回収（ローカルに無い CSV だけ取り込む）＋ manifest マージ
scripts/remote/sgnlab_fetch.sh

# 5) ローカルで集計 → 図 → Excel
uv run python scripts/eval/run_all.py --skip-sweep
```

### リモートで `--force` 再採取したときの回収

既定の fetch はローカル優先（同名 CSV は取り込まない）なので、リモートで採り直した結果を
採用するときは明示的に指定する:

```bash
scripts/remote/sgnlab_fetch.sh --take-remote   # ローカル raw/ を raw.backup.<日時>/ へ退避してから上書き
```

既定の fetch が「リモートと内容が異なるローカル既存CSVが N 件」と警告したら、この分岐が必要なサイン。

## 安全設計

- `push` / `fetch` は **`--delete` を使わない**。リモートの結果・ローカルの結果とも消えない。
- `push` はスイープ実行中を検知して拒否（`.sync_metadata.json` で git 来歴もリモートへ伝える）。
- 既定 `fetch` はローカルに無い CSV だけ取り込む（ヘッダのみ＝失敗残骸の CSV は先に除去）。
  `--take-remote` はローカル raw/ 全体を退避してから上書き。
- manifest はキー単位マージ（既定はローカル優先、`--take-remote` 時はリモート優先。
  成功エントリが失敗エントリに負けることはない）。マージ前に `manifest.backup.<日時>.json` を自動保存。
- `run` は投入ジョブ数を表示して確認を取る（大量ジョブの誤投入防止）。

## 設定の上書き

| 環境変数 | 既定値 | 意味 |
|---|---|---|
| `SGNLAB_HOST` | `sgnlab` | ssh の Host 名 |
| `SGNLAB_DIR` | `eval/high-way-branch-v2` | リモート作業ディレクトリ（$HOME 相対） |
| `SGNLAB_SUMO_HOME` | `/usr/share/sumo` | リモートの SUMO_HOME |
