---
name: mandatory-lc-100pct-requirement
description: "研究要件「5シナリオ必須LC100%」— 作動範囲内100%達成済み(pass6)・PR#56レビュー待ち・2指標分離・修正の全体像・forensic/検証ツールの所在・運用の罠"
metadata: 
  node_type: memory
  type: project
  originSessionId: d7db6234-ee5a-41cb-bf74-40b65202abaa
  modified: 2026-07-23T23:40:04.610Z
---

**研究要件（2026-07-24 ユーザー明言）**: 5評価シナリオ（diverge / merge / weave / weave2 / straight障害物）
すべてで必須車線変更が **100%完了**すること。環境長に対する車両過多が原因なら環境・車両数の調整も選択肢
（ユーザー了承済み）。

**Why**: 論文（IPSJ 2026-09締切）の主張の前提条件。達成率<100%のままでは評価が成立しない。

**状態（2026-07-24 08:45・プロンプト0完遂）**: **作動範囲内100%達成・PR #56 レビュー待ちのみ**
（branch `fix/100pct-mandatory-lc`・9コミット・MERGEABLE・push済）。
- 最終スイープ（**pass6**, `scripts/eval/out/`, commit 733023a→docs c92346c）: **385run・除外0・決定性一致**。
  **作動範囲内（weave Q≤3000 / weave2 Q≤3500 / 他は全グリッド）の全315run・47,544要求で
  締切達成率 100.0000%・未完了0**。全グリッドで失敗が残るのは超過需要域の11runのみ
  （weave Q3500–4000 / weave2 Q4000。196m/392mゾーンに織込み流1,400–2,400台/h＝物理容量超過、作動限界として明示）。
- 衝突は総90件（修正前と同水準・**側面衝突0**）を安全性指標として別掲（2指標分離）。straight障害物は衝突0。
- **旧未決4件は全て決着（2026-07-24 ユーザー回答）**: ①超過需要域→(a)作動範囲(envelope)で提示
  ②PR#56→rebase済み・レビューは本人 ③diverge_baseline→当面再採取しない ④excluded遺物→集計側フィルタで掃除（空に）。
- 納品済み: Drive `修論評価/DPSWS2026評価_2026-07/00_logs/`（FINDINGS.md・summary一式・run_manifest.txt・
  manifest.json・個票49run分・Excel）。ローカルは `../評価結果_dpsws2026/00_logs/`。
- DPSWS2026 runbook のプロンプトA〜C の前提「fix/deadline-100 が main にマージ済み」は
  **PR #56（branch名は fix/100pct-mandatory-lc）のマージがそれに相当**する。

**修正の中身**（詳細は PR #56 本文。5コミット・v2のみ・v1バイト一致確認済み）:
1. 打ち切りバイアス → ドレーン方式（流入600s締切・挿入待ち残は remove→canceled 確定、
   必須LC/回避が捌けるまで最大 **+900s** 継続=`DRAIN_MAX`）
2. 締切D=公称長 < 実エッジ長 → `traci.lane.getLength` 実測で D を上書き
3. 織込みスワップ・ペア デッドロック → 同一step相互changeLane（アトミック・スワップ）＋RSUはスワップ相手を
   提供車にしない＋スロット整列（対向にはフォールバック/前進、通常流には速度合わせ）＋提供車速度則の再設計
   （不足=比例で強く譲歩／停止要求車には前方余地があれば**通過**、詰まりなら **courtesy hold**／大余剰は安全に詰める）
4. 挿入判定の minGap 二重取り（getNeighbors の dist は minGap 控除済み）→ 閾値=相対制動項＋追跡遅れ項
   (v_back×`LC_REACTION_LAG`0.25s)＋`LC_SAFETY_MARGIN`0.5m

追加修正: スワップ第三者検査に junction 越境の実測ギャップ `_swap_live_gap_ok`（不可視車両との重なり交換=側面衝突を全滅）
／スワップ静的要求を minGap→制動項+余裕に緩和（停止車列で20cm差の循環待ち解消）／追従に臨界制動バンド=
**前車速度への**最大減速マッチング（目標0の全停止プロファイルは衝撃波を増幅し織込み流入が崩壊する。一度やらかした）
／`slow_down` に duration 上限クランプ（微速時 v/decel 溢れ→'Invalid time interval' 接続死の防止）。

