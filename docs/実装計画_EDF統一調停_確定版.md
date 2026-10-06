# EDF統一調停：アルゴリズム仕様

複数シナリオ（合流・分流・織込み・封鎖）の**必須車線変更（MLC）**を、単一の **EDF（最早締切順）優先度機構**で統一的に捌く集中調停アルゴリズムの仕様。各車は目標レーンと締切位置 `(target_lane, deadline_pos)` だけを持ち、調停機構はシナリオ形状に依存しない。実装は自己完結パッケージ `TraCI/v2/`（v1 非依存）。本書を読めば提案アルゴリズムを追えることを目的とする。

全車を traci 制御（`speedMode 0`＝SUMO の安全・LCモデルを無効化し全指令を自前で出す）する前提。

---

## 1. アーキテクチャ

責務を **Layer1（調停）／Layer2（実行）／縦制御・観測** の3層に分ける。

- **Layer1（調停・RSU）** `layer1/`
  - `priority.py: EDF` … EDF 鍵。実効距離 `dist` と鍵 `(dist, −wait, −pos, id)`（`effective_distance` / `make_key` / `order_requests`）。
  - `rsu.py: RSU` … Phase B の鍵順 提供車割当（`arbitrate`／占有印 `claimed`）、毎Tc 役割付与（`apply_roles`）。
  - RSU は**単一スナップショットを1回で調停する集中・静的ロジック**（状態を持たない staticmethod 集合）。Layer1/Layer2 の分離は責務分割（調停 vs 実行）であり通信分散ではない。
- **Layer2（実行）** `layer2/`
  - `pair_executor.py: Layer2` … 各要求車を EDF 順に実行（`execute_pairs`）。挿入安全なら瞬時LC、不可なら 提供車の協調減速・要求車の自己減速・締切間近の hold。
  - `safety.py: Safety` … 安全ギャップ `G_req`（`g_req`）。
- **観測・状態・縦制御** `v2_cav.py: V2CAV` … 自車観測（`update_self_observation`）、活性化（`update_activation`）、車間追従（`control_speed`）、締切達成記録（`update_deadline_achievement`）、障害物化（`make_obstacle`）。
- **スナップショット S_t** `snapshot.py: Snapshot` … Tc 時点の全車観測を凍結し、車線別・縦位置降順インデックス `lane_members` を作る。
- **要求・活性化** `lc_request.py: LCRequest / LCOperation` … 必須LC操作リスト・早め固定活性化窓・待ち時間。
- **環境（形状）** `environment.py: Environment / Group / ENVIRONMENTS` … net・本線 edge/長・必須LC仕様 `(target_lane, deadline_pos)`。**形状（net）と負荷（総流入 Q・必須LC比率 f）を分離**。
- **突発障害物** `obstacle.py: Obstacle` … 動的配置（`place`）と後方車への回避必須LC付与（`escalate`）。
- **パラメータ** `constants.py` … 物理定数＋機構パラメータ（R / TC / DELAY / ACTIVATION_MARGIN / HOLD_MARGIN）。
- **実行** `simulation.py: V2Simulation.run`、エントリ `__main__.py`（`<seed> <inflow> <mlc_ratio> [--env NAME] [--obstacle L,P,T] [--nogui]`）。

### メインループ（毎 step / 毎 Tc 2フェーズ）
各 step（`V2Simulation.run`）:
1. `traci.simulationStep()` ＋ 衝突検出。
2. 到着 / 未発進 / 出発処理と**全車観測**（観測を先に済ませてスナップショット一貫性を担保）。
3. （`--obstacle` 指定時）`Obstacle.place`＝位置到達トリガで停止車両化、`Obstacle.escalate`＝後方車へ回避必須LC付与。
4. **毎 Tc 2フェーズ調停**（§3）。Phase A（鍵計算）→ Phase B（提供車割当・役割付与）。
5. `control_speed`（各車・縦制御, §5）。
6. `Layer2.execute_pairs`（§4）を**最後に**呼び、協調減速・自己減速・hold・changeLane が最終指令になるようにする。

調停周期 `Tc = TIME_STEP = 0.1s`（既定で毎 step 成立＝実質「毎ステップ調停」）。評価で `Tc > TIME_STEP` に分離する余地を残す。

