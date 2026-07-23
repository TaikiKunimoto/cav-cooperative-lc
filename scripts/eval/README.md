# 評価スイープ（提案 v2 の複数シナリオ評価 ＋ 分流ベースライン比較）

修論評価用の **実行 → 集計 → 作図** を自動化するパイプライン。

## 伝えたいこと（このスイープが示すもの）

- **メッセージA**：単一の手法（EDF統一調停 v2）で、形状の異なる複数シナリオ（分流D・合流M・一側織込みMD-1f・両側織込みMD-2）の**必須車線変更（締切達成率）に一様に対応**できる。
- **メッセージB**：**流入量 Q と 必須LC比率 f を変えても**達成率が高位で安定する（頑健性）。
- **補足（分流）**：分流Dに限り、既存手法 v1（卒論 custom / LC2013 default）と**交通効率**を比較する（既存手法は複数シナリオ非対応のため分流のみ）。

## 構成

| スクリプト | 役割 |
|---|---|
| `run_all.py` | **1コマンド一気通貫**（実行→集計→作図→Excel）。まずこれを使う |
| `run_sweep.py` | 実行マトリクス（method×env×Q×f×seed）を並列実行。CSV を `out/raw/` に決定的名で集約し `out/manifest.json` を記録（gitコミット・SUMOバージョン等の来歴つき） |
| `../run_eval.py` | **一括ランナー（CLI 直積グリッド）**: policy×env×Q×f×seed範囲×並列度 を引数指定でループ実行。レジューム可・全コマンドを `out/run_manifest.txt` に記録。固定スイートでなく任意グリッドを回すときに使う |
| `aggregate.py` | manifest と各 CSV を集計 → `out/summary_long.csv` / `summary_scenario.{csv,md}` / `summary_robustness.csv` / `summary_mlc.{csv,md}` |
| `make_figures.py` | `summary_long.csv` から図を生成 → `out/figures/*.png` |
| `export_excel.py` | 集計結果＋来歴を1つの Excel ブックへ → `out/excel/評価サマリ_<git>_<日時>.xlsx`（毎回新規＝上書きなし） |
| `check_determinism.py` | 同一条件・同一seed を複数回実行し結果一致を検証（実装変更後の再現性ガード） |

sgnlab（リモート）で回す場合は `scripts/remote/README.md`、運用ルールは `docs/評価運用SOP.md` を参照。

## 使い方（リポジトリ直下から）

```bash
# 0) 動作確認（小グリッド・一気通貫。1 run ≈ 100秒 × 20 jobs / 並列）
uv run python scripts/eval/run_all.py --suite proposed --quick

# 1) 提案手法フルスイープ（4必須LC環境 + straight障害物。385 jobs ≈ ローカル80分）
uv run python scripts/eval/run_all.py --suite proposed --workers 20

# 2) 分流ベースライン（v2 / default / custom を分流で交通効率比較）
uv run python scripts/eval/run_all.py --suite baseline --workers 8

# 3) 既存の out/ から集計以降だけ再実行（図の体裁を変えた時など）
uv run python scripts/eval/run_all.py --skip-sweep
```

個別ステップ（`run_sweep.py` → `aggregate.py` → `make_figures.py` → `export_excel.py`）を
単独で叩くこともできる。成果物はすべて `scripts/eval/out/` 配下
（`raw/` 生CSV・`logs/` 実行ログ・`figures/` 図・`excel/` Excel・各 summary）。

## 評価グリッド（既定 = しっかり）

- 環境（必須LC）：`diverge` / `merge` / `weave` / `weave2`
- 障害物（突発）：`straight` + `--obstacle 1,500,60`（B シナリオ。回避LCは締切達成率の母数外なので安全性で評価）
- Q（総流入）：1500 / 2000 / 2500 / 3000 / 3500 / 4000 [veh/h]
- f（必須LC比率）：0.2 / 0.4 / 0.6
- seed：1–5

## 主要指標（CSV 列）

- `deadline_achievement_rate` … **締切達成率＝衝突なく締切内に完了した必須LC/発生した要求**（中核指標。
  straight障害物は母数0で空）。**衝突に関与した車線変更車両は目標到達済みでも未達成として数える**。
  内訳列: `mandatory_lc_completed`（衝突なし完了）/ `mandatory_lc_collided`（衝突関与）/
  `mandatory_lc_incomplete`（衝突なし未完了＝立ち往生・締切超過）。
  流入締切後にドレーン（最大+900s）してから計上するため、シミュ終了打ち切りによる
  「走行途中の車の失敗誤計上」は含まない（母数＝ゾーンに入って活性化した必須LC。canceled は母数外）
- `raw/<run名>__failures.csv` … **失敗個票**（新定義で未達成の要求ごとに車両ID・分類
  COLLIDED/TIMEOUT_STUCK/EXITED_INCOMPLETE・発生/締切位置・最終状態）。**失敗ゼロの run では作られない**
  ＝このファイルが無いことが 100% の証跡
- **作動包絡（operating envelope）** … 提案手法が必須LC 100% を満たす負荷域（weave Q≤3000 / weave2 Q≤3500 /
  他は全グリッド）。主結果は `summary_scenario_envelope.{csv,md}`、全グリッド（作動限界の明示込み）は
  `summary_scenario.{csv,md}` を見る
- `total_collisions` / `min_TTC` / `TET` … 安全性
- `traffic volume`（スループット）/ `canceled_vehicles` … 容量
  - 注意: `traffic volume` は **departed（入口通過）基準**＝ほぼ供給側。封鎖・渋滞下で
    「捌けているか」を見るときは集計が計算する **`exit_throughput`（exited 基準）** を使う
- `summary_excluded.csv` … 集計から除外された run（クラッシュ・timeout 等）。**空であることを毎回確認**
- `average_speed` / `average_travel_time` … 効率

## 実装フック（評価専用・環境変数。未設定なら従来動作＝golden 不変）

- `EVAL_OUTPUT_DIR` / `EVAL_OUTPUT_NAME` … 出力先と決定的ファイル名（並列衝突なし・冪等・再開可能）
- `EVAL_NO_PLOT` … v1 の time-space 図出力を抑止（高速化）
- `EVAL_SUMOCFG` … v1 の設定ファイル差し替え（ベースラインは `config/v1-fast/`＝ExitLane の人工渋滞を除去した高速版で実行）

## 注意・前提

- v1（高速版）net は **2496m**・v2 diverge は **1000m** と形状が異なるため、交通効率の比較は**平均速度／スループット（対供給）で行い、走行時間の絶対値は直接比較しない**（要 caveat）。
- 実行には `SUMO_HOME` が必要（headless `sumo`）。
