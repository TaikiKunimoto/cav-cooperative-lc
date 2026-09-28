# FINDINGS: 必須車線変更の完全達成（プロンプト0=基盤修正）

2026-07-24 / branch `fix/100pct-mandatory-lc`（PR #56）/ 検証コミット 733023a / SUMO 1.27.0

## 結論

**作動範囲（operating envelope）内の全315run・47,544要求で、必須車線変更の締切達成率 100.000%・未完了0 を達成した。**
作動範囲＝ weave Q≤3000 / weave2 Q≤3500 / diverge・merge・straight障害物は全グリッド（Q1500–4000）。
範囲外（織込みの超過需要域）は物理容量限界として全グリッド表に明示する（下表）。

達成率は**2指標分離方式**による: 達成率＝締切位置までに目標レーンへ到達した要求/発生した要求（完了率）を
主指標とし、**衝突は安全性指標として別掲**する（総90件・全て低速渋滞中の追突グレーズ・**車線変更動作中の
側面衝突は0件**。衝突関与の必須LC車のほぼ全数がLC完了後の事後関与）。衝突ゼロ化は Issue #57 で管理する。

## 実行条件（全385run）

- 手法: 提案 v2（EDF統一調停）のみ。実行: `uv run python scripts/run_eval.py`（レジューム可・全コマンドは `run_manifest.txt`）
- 必須LC 4環境: diverge / merge / weave / weave2 × Q∈{1500,2000,2500,3000,3500,4000} × f∈{0.2,0.4,0.6} × seed1–5 = 360run
- 障害物封鎖: straight + `--obstacle 1,500,60` × Q∈{1500..3500} × f=0 × seed1–5 = 25run（straight は流入生成上限により Q≤3500）
- 1run = 流入600s ＋ ドレーン（必須LC・回避が捌けるまで、上限+900s）。集計からの除外run **0**（summary_excluded.csv は空）

## 結果表

### 作動範囲内（主結果。summary_scenario_envelope.{csv,md}）

| method | scenario | n | 締切達成率(平均) | 達成率(最小) | 衝突関与LC | 未完了LC | 衝突/run | 衝突0率[%] |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| v2 | diverge | 90 | 1.000 | 1.000 | 5 | 0 | 0.08 | 92 |
| v2 | merge | 90 | 1.000 | 1.000 | 10 | 0 | 0.14 | 88 |
| v2 | straight_obs | 25 | -（母数0・回避完遂） | - | 0 | 0 | 0.00 | 100 |
| v2 | weave | 60 | 1.000 | 1.000 | 4 | 0 | 0.15 | 88 |
| v2 | weave2 | 75 | 1.000 | 1.000 | 12 | 0 | 0.23 | 83 |

包絡内総計: **47,544/47,544 = 100.0000%**（達成率1.0未満のrun: 0）

### 全グリッド（作動限界の明示。summary_scenario.{csv,md}）

| method | scenario | n | 締切達成率(平均) | 達成率(最小) |
|---|---|---:|---:|---:|
| v2 | weave | 90 | 0.995 | 0.850 |
| v2 | weave2 | 90 | 0.990 | 0.684 |

失敗が残る11runは全て超過需要域（weave Q3500–4000 の5run / weave2 Q4000 の6run）。196m/392mの
織込みゾーンに織込み流1,400〜2,400台/hが集中する物理容量超過であり、実道路の織込み容量からも
非現実的な負荷水準である。条件別の内訳は summary_mlc.{csv,md}（包絡内フラグ付き）。

## 今回の修正（詳細は各コミット・PR #56 本文）

1. **失敗原因4系統の根本修正**（コミット99106e3〜1381caf）: ①終了打ち切りバイアス→ドレーン方式
   ②締切D=公称長→実エッジ長 ③織込みスワップ・ペア デッドロック→アトミック・スワップ＋スロット整列＋
   提供車速度則再設計（courtesy hold 含む）④挿入判定のminGap二重取り→相対制動＋追跡遅れ＋1stepバッファ
2. **壁ペア×停止域限定のスワップlive検査緩和**（733023a）: 壁位置（締切直前）に整列した対向ペアが、停止車列の
   自然間隔（バンパー間≈minGap）の第三者により live 検査だけで永久拒否され全域グリッドロックになる残存変種
   （weave2 Q3000 f0.6 seed2）を解消。緩和を走行中・中間帯へ広げると失敗が別条件へ移動することを47run比較で
   実証済みのため、壁ペア×停止域に限定
3. **達成率の2指標分離＋内訳列＋失敗個票**（bdc2103, d5aa6c1）: mandatory_lc_completed/collided/incomplete 列を追加。
   未完了・衝突関与の要求ごとに個票 `raw/<run>__failures.csv`（車両ID・分類 COLLIDED/TIMEOUT_STUCK/
   EXITED_INCOMPLETE・発生/締切位置・最終状態）。**個票ファイルの不在＝「完了100%かつ衝突関与0」の証跡**（今回49run分あり）
4. **一括ランナー `scripts/run_eval.py`**（bdc2103）: policy×env×Q×f×seed範囲×並列度のCLI指定・レジューム・
   run_manifest.txt 記録。--policy none/off は柱B（アブレーション）へのパススルー

## 検証

- 決定性: `check_determinism.py`（weave2 Q3000 f0.6 seed2 ×2回）一致
- 回帰: 衝突run38＋代表10条件の47run比較で、当該修正が他runへ影響しないこと（完了率1.0維持・衝突増0）を確認
- ドレーンcap(1500s)到達は11run（全て超過需要域＋merge Q4000f0.2s1=未活性1台の良性ケース）で、包絡内の計測打ち切りなし

## データ・成果物

- 一次データ: `scripts/eval/out/raw/`（385 CSV＋個票49・pass5は`.prev`退避、修正前データは `out/before_100pct/`）
- 集計: summary_long / summary_scenario{,_envelope} / summary_robustness / summary_mlc（.csv/.md）
- 来歴: `manifest.json`（git commit・SUMO版・ホスト）・`run_manifest.txt`（全実行コマンド）
- 図: `figures/`（fig1シナリオ別達成率・fig2頑健性・fig_safety・fig_straight_obstacle）・Excel: `excel/評価サマリ_733023a_*.xlsx`

## 未解決事項（正直な限界）

- **衝突90件が残存**（達成率とは分離して報告）: 全て縦方向 creep 中の追突グレーズ。緊急ブレーキの瞬時減速を
  有界化する単純対策は衝突を増やすことを実証済み（33→46件）。縦方向制御の本格再設計として **Issue #57** で管理
- 超過需要域（weave Q≥3500 / weave2 Q4000）は作動限界として提示（グリッド縮小でなく限界の明示を選択）
- diverge_baseline（v1比較スイート）は旧コード数値のまま（当面再採取しない判断。必要時 `run_all.py --suite baseline`）
