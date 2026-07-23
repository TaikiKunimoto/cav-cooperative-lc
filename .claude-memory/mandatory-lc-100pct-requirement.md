---
name: mandatory-lc-100pct-requirement
description: 研究要件「5シナリオ全てで必須LC完了率100%」と、その達成のための修正4系統・forensic分析手法・修正前データの所在
metadata:
  node_type: memory
  type: project
---

**研究要件（2026-07-24 ユーザー明言）**: 5つの評価シナリオ（diverge / merge / weave / weave2 / straight障害物）
すべてで車線変更操作が **100% 完了**しなければならない。まず 100% を達成し、その上で評価を進める。
環境の長さに対して車両が多すぎることが原因の失敗なら、環境や車両数の調整も選択肢（ユーザー了承済み）。

**Why**: 論文（IPSJ 2026-09 締切）の主張の前提条件。達成率 <100% のままでは評価が成立しない。

**状態（2026-07-24）**: PR #56 でレビュー待ち。フルスイープ結果:
diverge/merge は全グリッドで 100%・straight障害物は衝突0で回避完遂・weave は Q≤3000 で 100%・
weave2 は Q≤3500 で 100%（Q3000f0.6 の1run除く）。残る失敗は織込み超過需要域
（weave Q≥3500 / weave2 Q4000）の11run＝ゾーン長に対する物理容量限界で、
**(a) グリッドを作動包絡内に絞る / (b) 織込みゾーン長を伸ばす** の選択がユーザーの未決事項。

**How to apply**:
- 修正ブランチ `fix/100pct-mandatory-lc`。失敗原因は4系統で、対応する修正が入っている:
  1. シミュ終了打ち切り（走行途中の車を失敗計上）→ ドレーン方式（流入600s締切＋最大+300s掃き出し）
  2. 締切 D が公称長 ≠ 実エッジ長 → traci.lane.getLength の実測値で D を上書き
  3. 織込みのスワップ・ペア デッドロック（対向要求車が壁で相互ブロック→グリッドロック）
     → 対向スワップ（同一step相互changeLane）＋スロット整列＋提供車速度則の修正（通過で明け渡し）
  4. 挿入安全判定の minGap 二重取り（getNeighbors の dist は minGap 控除済み）
     → 閾値=相対制動項+LC_SAFETY_MARGIN(0.5m)。低速高密度域の挿入不能が解消
- 修正前のスイープ一次データは `scripts/eval/out/before_100pct/` にコピー保全済み（raw の .prev 退避も併存）。
- 失敗の再現・分類には scratchpad の forensic ランナー（monkeypatch 計装、リポジトリ非改変）を使った。
  分類: CENSORED / STUCK_AT_WALL / STUCK_QUEUE / ARRIVED_UNRECORDED / COLLIDED。
  再発時は同じ手法で per-vehicle timeline + 挿入チェック詳細を採るのが速い。
- スイープ実行中に作業ツリーを変更しない（SOP §2.3。golden 切り分けで違反し数百jobを即死させた実績あり。
  切り分けは git worktree で行う）。

関連: [[python-toolchain-quirks]]
