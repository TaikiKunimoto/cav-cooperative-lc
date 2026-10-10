"""速度指令の調停: 縦方向制御（追従）と Layer2（譲り・整列・保持）が出す候補を 1 本にまとめ、毎 step 1 回だけ setSpeed する。

speedMode 0 の車は SUMO の安全速度・加減速上限を受けないので、ここが唯一の上限クリップになる。候補は
「目標速度と、そこへ向かうときに許す減速度／加速度」の組（``SpeedCandidate``）で、次 step の速度に直してから
最小（最も遅い）を採る。これにより、追従の安全側（前車速度へ最大減速）・協調の快適側（譲りは緩い減速）・加速の
上限が 1 か所で揃い、指令を出さない分岐が無いので前回の指令速度が残り続けることも無い。
"""

import csv
from dataclasses import dataclass
import math
import os
from typing import IO, TYPE_CHECKING, Any, ClassVar

from v2.constants import EMERGENCY_DECEL, MAX_ACCEL, MAX_DECEL, TIME_STEP

if TYPE_CHECKING:
    from v2.v2_cav import V2CAV


@dataclass(frozen=True)
class SpeedCandidate:
    """1 つの速度候補: 規則名・目標速度 [m/s]・許す減速度 [m/s²]（正）・許す加速度 [m/s²]。"""

    rule: str
    target: float
    decel: float
    accel: float = MAX_ACCEL

    def __post_init__(self) -> None:
        if not math.isfinite(self.target) or self.target < 0.0:
            raise ValueError(f"目標速度は非負の有限値で指定してください（rule={self.rule}, target={self.target}）")
        if not (math.isfinite(self.decel) and self.decel > 0.0):
            raise ValueError(f"減速度は正の有限値で指定してください（rule={self.rule}, decel={self.decel}）")
        if not (math.isfinite(self.accel) and self.accel >= 0.0):
            raise ValueError(f"加速度は非負の有限値で指定してください（rule={self.rule}, accel={self.accel}）")

    def next_speed(self, speed: float) -> float:
        """今の速度からこの候補に従ったときの次 step の速度（目標へ、許す加減速度の範囲で近づく）。"""
        lo = speed - self.decel * TIME_STEP
        hi = speed + self.accel * TIME_STEP
        return max(0.0, min(max(self.target, lo), hi))


class SpeedArbiter:
    """候補から次 step の速度を 1 つ決める（状態を持たない静的ロジック）。"""

    EMERGENCY = "emergency"  # この規則の候補だけ |MAX_DECEL| を超える減速（EMERGENCY_DECEL まで）を許す

    @staticmethod
    def choose(speed: float, candidates: list[SpeedCandidate]) -> tuple[float, SpeedCandidate]:
        """次 step の速度が最も遅い候補を採り、加減速の上限で最後に 1 回クリップして (次速度, 採用候補) を返す。

        同点は先に出された候補（追従 → Layer2 の順）を採る（決定的）。
        """
        if not candidates:
            raise ValueError("速度候補が空です")
        chosen = min(candidates, key=lambda c: c.next_speed(speed))
        next_speed = chosen.next_speed(speed)
        decel_cap = EMERGENCY_DECEL if chosen.rule == SpeedArbiter.EMERGENCY else abs(MAX_DECEL)
        next_speed = max(next_speed, speed - decel_cap * TIME_STEP)
        next_speed = min(next_speed, speed + MAX_ACCEL * TIME_STEP)
        return max(0.0, next_speed), chosen


class SpeedLog:
    """環境変数 ``V2_SPEED_LOG=<path>`` のときだけ、全車の毎 step の速度指令（規則・目標・次速度）を CSV に書く。"""

    ENV_VAR = "V2_SPEED_LOG"
    FIELDS: ClassVar[tuple[str, ...]] = (
        "time",
        "veh",
        "lane",
        "lane_pos",
        "speed",
        "next_speed",
        "rule",
        "target",
        "status",
    )

    def __init__(self, path: str) -> None:
        self._fh: IO[Any] = open(path, "w", newline="")
        self._writer = csv.writer(self._fh)
        self._writer.writerow(self.FIELDS)

    @classmethod
    def from_env(cls) -> "SpeedLog | None":
        path = os.environ.get(cls.ENV_VAR)
        return cls(path) if path else None

    def write(self, sim_time: float, veh: "V2CAV") -> None:
        self._writer.writerow(
            [
                f"{sim_time:.1f}",
                veh.id,
                veh.lane,
                None if veh.lane_pos is None else round(veh.lane_pos, 2),
                round(veh.speed, 3),
                None if veh.speed_next is None else round(veh.speed_next, 3),
                veh.speed_rule,
                None if veh.speed_target is None else round(veh.speed_target, 3),
                veh.status.name,
            ]
        )

    def close(self) -> None:
        self._fh.close()
