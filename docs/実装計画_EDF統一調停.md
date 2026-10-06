# 実装計画：EDF 統一調停（確定アルゴリズム）

作成 2026-06-13。**この `cav-cooperative-lc` リポジトリに、確定した提案アルゴリズムを実装する**ための計画。Claude Code で段階的に実装する前提で、現状コードの所在・変更点・段階スライス・検証を file:line 付きで記す。

確定仕様の一次資料（別フォルダ `修論壁打ち/`）。要点は §2 に転記済み:
`優先度アルゴリズム_確定.md` / `コア機構_決定事項.md` / `システムモデル_決定事項.md` / `SUMOデフォルト_LC2013_との差分.md`

---

## 1. 現状コードの地図（実装前の事実・確認済み）

**ドライバ（`TraCI/`）**
- `custom.py` … 提案モデル（本実装の対象）
- `default.py` … SUMO 既定 LC を使うモデル ＝ **評価ベースライン②（LC2013）**。`cav/default_cav.py:105` で `setLaneChangeMode(...,0b011000010101)` ＝ SUMO の戦略的/協力的 LC を有効化。
- `simple.py` … 卒論系の別バリアント（提案同様に SUMO LC を無効化）。

**CAV クラス（`TraCI/cav/`）**
- `base_cav.py` … `BaseCAV` ＋ `params`。`_calculate_safety_gap`（base_cav.py:76-84）。
- `custom_cav.py` … `CustomCAV`（提案）。
- `default_cav.py` / `simple_cav.py` … 各ベースライン。
- `constants.py` … `MIN_GAP=2.8` / `MAX_DECEL=-5.0` / `REACTION_TIME=0.75` / `MAX_SPEED` 等。
- `status/status.py` … `CarStatus{NORMAL, YIELDING, LANE_CHANGING}` / `CarAction` / `LaneChangeStatus`。

**メインループ（`custom.py` 〜135-150）** … 現状は**挿入順・1パス**:
```
for veh in state.vehicle_instance:
    veh.update_status(congestion_point)
    veh.decide_next_action_and_priority()   # 優先度決定＋ペア形成(_decide_yielding_vehicle)
    veh.execute_lane_change(lane_change_history)
    veh.control_speed()
```
→ **緊急度ソートなし・先着が譲り役を取る**。コア機構_決定事項.md §5 が指摘する「穴」。

**現在の優先度** … 静的スカラー `priority 0–7`（`custom_cav.py:60-150` のルールテーブル＋`:187-191` で「分岐50m手前で未達→priority=7」）。

**現在のペア形成** … `_decide_yielding_vehicle`（`custom_cav.py:556-611`）。候補＝目標車線後続で `priority<自分 かつ status∈{NORMAL,LANE_CHANGING}`、最近傍を確保（要求車 speed=0 なら2番目）、確保車を `status=YIELDING`。

**安全gap** … `_calculate_safety_gap`（`base_cav.py:76-84`）＝ `reaction(=speed×0.75) + braking(=v²/(254·μ)) + minGap`。挿入時の前後二方向 gap 判定は `custom_cav.py:660-695`。

**SUMO LC 無効化** … `custom_cav.py:61-62` `setLaneChangeMode=0 / setSpeedMode=0`（提案は TraCI 全制御）。

**シナリオ** … `config/` は**分流のみ**（`high-way.net.xml`・本線2500m）。M（合流）/B（封鎖）の net・rou は未整備。

---

## 2. 確定アルゴリズムの要約（実装ターゲット）

