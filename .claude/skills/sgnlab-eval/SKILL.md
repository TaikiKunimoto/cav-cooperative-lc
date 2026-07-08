---
name: sgnlab-eval
description: sgnlab サーバでの評価スイープの同期・起動・進捗確認・結果回収。リモートで実験を回したい/結果を持ち帰りたい時に使う。
---

# sgnlab リモート評価

`scripts/remote/` のスクリプト群を使う。詳細は `scripts/remote/README.md` を先に読むこと。

## 手順

```bash
scripts/remote/sgnlab_push.sh                       # 1) コード同期（結果は消さない）
scripts/remote/sgnlab_run.sh --suite proposed --quick  # 2) 起動（ジョブ数確認プロンプトあり）
scripts/remote/sgnlab_status.sh                     # 3) 進捗（読み取りのみ）
scripts/remote/sgnlab_fetch.sh                      # 4) 回収（ローカル既存は上書きしない）
uv run python scripts/eval/run_all.py --skip-sweep  # 5) ローカルで集計→図→Excel
```

リモートで `--force` 再採取した結果を採用するときだけ `sgnlab_fetch.sh --take-remote`
（ローカル raw/ を raw.backup.<日時>/ へ退避してからリモートで上書き）。fetch が
「内容が異なるCSVが N 件」と警告したら、この分岐が必要なサイン。ユーザーに確認してから使う。

## 安全ルール（厳守）

- **フルスイープ（数百 jobs）の起動は必ずユーザーに事前確認**。--yes を独断で付けない。
- リモートでの削除系操作（rm, --delete 付き rsync 等）は行わない。掃除が必要ならユーザーに提案する。
- 接続できない場合（Tailscale 切断等）は2回まで再試行し、ダメならユーザーに報告する。
- sgnlab 上のリポジトリはローカルの写しであり、リモート側でコードを直接編集しない
  （編集は必ずローカル→push の一方向。結果は fetch の一方向）。