---

## 2. 優先度：EDF 鍵

- **実効距離** `dist = (D − pos) − (k − 1)·R`（D=締切位置、pos=現在縦位置、k=残りLC段数=|現在レーン − 目標レーン|、R=1段分の余地）。多段LC車は残り段数分だけ締切が手前に前倒しされる。
- **鍵** `(dist 小, 待ち時間 大, 縦位置〔前方=大〕, ID)`（`EDF.make_key`）。辞書式・第4要素 ID が一意なので鍵に同点が出ず、処理順に循環が生じない＝**デッドロックフリー**。**車種別・失敗の酷さは鍵に入れない**。
- Phase A は鍵昇順（dist 小＝最も緊急から）にソート（`EDF.order_requests`）。

### k・D ＝ アクティブ操作基準
各車は必須LC操作のリスト `operations: list[LCOperation]`（各 `(target_lane, deadline_pos)`）を持ち、**未完了のうち deadline が最も近い操作**を active とする（`V2CAV.active_operation`）。鍵の D・k はこの active 操作基準で測る。

- **通常の必須LC（多段 lane0→lane2 等を含む）**: 操作は1つ。target は最終目標で、レーン変更に応じて k が段階的に減る。
- **複数操作を持つのは障害物回避が append された車のみ**: 回避操作（`deadline=障害物位置`）は元の目標より締切が近いので active になり、D・k が一時的に回避基準へ切替（回避先は隣レーンゆえ k=1）。障害物通過で回避操作が完了し、active が元の最終目標へ**自動復帰**する。

---

## 3. 調停：2フェーズ（毎 Tc）

- **Phase A（鍵計算）** `Snapshot.capture` → `LCRequest.build_all` → `EDF.order_requests`。
  鍵計算の対象は**活性化窓内（`D − ACTIVATION_MARGIN` 通過かつ目標車線未達）の要求車のみ**（窓外の車は要求を出さない）。
- **Phase B（提供車割当）** `RSU.arbitrate` → `RSU.apply_roles`。
  - 鍵昇順（最も緊急から）に処理し、各要求車に「**次の1段LCの目標車線の後続**のうち、鍵が自分より下位（劣位）かつ今Tc未占有 の最近傍」を提供車に確保する（`RSU._find_provider`）。要求車が停車中は2番目の候補を選ぶ（先頭直後の詰まり回避）。
  - **占有印 `claimed`**（横取り禁止）: 同一提供車の二重割当を防ぐ。
  - **譲歩の伝播**: 既に上位車の提供車として `claimed` された要求車は、今Tc は譲る側に回り自分のLCを見送る。
  - 役割付与（`apply_roles`）は毎Tc フル再構築（提供車=YIELDING / 要求車=LANE_CHANGING）。より緊急な車が来れば提供車は自動で付け替わる（上位優先ゆえ逆転なし）。`status` は縦制御の「加速抑制」表示にのみ使い、**候補除外には使わない**。
  - **提供車は目標車線の後続車に限る**（`_find_provider` は `lane_pos < 要求車pos` のみ）。その協調減速は要求車の**後方**にギャップを開ける（→ §4.3 と相補的）。

---

## 4. 実行：Layer2（`execute_pairs`、各要求車を EDF 順に処理）

目標車線への挿入が安全（前後二方向 OK・§4.1）かつスロット未確保（§4.5）なら `changeLane` で瞬時に1段車線変更。不可なら以下を併用する。

### 4.1 挿入安全判定 `_insertion_safe_live`
目標車線の実・後続/前走を `getNeighbors`（ジャンクション跨ぎで流入車も捕捉）で取得し、前後二方向の安全ギャップを満たすか**実時間判定**する。
`required = MIN_GAP·0.5 + g_req(接近側の速度) × Δv/MAX_SPEED`（接近時のみ加算）。`dist` は minGap 込みの実ギャップ。**接近側ほど大きな車間を要求**する（後続が自分より速ければ後続速度で、自分が先行より速ければ自車速度で `g_req` を評価する非対称な式）。