- **優先度＝EDF**。実効距離 `dist = (D − pos) − (k − 1)·R`（D=締切位置、pos=現在縦位置、k=残りLC回数=|現在レーン−目標レーン|、R=1回分の余地）。
- **鍵 ＝ (dist 小, 待ち時間 大, 縦位置〔前方=大〕, ID)**。辞書式・一意 → デッドロックフリー。**種別・酷さは使わない**。
- **執行＝毎 Tc 2フェーズ**。Phase A＝全要求車の Key を同一スナップショットで計算。Phase B＝Key 順（dist小から）に処理し、各要求車に「目標車線後続のうち Key が自分より下位 かつ 今パス未確保 の最近傍」を譲り役に確保（停車中は2番目）。
- **毎 Tc 再割当＋差分配信**。より緊急な車が来れば提供車は自動で付け替わる（**上位優先ゆえ逆転なし**）。`status` は Layer2 の役割表示に降格＝**候補除外に YIELDING を使わない**。
- **G_req ＝ (v×δ) ＋ 制動距離 ＋ minGap**（前後二方向）。**人間の空走(0.75s)を除去**、反応距離は通信遅延 δ（理想 δ=0 でゼロ、段階4で増加）。
- **Θ_force** … 順位ではなく「dist ≤ Θ_force かつ枠なし → 劣化（安全減速で待機）」の挙動閾値。
- **活性化＝早め・固定**（必須LC判明時）。卒論の動的開始位置はコア外（システムモデル §4 改訂）。
- **保証**：デッドロックフリー／二重割当なし／feasible なら全員成功／超過は劣化／衝突は安全層。
- **ベースライン**：①集中版（提案）②SUMOデフォルト LC2013（`default.py`）。指標に**テレポート数**（LC2013 は密区間で wrong-lane テレポート＝破綻）。

---

## 3. 現状 → 目標のギャップ（変更マップ）

| 項目 | 現状 | 目標 | 主な変更ファイル |
|---|---|---|---|
| 調停の順序 | 挿入順・1パス | 毎Tc 2フェーズ（鍵計算→ソート→上位から割当） | `custom.py`（メインループ） |
| 優先度 | 静的スカラー 0–7 | EDF `dist` ＋ 鍵 (dist,待ち,縦位置,ID) | `custom_cav.py` |
| ペア形成 | 各車独立 `_decide_yielding_vehicle`・status で除外 | 中央の順序付き割当・占有印（今パス未確保）・YIELDING除外なし | `custom.py` ＋ `custom_cav.py` |
| 安全gap | reaction=0.75s 固定 | reaction=v×δ（δ=0でゼロ） | `base_cav.py` ＋ `constants.py` |
| Θ_force | priority=7 トリガ | dist≤Θ_force の挙動/劣化閾値 | `custom_cav.py` |
| シナリオ | 分流のみ | M/B net・rou 追加 | `config/` |
| 指標 | 既存 | ＋テレポート数・締切達成率 | `simulationStatistics/` |

---

## 4. 変更タスク（ファイル単位）

- **T1 2フェーズ化（最重要・`custom.py`）**：メインループの per-veh `decide→execute` を分解。(A) 全車 `update_status` ＋ `Key` 計算。(B) 要求車を Key で降順ソート → 上位から `_decide_yielding_vehicle`（中央の `claimed` 集合を共有）→ `execute_lane_change`。`control_speed` は各車。
- **T2 EDF鍵（`custom_cav.py`）**：`dist=(D−pos)−(k−1)·R` を計算するメソッド追加（D＝route/scenario から締切位置、k＝|lane−target_lane|）。`Key=(dist, wait_time, lane_pos, id)`。`wait_time`＝活性化からの経過（`params` に活性化時刻を追加）。静的 priority テーブル（:60-150）と `:187-191` の 7 付与を置換。
- **T3 候補フィルタ＆再割当（`custom_cav.py:556-611` ＋ `custom.py`）**：候補＝`Key(veh) が自分より下位 かつ veh が今パスで未 claimed`（YIELDING 除外を撤廃）。毎 Tc フル再割当（前 Tc のペアを引き継がず、Phase B で取り直す）。`status` は割当結果から付与（提供車＝YIELDING、要求車＝LANE_CHANGING）。
- **T4 G_req δ化（`base_cav.py:80` ＋ `constants.py`）**：`reaction_distance = speed × DELAY`（`DELAY=δ`、既定 0）。`REACTION_TIME` 依存を除去。`braking + minGap` は維持。挿入判定（`custom_cav.py:660-695`）の前後二方向はそのまま、係数 `1.5×minGap`・`speed_diff/MAX_SPEED` は執筆前に整理（コメント）。
- **T5 Θ_force 挙動閾値（`custom_cav.py`）**：`dist ≤ Θ_FORCE` で committed 挙動（減速して待つ）＝劣化。順位には載せない（鍵は dist が決める）。
- **T6 多シナリオ（`config/`）**：合流 M・封鎖 B の `net.xml`/`rou.xml`/`sumocfg` を追加。締切位置 D の定義をシナリオ別に（出口/加速車線端/障害物位置）。
- **T7 計測（`simulationStatistics/`）**：`traci.simulation.getStartingTeleportNumber()`/`getEndingTeleportNumber()` でテレポート数、締切達成率（必須LC完了/要求）、TTC、平均速度を集計。
- **T8 ベースライン確認（`default.py`）**：LC2013 ベースラインとして稼働確認。同一 net/rou・同一指標で比較できるよう統計を揃える。