**やってはいけない（2026-07-24 実証）**: 緊急ブレーキ（gap<MIN_GAP時の `_emergency_brake` の瞬時 setSpeed ジャンプ）を
「有界減速＋ギャップ回復則」に置換する試み → **衝突が増える**（47run比較で関与LC 33→46・総衝突61・14runで増加・
canceled も乱れる）ため差し戻し済み。瞬時ジャンプは当該ペアの接触を確実に防ぐ magic brake で、除去すると第一接触が増える。
creep衝突（追突グレーズ、SUMO警告 decel>wished が直前に出る）の解消は縦方向制御の本格再設計が必要な難所。

**達成率の定義（2026-07-24 ユーザー判断＝2指標分離）**: 達成率＝**締切内完了/発生**（完了率）を論文の主指標とし、
衝突は安全性指標（total_collisions・mandatory_lc_collided=衝突関与車の要求数の参考列）として**別掲**する。
根拠: 包絡内の衝突関与LC 33件中32件は**締切内完了後の事後関与**（もらい事故含む）で調停機構の失敗ではない。
runbookタスク2の字義（衝突込み定義）だと包絡内99.88%になるため分離を選択。
**衝突ゼロ化（縦方向制御の再設計）は Issue #57 で将来タスクとして管理**（忘れない、がユーザー指示）。
weave2 Q3000f0.6s2（包絡内唯一の完了失敗24件）は**全車 speed=0 の全域グリッドロック**（位置7.8〜396.9mに静止・
canceled94・drain cap到達）＝離散デッドロックの残存変種 → forensic で修正を試みる（ユーザー承認済み。
純粋な容量飽和と判明したら包絡精密化=weave2 f0.6のみQ≤2500 へフォールバック）。

**ツール**（`.claude-memory/tools/` に保全。旧 scratchpad 由来・リポジトリ非改変の monkeypatch 計装）:
- `forensic.py` — 失敗の車両別分類（OK/COLLIDED/ARRIVED_UNRECORDED/STUCK_AT_WALL/STUCK_QUEUE/CENSORED）＋
  timeline.jsonl＋挿入チェック詳細。
  `cd TraCI && uv run python ../.claude-memory/tools/forensic.py <seed> <Q> <f> --env <env> [--obstacle L,P,T]`
- `verify_sweep.py` — スイープ後の env別100%チェック・LC失敗run列挙・衝突run・ドレーンcap到達・excluded の一括検証

**運用の罠**:
- seed は**文字列のまま** `random.seed()` へ渡る（`random.seed("2")`）。再現時も同じ渡し方をする
- ドレーンcap(600+900=1500s)到達はログ末尾 `TIME: 1500.0` で検知。merge Q4000f0.2s1 は cap 到達だが失敗0
  （ランプ上で未活性化の1台のみ=良性）
- golden(tests/golden) は macOS 採取 snapshot のため Linux では FAIL するが本件と無関係（追いかけない）
- スイープ実行中に作業ツリーを変更しない（SOP§2.3。ImportError で数百job即死の前科。切り分けは git worktree）
- リグレッション代表条件（再検証はこのセット。現行コードで100%確認済み）:
  weave2 Q1500f0.6s2／Q3500f0.6s2／Q4000f0.4s5、weave Q2000f0.6s4／Q2500f0.4s1／Q3000f0.2s1、
  merge Q2000f0.6s5／Q3500f0.4s3、diverge Q3500f0.2s2／Q4000f0.6s2

**データ所在**: 現行=pass5 `scripts/eval/out/`。修正前一次データ=`scripts/eval/out/before_100pct/`
（ローカルのみ・gitignore済）と `out/raw/*.prev`。

関連: [[python-toolchain-quirks]] [[v2-evaluation-2026-06]] [[no-direct-push-to-main]]
