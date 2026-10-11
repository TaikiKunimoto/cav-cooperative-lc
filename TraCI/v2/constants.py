"""v2（EDF統一調停）の定数。

v2 は完全自己完結とし、ベースライン（v1/cav）に依存しない。物理定数（v1 と同値）もここで直接定義する。
シナリオ固有の値（本線 edge・長さ・目標レーン・route）は環境ごとに異なるため ``environment.py`` が持つ。
機構依存の評価パラメータ（R, Tc, δ, 活性化マージン）は評価で確定する暫定値（`docs/実装計画_EDF統一調停.md` §7）。
"""

# --- 物理定数（提案/ベースラインで統一。vType(accel/decel/minGap/length) と同値にする）---
MAX_SPEED: float = 27  # [m/s] 最高速度
# 最大加速度。提案は traci 制御（speedMode 0）で vType の accel が直接指令に効かないため、本値が実効上限になる
# （_safe_accel_duration → slowDown の継続時間で accel=MAX_ACCEL にクリップ）。2.6 は SUMO passenger 既定／
# IDM 現実レンジ（Treiber&Kesting 0.8–2.5）に基づく文献値（旧 10.0 は物理限界超の非現実値）。評価で 2.0/2.6/3.0 を感度分析。
MAX_ACCEL: float = 2.6  # [m/s^2] 最大加速度（vType accel と統一）
MAX_DECEL: float = -5.0  # [m/s^2] 最大減速度（vType decel=5.0 と統一）
# 緊急制動の上限減速度。最大減速 |MAX_DECEL| では前車速度へ揃える前にバンパー間が minGap を割るときだけ、
# ここまでの減速を許す（SUMO vType の emergencyDecel 既定値と同じ）。発動回数は結果に出力する。
EMERGENCY_DECEL: float = 9.0  # [m/s^2]
MIN_GAP: float = 2.8  # [m] 最小車間距離（vType minGap と統一。提案は model_post_init で setMinGap）
VEH_LENGTH: float = 5.0  # [m] 車長（vType length と統一）
# SUMO の laneChangeMode 既定値（strategic/cooperative/speedGain/keepRight 有効・traci と衝突時は安全側）。
# off-late が活性化窓進入で車線変更を解禁するときに戻す値（TraCI ドキュメントの default 0b011001010101）
SUMO_DEFAULT_LC_MODE: int = 0b011001010101  # = 1621
FRICTION_COEFFICIENT: float = 0.7  # 摩擦係数（制動距離計算用）
TIME_STEP: float = 0.1  # [s] シミュレーション時間ステップ

