"""安全ギャップ G_req（式C）と、次の1段LCの目標車線計算。

G_req = 空走(v×δ) + 制動距離 + minGap。人間の反応時間(0.75s)は除き、空走は通信遅延 δ（理想 δ=0 でゼロ）。
挿入時の前後二方向の安全判定は、フィーダー流入車も見えるよう実測（getNeighbors）で行うため Layer2（pair_executor）に置く。
"""

import math

from status.status import CarAction
from v2.constants import (
    DELAY,
    FRICTION_COEFFICIENT,
    HOLD_DECEL,
    LC_REACTION_LAG,
    LC_SAFETY_MARGIN,
    MAX_DECEL,
    MIN_GAP,
    VEH_LENGTH,
)
from v2.lc_request import LCRequest


class Safety:
    """安全層：安全ギャップ G_req（式C）と次の1段LCの目標車線（状態を持たない静的ロジック）。"""

    @staticmethod
    def g_req(speed: float) -> float:
        """安全ギャップ G_req（式C）= 空走(v×δ) + 制動距離 + minGap。δ=0（理想通信）で空走項はゼロ。"""
        speed_kmh = speed * 3.6
        reaction = speed * DELAY
        braking = (speed_kmh**2) / (254.016 * FRICTION_COEFFICIENT)
        return reaction + braking + MIN_GAP

    @staticmethod
    def net_required(v_back: float, v_front: float) -> float:
        """minGap 控除済みギャップに対する必要量 ＝ 相対制動項 ＋ 追跡遅れ ＋ 1step進化バッファ（相対制動モデル）。

        後続 v_back が前方 v_front へ最大減速で追突しない車間の minGap 超過分::

            max(0, (v_back² − v_front²) / (2·|MAX_DECEL|)) + v_back × LC_REACTION_LAG + LC_SAFETY_MARGIN

        相対制動モデルは「後続の即時最大減速」を仮定するが、実際の追従制御は前車速度を 0.1s 刻みで追いかけるため、
        前車が減速し始めると数step 分のラグで食い込む。その分を ``v_back × LC_REACTION_LAG`` として足す
        （rear-end グレーズの防止。停止・微速域では ~0 なので詰まった車列への挿入可能性は保たれる）。
        判定と changeLane 反映の1stepズレに対する ``LC_SAFETY_MARGIN`` も足す（境界挿入の側面衝突防止）。

        挿入判定・対向の 2 台の順番（Layer2）と追従（``V2CAV.control_speed`` の安全車間）がこの 1 本の定義を使う
        （層の間で安全の定義を食い違わせない）。
        """
        braking = max(0.0, (v_back**2 - v_front**2) / (2 * abs(MAX_DECEL)))
        return braking + v_back * LC_REACTION_LAG + LC_SAFETY_MARGIN

    @staticmethod
    def stop_pos_behind(requester_pos: float) -> float:
        """止まった要求車を入れるために、目標車線の後続が前端を止める位置（要求車の後端の minGap ＋ 2·LC_SAFETY_MARGIN 手前）。

        そこで止まった後続との控除済み距離は 2·LC_SAFETY_MARGIN になり、停止車どうしの挿入判定（必要量 LC_SAFETY_MARGIN）が通る。
        """
        return requester_pos - VEH_LENGTH - MIN_GAP - 2 * LC_SAFETY_MARGIN

    @staticmethod
    def stop_speed(room: float, decel: float = HOLD_DECEL) -> float:
        """残り room [m] で減速度 decel（既定 HOLD_DECEL）で止まれる速度 √(2·decel·room)（room ≤ 0 なら 0）。"""
        return math.sqrt(2 * decel * room) if room > 0 else 0.0

    @staticmethod
    def next_lane(req: LCRequest) -> int:
        """次の1段LCの目標車線（current_lane を direction 方向に1つ）。"""
        return req.current_lane + (1 if req.direction == CarAction.CHANGE_LEFT else -1)