### 4.2 提供車の協調減速 `_provider_yield`
割当てられた提供車（後方）が `_supporting_speed`（gap 不足分に応じて要求車速度の 0〜30%）まで減速し、要求車が入る gap を**後方**に開ける。

### 4.3 要求車の自己減速 `_requester_match_target_speed`
**要求車自身**が目標レーン先行の速度まで減速し、速度差 Δv を縮めて §4.1 の必要ギャップを下げ、既存ギャップへ滑り込む。提供車（後方限定）の補完：自分が前へ行き過ぎないよう速度を落とすことで、**低速・混雑した目標レーンへも挿入を成立させる**（最高速のまま突っ込むと速度差で常に挿入不可になるのを防ぐ）。

### 4.4 締切間近の hold `_hold_before_deadline`
`dist = EDF.effective_distance(req) ≤ HOLD_MARGIN` でなお挿入できない場合の**最終手段**。締切位置 D の手前で滑らかに停止し、行き止まり（teleport 無効下の lane-drop）での急停止を防ぐ＋D で待って入る猶予を作る（D で停止する減速を `MAX_DECEL` 上限で `slowDown`）。EDF 鍵には載せない（順位不変）。

> §4.3 自己減速と §4.4 hold は、own leader（自車線前方車）が `safety_gap` 内なら**スキップして `control_speed` に委ねる**（弱い減速指令が緊急ブレーキを上書きして減速/hold 車列で追突するのを防ぐ）。遠い間は自己減速で挿入を狙い、締切間近（dist ≤ HOLD_MARGIN）でなお不可なら hold へ移行する。

### 4.5 スロット確保 `committed` / `_slot_free`
同一step・同一目標車線への位置重なり挿入を防ぐ（Layer1 の提供車確保 `claimed` と対称な、Layer2 の目標スロット確保）。`target_lane` キーで源レーン非依存（両側→中央の同時挿入も弾く）。スロットが消費されるのは実際の `changeLane` の瞬間だけなので、これは実行時＝Layer2 の責務。

---

## 5. 縦方向制御 `control_speed`
SUMO の安全制御は無効（speedMode 0）なので車間追従を自前で行う。

- 前方車との距離が `MIN_GAP` 未満 → 緊急減速（前方車速度・自車速度・1.0 の最小へ）。
- 前方車なし → 制限速度へ加減速。
- 前方車あり → 距離が `safety_gap`（=`G_req`）以上なら原則 制限速度へ、未満なら前方車よりやや低い速度（leader 速度 − 1）へ TTC・最大減速時間で減速。
- 協調・車線変更中（YIELDING / LANE_CHANGING）は加速しない（`do_not_speed_up`）。

---

## 6. 活性化（早め・固定）
`D − ACTIVATION_MARGIN` を通過し、かつ目標車線に未達なら活性（`LCRequest.in_activation_window`）。`update_activation` が操作ごとに一度だけ活性化時刻を記録する（待ち時間の起点＝鍵の第2要素）。卒論の動的開始位置（渋滞末尾）ロジックは持たない。

---

## 7. 安全ギャップ `G_req`
`G_req = v×δ + 制動距離 + minGap`（`Safety.g_req`）。空走項は人間の反応時間ではなく**通信遅延 δ**（`DELAY`、理想 δ=0 で空走ゼロ／段階4で増加）。挿入判定（§4.1）と追従（§5 の `safety_gap`）の両方が同一の `G_req` を基準にし、安全車間の定義を1本化している。

---

## 8. 車両物理
提案（traci）とベースライン（SUMO/vType）で物理を統一し、両手法の違いを**LC決定ロジックだけ**にする。

- `MAX_SPEED=27 m/s`、`MAX_ACCEL=2.6 m/s²`、`MAX_DECEL=−5.0 m/s²`、`minGap=2.8 m`、`length=5 m`。
- traci 制御（speedMode 0）では vType の accel は直接指令に効かないため、`MAX_ACCEL` を Python 側の `slowDown` 継続時間で**実効加速上限**としてクリップする。2.6 は SUMO passenger 既定／IDM 現実レンジ（0.8〜2.5）に基づく。

---