---

## 5. 段階スライス（実装順）

- **段階1（最小・既存分流 net）**：T1+T2+T3+T4 を**現行の分流シナリオ**に適用。集中版が成立し、挙動が現行より悪化しない（テレポート 0 維持・締切達成率）ことを確認。＝確定アルゴリズムのコア。
- **段階2（複合）**：T6 で M/B net 追加 → ウィービング（M+D）で統一調停を確認。G_req 係数・R・Θ_force を評価で詰める。
- **段階3（突発障害物）**：B を動的に発生させ頑健性。
- **段階4（通信）**：δ・パケットロス注入、差分配信の通信量、2層分散版（局所化）で集中版との優位性。

---

## 6. 検証（各段階の合格条件）

- **割当一意（ユニットテスト）**：Phase B 後、同一提供車が2要求車に割り当たっていない。鍵に同点が無い。
- **EDF 順序（ログ）**：処理順＝dist 昇順になっている。逆転（緊急車が余裕車を待つ）が起きていない。
- **段階1 回帰**：現行分流で締切達成率が悪化しない／テレポート 0。
- **ベースライン対比**：密な複合シナリオで `default.py`(LC2013)＝テレポート発生、提案＝0（`SUMOデフォルト_LC2013_との差分.md` §4）。
- **安全**：衝突 0（挿入時 gap チェック＋緊急減速）。

---

## 7. 未確定パラメータ（評価で決定・コードは既定値で先行）

`R`（1回分の余地）、`Θ_FORCE`（M/B/D 別）、`δ`（通信遅延・既定0）、`G_req` 係数（`1.5×minGap`・速度差正規化）、`Tc`（調停周期・既定 0.1s）、活性化マージン。

---

## 8. 落とし穴・注意

- **priority(0–7) の参照箇所**：削除前に `grep -n "priority" TraCI/cav/custom_cav.py` で `control_speed`・status 遷移・`execute_lane_change` が priority に依存していないか確認し、Key ベースに置換。
- **YIELDING の扱い**：候補除外に使わない（再割当＋上位優先が逆転を防ぐ）。ただし Layer2 の挙動（提供車の協調減速）では status を見る。
- **活性化**：卒論の動的開始位置（`custom.py` の `_get_congestion_point`/渋滞末尾、400m/70m）はコア外＝早め固定活性化に簡素化（システムモデル §4 改訂）。渋滞末尾ロジックは段階4の評価で必要時のみ「締切=列末尾」として復活。
- **停車中の例外**：要求車 speed=0 のとき2番目に近い候補を選ぶ既存例外は維持（`_decide_yielding_vehicle`）。
- **差分配信**：単一プロセス sim では機能上は毎Tc再割当でよい。差分配信は段階4の通信量カウントの意味づけ。
