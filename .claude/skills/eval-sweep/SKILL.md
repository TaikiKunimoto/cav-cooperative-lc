---
name: eval-sweep
description: 修論評価スイープの実行・集計・作図・Excel化を一気通貫で行う。実験を回したい/結果をまとめたい/図を作り直したい時に使う。
---

# 評価スイープの実行・集計

このリポジトリの評価パイプライン（`scripts/eval/`）を運用する。詳細仕様は
`scripts/eval/README.md`、運用ルールは `docs/評価運用SOP.md` を必ず先に読むこと。

## 基本コマンド（1コマンド一気通貫）

```bash
uv run python scripts/eval/run_all.py --suite proposed --quick   # 動作確認（約5分）
uv run python scripts/eval/run_all.py --suite proposed           # フル（ローカル約80分）
uv run python scripts/eval/run_all.py --skip-sweep               # 既存結果から集計以降のみ
```

個別ステップ: `run_sweep.py`（実行）→ `aggregate.py`（集計）→ `make_figures.py`（図）→ `export_excel.py`（Excel）。

## 実行前チェック（必須）

1. `git status` — 作業ツリーが dirty なら、その旨をユーザーに伝えてから実行する
   （manifest に git_dirty が記録される。論文用の本番スイープはクリーンなコミットで行う）。
2. `SUMO_HOME` が設定されていること（未設定なら run_sweep が止まる。勝手に値を推測して設定しない）。
3. フルスイープ（385 jobs・約80分）を回す前は、必ずユーザーに確認する。--quick は確認不要。

## 実行後サニティチェック

- run_sweep 末尾の `bad=` が 0 か。失敗 run があれば `out/logs/<name>.log` を確認して報告する。
- `summary_scenario.csv` の n が期待値（env×Q×f×seed の積）と一致するか。
- `deadline_rate` が 0..1 の範囲か。`collisions` が異常に大きくないか（過去実績: 高負荷で 0〜数件/run）。
- 異常があれば図・Excel を作る前にユーザーへ報告する。

## してはいけないこと

- `out/raw/` `manifest.json` の削除・手動編集（結果の一次データ。消すのはユーザーの判断）。
- `--force` の独断使用（既存結果の再実行＝上書き。ユーザー指示がある時のみ）。
- 評価数値の解釈・論文への採否の判断（人間の仕事。数値の異常検出までが自分の仕事）。
