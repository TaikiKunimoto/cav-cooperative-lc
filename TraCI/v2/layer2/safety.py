"""安全ギャップ G_req（式C）と、次の1段LCの目標車線計算。

G_req = 空走(v×δ) + 制動距離 + minGap。人間の反応時間(0.75s)は除き、空走は通信遅延 δ（理想 δ=0 でゼロ）。
挿入時の前後二方向の安全判定は、フィーダー流入車も見えるよう実測（getNeighbors）で行うため Layer2（pair_executor）に置く。
"""

from status.status import CarAction
from v2.constants import DELAY, FRICTION_COEFFICIENT, MIN_GAP
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
    def next_lane(req: LCRequest) -> int:
        """次の1段LCの目標車線（current_lane を direction 方向に1つ）。"""
        return req.current_lane + (1 if req.direction == CarAction.CHANGE_LEFT else -1)