# --- 機構依存パラメータ（評価で確定・暫定値）---
R: float = 50.0  # [m] 1回のLCに残す余地（多段LCの実効距離 dist=(D−pos)−(k−1)·R）
TC: float = TIME_STEP  # [s] 調停周期（Tc=シミュレーション刻みで開始、後でstepから分離可能）
DELAY: float = 0.0  # [s] 通信遅延 δ（理想0・段階4で増加。G_req の空走項 v×δ に使う）
ACTIVATION_MARGIN: float = 400.0  # [m] 早め固定活性化: 締切Dの何m手前から要求を活性化するか
# 締切前の保持: 挿入できていない要求車に、締切 D までの実距離がこの値以下のとき「D で HOLD_DECEL の減速で止まれる
# 速度」√(2·HOLD_DECEL·残距離) を速度の上限として与える（止まる計画は立てず、整列・挿入は続ける）。分岐直前まで
# 巡航→lane-drop 端で急停止、を避ける挙動品質用（EDF 鍵には載せない）。0 で無効。
# HOLD_MARGIN は最高速度から HOLD_DECEL で止まる距離 27.78²/(2·2.5)≈154 m を目安にする（各環境の D 未満）。
HOLD_MARGIN: float = 155.0  # [m]
HOLD_DECEL: float = 2.5  # [m/s^2] 保持の減速度（上限速度の曲線を決める。追いつくまでは |MAX_DECEL| まで許す）
# 要求車の整列（前方が足りなければ前方隣接車より ALIGN_DELTA 遅く、後方が足りなければ ALIGN_DELTA 速く）の減速度上限
ALIGN_DECEL: float = 2.0  # [m/s^2]
# --- 提供車の譲り（必要なときだけ・有界に）。要求車の自力挿入が後続側の隙間不足で落ちたときだけ、
# 挿入判定と同じ相対制動の必要量（Safety.net_required）が満たされるまで、減速だけで譲る ---
YIELD_DELTA: float = 3.0  # [m/s] 要求車に対して下げる相対速度（譲りの目標 = 要求車速度 − YIELD_DELTA）
YIELD_MIN_RATIO: float = 0.7  # 譲りの目標速度の下限（要求車速度に対する比）
YIELD_DECEL: float = 3.5  # [m/s^2] 譲りの減速度上限（保持中の要求車（HOLD_DECEL）より速く下がれる値。安全側の追従は別に |MAX_DECEL| まで効く）
YIELD_MIN_SPEED: float = 1.0  # [m/s] これ未満の要求車は停止扱い（減速で合わせず、通過か締切直前の停止で対応）
YIELD_MAX_DURATION: float = 5.0  # [s] 同じ要求車に譲り続ける時間の上限（超えたら譲りをやめ次の後続に交代）
YIELD_COOLDOWN: float = 5.0  # [s] 譲りをやめた提供車が再び提供車に選ばれない時間
# 提供車の協調減速（Layer1 の提供車割当 → Layer2 の _provider_yield）を使うか。0 で Phase B の割当を行わず、
# 要求車は自力挿入・対向スワップ・スロット整列・締切前保持だけで車線変更する（要素の除去による比較用。既定 1）。
COOP_YIELD: float = 1.0
# 要求車のスロット整列（Layer2）: 目標車線の前方隣接車にブロックされている時に付ける相対速度差。
# 同速追従（旧 _requester_match_target_speed）は横並びの重なりを固定してしまい、織込みで対向要求車と
# 速度同調したまま lane-drop 終端に2台並んで到達→相互ブロックの永久デッドロックを生んだ。
# 前方ブロック時は「前方隣接車 − ALIGN_DELTA」まで下げて相対的に後ろへずれ、重なりを解消する。
ALIGN_DELTA: float = 3.0  # [m/s]
# 対向スワップ判定の縦位置窓: 隣接レーンで互いのレーンを目指す2要求車の縦位置差がこの範囲なら
# 「重なり＝通常挿入が双方永久に不可能」とみなし、同一stepの相互 changeLane（アトミック・スワップ）で解消する。
# 車長 + minGap ＝ 相手が居る限り前後どちらの安全ギャップも取れない幾何学的重なりの上限。
SWAP_WINDOW: float = VEH_LENGTH + MIN_GAP  # [m]
# スワップ live 検査で「事実上停止」とみなす速度上限。壁ペア（両者とも締切直前）かつ第三者・自車とも
# これ未満の停止車列に限り、要求ギャップから minGap 分を差し引く（＝物理ギャップ基準。停止車列の
# 自然間隔≈minGap を通し、壁位置で整列した対向ペアの永久拒否＝全域グリッドロックを防ぐ）。
# 緩和を走行中や中間帯の停止ペアへ広げると流全体が組み変わり別条件へ失敗が移動する
# （衝突増・新たな未完了）ことを47run比較で実証済みのため、壁ペア×停止域に限定する。
SWAP_STOPPED_EPS: float = 0.5  # [m/s]
# 車線変更の安全判定に足す1step進化バッファ。判定（コマンド発行時の位置）と changeLane の反映（次step）
# の間に相対位置は最大 Δv·Δt + ½·a·Δt² ≈ 0.3m 動く。マージン0だと境界ぎりぎりの挿入が数cm の
# 食い込み＝側面衝突（SUMO laneChange stage collision）になるため、余裕を持たせる。
LC_SAFETY_MARGIN: float = 0.5  # [m]
# 挿入の後続側ギャップに足す追跡遅れ時間。相対制動モデルは「後続が即時に最大減速する」仮定だが、
# 実際の追従制御は 0.1s 刻みで前車速度を追いかけるため、挿入直後に前車（挿入した要求車）が減速すると
# 追跡ラグ ≈ 数step 分だけ車間を食い込む（rear-end グレーズの主因）。後続速度×本値を余裕として足す。
LC_REACTION_LAG: float = 0.25  # [s]
# 流入終了後のドレーン（掃き出し）上限時間。必須LC の締切達成率を「投入した需要」で正しく測るため、
# 流入は simulation_time で締め切り、ネット上に残る必須LC車が完了/退出するまで最大 DRAIN_MAX 秒だけ
# シミュレーションを継続する（シミュ終了時刻の打ち切りで走行途中の車を「失敗」計上しない）。
# 出口ランプ容量律速の行列（分流 f0.6×高Q）は流入締切後も捌き切りに数百秒かかるため、余裕を持った値に
# する（流入は締切済みなので停止・詰まりが無い限り行列は単調に減り必ず終了する。健全runは残タスク完了で
# 早期終了し、この上限に達するのは異常時のみ）。
DRAIN_MAX: float = 900.0  # [s]

# --- 追従則（相対制動。挿入判定と同じ Safety.net_required を安全車間に使う）---
# 車間が安全車間（net_required + minGap）を上回るときの追従目標 = min(制限速度, 前車速度 + FOLLOW_GAIN·(車間 − 安全車間))。
# 余剰車間に比例して前車より速く詰め、安全車間で前車速度に連続接続する（段差のある目標が生む振動を避ける）。
FOLLOW_GAIN: float = 0.5  # [1/s]
# 追従の時間車頭（desired time headway）。安全車間 = max(相対制動の必要量, v·FOLLOW_TAU) + minGap。
# 相対制動だけだと同速時の車間が 0.25·v + 0.5（27 m/s で約 0.4 s）まで詰まり、非協調ベースライン（Krauss, tau=1.0）と
# 車両物理が揃わない。全車に setTau(1.0) している値（F4 の統一）と同じ 1.0 s を使う。
FOLLOW_TAU: float = 1.0  # [s]
# 車間が安全車間を割ったときに前車速度へ揃える減速度の下限。相対減速 Δv²/(2·(車間 − minGap)) がこれより小さくても
# 最低この減速度で揃え、だらだら接近しない。上限は |MAX_DECEL|
FOLLOW_MIN_DECEL: float = 1.0  # [m/s^2]
