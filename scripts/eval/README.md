# 評価スイープ（提案 v2 の複数シナリオ評価 ＋ 分流ベースライン比較）

修論評価用の **実行 → 集計 → 作図** を自動化するパイプライン。

## 伝えたいこと（このスイープが示すもの）

- **メッセージA**：単一の手法（EDF統一調停 v2）で、形状の異なる複数シナリオ（分流D・合流M・一側織込みMD-1f・両側織込みMD-2）の**必須車線変更（締切達成率）に一様に対応**できる。
- **メッセージB**：**流入量 Q と 必須LC比率 f を変えても**達成率が高位で安定する（頑健性）。
- **補足（分流）**：分流Dに限り、既存手法 v1（卒論 custom / LC2013 default）と**交通効率**を比較する（既存手法は複数シナリオ非対応のため分流のみ）。

## 構成

| スクリプト | 役割 |
|---|---|
| `run_sweep.py` | 実行マトリクス（method×env×Q×f×seed）を並列実行。CSV を `out/raw/` に決定的名で集約し `out/manifest.json` を記録 |
| `aggregate.py` | manifest と各 CSV を集計 → `out/summary_long.csv` / `summary_scenario.{csv,md}` / `summary_robustness.csv` |
| `make_figures.py` | `summary_long.csv` から図を生成 → `out/figures/*.png` |

## 使い方（リポジトリ直下から）

```bash
# 0) 動作確認（小グリッド）
uv run python scripts/eval/run_sweep.py --suite proposed --quick

# 1) 提案手法フルスイープ（4必須LC環境 + straight障害物）
uv run python scripts/eval/run_sweep.py --suite proposed --workers 20

# 2) 分流ベースライン（v2 / default / custom を分流で交通効率比較）
uv run python scripts/eval/run_sweep.py --suite baseline --workers 8

# 3) 集計 → 作図
uv run python scripts/eval/aggregate.py
uv run python scripts/eval/make_figures.py
```

成果物はすべて `scripts/eval/out/` 配下（`raw/` 生CSV・`logs/` 実行ログ・`figures/` 図・各 summary）。

## 評価グリッド（既定 = しっかり）

- 環境（必須LC）：`diverge` / `merge` / `weave` / `weave2`
- 障害物（突発）：`straight` + `--obstacle 1,500,60`（B シナリオ。回避LCは締切達成率の母数外なので安全性で評価）
- Q（総流入）：1500 / 2000 / 2500 / 3000 / 3500 / 4000 [veh/h]
- f（必須LC比率）：0.2 / 0.4 / 0.6
- seed：1–5

## 主要指標（CSV 列）

- `deadline_achievement_rate` … **締切達成率＝必須LC完了/要求**（中核指標。straight障害物は母数0で空）
- `total_collisions` / `min_TTC` / `TET` … 安全性
- `traffic volume`（スループット）/ `canceled_vehicles` … 容量
- `average_speed` / `average_travel_time` … 効率

## 実装フック（評価専用・環境変数。未設定なら従来動作＝golden 不変）

- `EVAL_OUTPUT_DIR` / `EVAL_OUTPUT_NAME` … 出力先と決定的ファイル名（並列衝突なし・冪等・再開可能）
- `EVAL_NO_PLOT` … v1 の time-space 図出力を抑止（高速化）
- `EVAL_SUMOCFG` … v1 の設定ファイル差し替え（ベースラインは `config/v1-fast/`＝ExitLane の人工渋滞を除去した高速版で実行）

## 注意・前提

- v1（高速版）net は **2496m**・v2 diverge は **1000m** と形状が異なるため、交通効率の比較は**平均速度／スループット（対供給）で行い、走行時間の絶対値は直接比較しない**（要 caveat）。
- 実行には `SUMO_HOME` が必要（headless `sumo`）。
