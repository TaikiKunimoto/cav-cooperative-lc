# 評価運用 SOP（実験の回し方・守るべきルール・確認事項）

修論・IPSJ 論文（2026年9月締切）の評価実験を、**再現可能・非破壊・半自動**で回すための標準手順。
ツールの使い方の詳細は `scripts/eval/README.md`（ローカル）と `scripts/remote/README.md`（sgnlab）。

---

## 1. 実験の基本フロー

```
[実装変更] → 決定性チェック → quick スイープ → （本番）フルスイープ → 集計 → 図/Excel → 人間が解釈
```

| やること | コマンド | 目安時間 |
|---|---|---|
| 決定性チェック（同一seed→同一結果） | `uv run python scripts/eval/check_determinism.py` | 約4分 |
| 動作確認スイープ | `uv run python scripts/eval/run_all.py --suite proposed --quick` | 約5分 |
| フルスイープ（ローカル） | `uv run python scripts/eval/run_all.py --suite proposed` | 約80分 |
| フルスイープ（sgnlab） | `scripts/remote/sgnlab_push.sh` → `sgnlab_run.sh` → `sgnlab_fetch.sh` | サーバ性能次第 |
| 集計以降だけ再実行 | `uv run python scripts/eval/run_all.py --skip-sweep` | 1分未満 |

## 2. 再現性のルール（论文に載せる数値を採るとき）

1. **クリーンなコミットで採る**: 論文用スイープは必ず commit 済みの状態で実行する。
   manifest.json に git コミット・branch・dirty フラグ・SUMO バージョンが自動記録される。
   `git_dirty: true` のスイープ結果は論文に使わない。
2. **コード変更後は決定性チェック**: v2 は Python `random` のグローバル状態に依存しており、
   乱数を消費する処理の追加・順序変更で同一 seed でも結果が変わる。実装をいじったら
   `check_determinism.py` を1回回す（不一致なら図表がすべて信用できなくなる）。
3. **スイープ途中でコードを変えない**: run_sweep は冪等（済みの run をスキップ）なので、
   途中でコードを変えて再開すると新旧コミットの結果が混ざる。変えたら `--force` で全再実行。
   `--force` 時は旧 CSV が `<run名>.csv.prev` に退避される（再実行が失敗しても旧結果は残る）。
   **リモート（sgnlab）で `--force` 再採取した場合**、回収は `sgnlab_fetch.sh --take-remote`
   を使う（既定の fetch はローカル優先のため新しい結果を取り込まない。fetch が警告を出す）。
4. **1条件だけ手で回したいとき**も、素の `python -m v2` ではなく quick スイープか
   EVAL_OUTPUT_NAME を使う（`statistics/v2/` への手動実行ファイル散乱を増やさない）。

## 3. 結果を壊さないルール

- `scripts/eval/out/raw/`・`manifest.json` は一次データ。**削除・手動編集しない**。
  上書きは run_sweep の `--force` 指定時のみ（意図的な再採取）。
- Excel は `out/excel/評価サマリ_<git>_<日時>.xlsx` に毎回新規作成（既存があればエラーで停止）。
- sgnlab からの回収は `--ignore-existing`＝ローカル優先。manifest はマージ前に自動バックアップ。
- 旧来の `TraCI/simulationStatistics/statistics/` 直下は同一分内の再実行でも連番付与で
  上書きされない（修正済み）が、新規の実験ではスイープ経由を正とする。

## 4. 実行後サニティチェック（毎回）

- [ ] run_sweep 末尾の `bad=0` か（失敗があれば `out/logs/<run名>.log` を見る）
- [ ] `out/summary_excluded.csv` が空か（除外 run は生存バイアスの元。例: 衝突でクラッシュした
      run が消えると安全性が過大評価される。除外がある場合は理由を確認してから数値を使う）
- [ ] `summary_scenario.csv` の n ＝ 期待 run 数（例: フル proposed は env×6Q×3f×5seed=90/env）
- [ ] `deadline_rate` は 0〜1 / `collisions` は過去実績レンジ（0〜数件/run、高負荷でのみ増える）か
- [ ] `throughput` が供給 Q に対して極端に低くないか（低い＝流入詰まり・設定ミスの疑い）
- [ ] 同一条件・複数 seed 間で異常な外れ値がないか（`summary_robustness.csv` の std）

## 5. 人間が判断すること（AI・スクリプトに任せないこと）

- 指標の解釈（達成率低下が「手法の限界」か「容量超過で物理的に妥当」か）
- 評価パラメータの掃引範囲・シナリオ構成の妥当性（研究の主張と対応しているか）
- 論文に載せる図表の選択と、数値の最終確認（Excel の meta シートで来歴を確認）
- ベースライン比較の公平性（net 形状差の caveat を含む）

## 6. 置き場所の規約

| 何 | どこ | git 管理 |
|---|---|---|
| 生 run CSV / 実行ログ / manifest | `scripts/eval/out/{raw,logs}/`, `manifest.json` | しない（再生成可能） |
| 集計サマリ（CSV/MD）・図・所見 | `scripts/eval/out/`（summary_* / figures/ / FINDINGS.md） | する（PRで更新） |
| Excel ブック | `scripts/eval/out/excel/` | しない（都度生成） |
| 論文用に確定した図 | `~/workspace/lab-workspace/tex/<論文>/figure/` へコピー | 論文側 |

図を論文へ持っていくときは「summary_long.csv → make_figures.py → コピー」の経路のみ使う
（手元で図をいじらない。体裁変更は make_figures.py を変更して再生成＝再現可能に保つ）。