## 9. 評価環境（`ENVIRONMENTS`）
**形状（net）＝環境／負荷（総流入 Q・必須LC比率 f）＝パラメータ**で分離。各車は `(target_lane, deadline_pos)` だけを持ち、機構は route 名に依存しない。

| 環境 | name | 本線 edge / 長 | 締切 D | 内容 |
|---|---|---|---|---|
| ① 分流 D | `diverge` | DivergeZone / 1000m | 1000 | 本線3車線、分流車が右端 lane0 へ必須LC → ExitRamp 出口 |
| ② 合流 M | `merge` | MergeZone / 194m | 194 | 本線2車線＋加速車線、合流車が lane0→lane1 へ必須LC |
| ③ 素地 B | `straight` | Road / 1000m | 障害物位置（動的） | 直進3車線。封鎖Bは `--obstacle` で動的発生 |
| ④ 一側織込み MD-1f | `weave` | WeaveZone / 196m | 196 | 補助車線 lane0 で 合流(↑) と 分流(↓) が逆向き交差 |
| ⑤ 両側織込み MD-2 | `weave2` | WeaveZone / 392m | 392 | 4車線(1:2:1)、上下対称の合流・分流＋対角3車線織込み(crossing) |

- **封鎖 B ＝ 動的突発障害物**: 静的 net を作らず `straight` ＋ `--obstacle 'lane,pos,time'` で走行中の1台を停止＝障害物化（形状非依存・突発タイミングをパラメータ化）。
- **障害物エスカレーション**: 障害物配置後、後方・同一レーンの車（through も既存MLC車も）に隣レーンへの回避必須LCを動的付与する。回避先は最終目的地側を優先、不可なら逆側退避、通過後に元の目標へ復帰（§2 のアクティブ操作切替と連動）。

---

## 10. メトリクス
`SimulationStatistics`（v1/v2 共有）で集計し `simulationStatistics/statistics/v2/` に CSV 出力。

- **締切達成率（必須LC完了率）＝ 中核指標**: 母数＝活性化した非回避操作、分子＝締切位置までに目標レーン到達（`update_deadline_achievement` / `record_deadline_outcome`）。提案 vs LC2013 の主比較指標。
- 衝突数・巻込み台数、TTC（min_TTC・TET）、全体平均速度、公平性、未発進(canceled)台数。
- **テレポート無効方針**: 両手法とも `--time-to-teleport -1`。SUMO の介入を排し、stuck 車を running として残して失敗を可視化する。失敗は締切未達・stuck・衝突として現れる（テレポート数は代理指標にすぎず指標から外す）。

---

## 11. パラメータ（評価で確定）

| 記号 | 定数 | 既定 | 役割 |
|---|---|---|---|
| R | `R` | 50.0 | 多段LCで1段に残す余地（dist の前倒し量） |
| δ | `DELAY` | 0.0 | 通信遅延（G_req 空走項。段階4で増加） |
| Tc | `TC` | 0.1（=TIME_STEP） | 調停周期 |
| 活性化マージン | `ACTIVATION_MARGIN` | 400.0 | D の何m手前から要求を活性化するか |
| hold マージン | `HOLD_MARGIN` | 100.0 | D 手前で hold へ移行する dist 閾値 |

> 距離系 margin（`ACTIVATION_MARGIN`・`R`・`HOLD_MARGIN`）は締切区間長 D に依存する（現行 D = diverge 1000・merge 194・weave 196・weave2 392）。複数 seed・負荷掃引で頑健性を確認し、絶対値か D 比率か・シナリオ別かを評価で確定する。

---

## 12. 保証と将来

**保証**: デッドロックフリー（鍵一意）／二重割当なし（`claimed`）／feasible なら全員成功／容量超過は劣化（hold で待機）／衝突は安全層（挿入安全・緊急減速）が防ぐ。

**ベースライン**: ① 集中版（提案）／② SUMO デフォルト LC2013（`TraCI/v1/default.py`）。同一 net/rou・同一指標（テレポート無効・締切達成率・衝突・平均速度・TTC）で比較する。

**段階4（通信・未着手）**: δ・パケットロス注入、差分配信の通信量、2層分散版（局所化）で集中版との優位性を評価する。
