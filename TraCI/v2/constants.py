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
MIN_GAP: float = 2.8  # [m] 最小車間距離（vType minGap と統一。提案は model_post_init で setMinGap）
VEH_LENGTH: float = 5.0  # [m] 車長（vType length と統一）
FRICTION_COEFFICIENT: float = 0.7  # 摩擦係数（制動距離計算用）
TIME_STEP: float = 0.1  # [s] シミュレーション時間ステップ

# --- 機構依存パラメータ（評価で確定・暫定値）---
R: float = 50.0  # [m] 1回のLCに残す余地（多段LCの実効距離 dist=(D−pos)−(k−1)·R）
TC: float = TIME_STEP  # [s] 調停周期（Tc=シミュレーション刻みで開始、後でstepから分離可能）
DELAY: float = 0.0  # [s] 通信遅延 δ（理想0・段階4で増加。G_req の空走項 v×δ に使う）
ACTIVATION_MARGIN: float = 400.0  # [m] 早め固定活性化: 締切Dの何m手前から要求を活性化するか
# 枠が取れず提供車も無い要求車を、締切D手前で滑らかに減速・保持し始める実効距離 dist の閾値（F5）。
# 「Dからの距離マージン」で、分岐直前まで巡航→SUMOトポロジ（lane-drop）で急停止、を避ける挙動品質用（EDF鍵には載せない）。
# 暫定100m（評価で確定）。停止距離 v_max²/(2|MAX_DECEL|)≈73m 以上かつ各環境の D 未満で機能する（現行 D: merge194/weave196/weave2392/diverge1000 はいずれも成立）。
HOLD_MARGIN: float = 100.0  # [m]（暫定・評価で確定）
# 要求車のスロット整列（Layer2）: 目標車線の前方隣接車にブロックされている時に付ける相対速度差。
# 同速追従（旧 _requester_match_target_speed）は横並びの重なりを固定してしまい、織込みで対向要求車と
# 速度同調したまま lane-drop 終端に2台並んで到達→相互ブロックの永久デッドロックを生んだ。
# 前方ブロック時は「前方隣接車 − ALIGN_DELTA」まで下げて相対的に後ろへずれ、重なりを解消する。
ALIGN_DELTA: float = 2.0  # [m/s]
# 対向スワップ判定の縦位置窓: 隣接レーンで互いのレーンを目指す2要求車の縦位置差がこの範囲なら
# 「重なり＝通常挿入が双方永久に不可能」とみなし、同一stepの相互 changeLane（アトミック・スワップ）で解消する。
# 車長 + minGap ＝ 相手が居る限り前後どちらの安全ギャップも取れない幾何学的重なりの上限。
SWAP_WINDOW: float = VEH_LENGTH + MIN_GAP  # [m]
# 車線変更の安全判定に足す1step進化バッファ。判定（コマンド発行時の位置）と changeLane の反映（次step）
# の間に相対位置は最大 Δv·Δt + ½·a·Δt² ≈ 0.3m 動く。マージン0だと境界ぎりぎりの挿入が数cm の
# 食い込み＝側面衝突（SUMO laneChange stage collision）になるため、余裕を持たせる。
LC_SAFETY_MARGIN: float = 0.5  # [m]
# 流入終了後のドレーン（掃き出し）上限時間。必須LC の締切達成率を「投入した需要」で正しく測るため、
# 流入は simulation_time で締め切り、ネット上に残る必須LC車が完了/退出するまで最大 DRAIN_MAX 秒だけ
# シミュレーションを継続する（シミュ終了時刻の打ち切りで走行途中の車を「失敗」計上しない）。
DRAIN_MAX: float = 300.0  # [s]
