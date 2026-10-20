"""任意の車線変更（速度利得）: 提供車を付けず（協調しない）、必須の車線変更の邪魔をしない範囲で、速く流れている隣の車線へ移る。

本線 edge（調停の対象の区間）にいる車が、次のすべてを満たすときだけ隣の車線へ移る::

    1. 隣の車線の前方 DLC_LOOKAHEAD m 以内を走る車の平均速度が、自分の速度の (1 + DLC_SPEED_GAIN) 倍以上で、
       自分の速度より速い（前方に車がいなければ、その車線の制限速度で測る）
    2. 挿入判定（必須の車線変更と同じ式。``Layer2._insertion_check_live``）が通り、同じ step に同じ車線へ確定した
       車線変更とも同じ式の車間が取れる（``Layer2._slot_free``）
    3. 移った先でも、行き先までに要る車線変更の数が増えない（ルートを続けるのに要る数＝getBestLanes に、前方に
       障害物（停止車両）がいる車線ではその回避の 1 回を足す。まだ済んでいない操作（要求を出す前の必須の車線変更・
       回避）があれば、その目標車線までの数も下回らない）
    4. 要求車（必須の車線変更・回避の要求を出している車）と、譲っている提供車ではない
    5. 移る先の車線を次の目標にしている要求車の前後 DLC_REQUESTER_CLEARANCE m に入らない
    6. 前の任意の車線変更から DLC_COOLDOWN 秒たっている

必須の車線変更（``Layer2.execute_pairs``）のあとに呼び、同じ step の確定（``committed``）を共有する。車は前から順に
（本線の車線ごとに縦位置の降順で）見る。両側とも条件を満たせば、平均速度の速い方へ移る。
"""

from dataclasses import dataclass
import os
import sys

from status.status import CarStatus
from utils.traci_wrapper import get_edge_lane_number, get_lane_max_speed, get_veh_best_lane_offsets
from v2.constants import (
    DISCRETIONARY_LC,
    DLC_COOLDOWN,
    DLC_LOOKAHEAD,
    DLC_REQUESTER_CLEARANCE,
    DLC_SPEED_GAIN,
)
from v2.layer2.pair_executor import Layer2
from v2.layer2.safety import Safety
from v2.lc_request import LCRequest
from v2.snapshot import Snapshot
from v2.v2_cav import V2CAV

if "SUMO_HOME" in os.environ:
    tools = os.path.join(os.environ["SUMO_HOME"], "tools")
    sys.path.append(tools)
else:
    sys.exit("please declare environment variable 'SUMO_HOME'")
import traci


@dataclass
class DiscretionaryCounts:
    """任意の車線変更の集計（run の終わりに出す）。止めた回数は「ほかの条件はすべて満たしていた」車両・step の数。

    条件 4 で止めた回数は、譲っている提供車の分だけを数える。要求車が条件 3 を満たして移れる向きは必須の車線変更の
    向きだけで、それは ``Layer2.execute_pairs`` が扱う（任意の車線変更を止めたことにはならない）。
    """

    lane_changes: int = 0
    blocked_provider: int = 0  # 条件 4（譲っている提供車）で止めた
    blocked_clearance: int = 0  # 条件 5（移る先の車線を目標にしている要求車の近く）で止めた

    def summary_row(self) -> dict[str, int]:
        return {
            "discretionary_lane_changes": self.lane_changes,
            "blocked_provider": self.blocked_provider,
            "blocked_requester_clearance": self.blocked_clearance,
        }


class Discretionary:
    """任意の車線変更（状態を持たない静的ロジック。車ごとの待ち時間は ``V2CAV.discretionary_cooldown_until``）。"""

    @staticmethod
    def execute(
        by_id: dict[str, V2CAV],
        req_by_id: dict[str, LCRequest],
        snap: Snapshot,
        committed: dict[int, list[tuple[float, float]]],
        counts: DiscretionaryCounts,
    ) -> int:
        """本線 edge の車の任意の車線変更を判定して実行し、実行した数を返す（``counts`` に集計を足す）。"""
        if DISCRETIONARY_LC <= 0:
            return 0
        targeted: dict[int, list[float]] = {}  # 車線 -> その車線を次の目標にしている要求車の縦位置
        for req in req_by_id.values():
            targeted.setdefault(Safety.next_lane(req), []).append(req.current_pos)
        obstacles: dict[int, list[float]] = {}  # 車線 -> 障害物（停止車両）の縦位置
        for o in snap.obs.values():
            if o.is_obstacle and o.road == snap.mainlane_edge and o.lane is not None and o.lane_pos is not None:
                obstacles.setdefault(o.lane, []).append(o.lane_pos)
        lanes = sorted(int(lane_id.rsplit("_", 1)[1]) for lane_id in snap.lane_members)
        lane_count = get_edge_lane_number(snap.mainlane_edge)
        executed = 0
        for lane in lanes:
            for vid in snap.lane_members[f"{snap.mainlane_edge}_{lane}"]:  # 縦位置の降順（前から）
                veh = by_id.get(vid)
                if veh is None or veh.is_obstacle or veh.lane != lane or veh.lane_pos is None:
                    continue
                if snap.sim_time < veh.discretionary_cooldown_until:
                    continue  # 条件 6
                if Discretionary._try(veh, lane, lane_count, req_by_id, targeted, obstacles, snap, committed, counts):
                    executed += 1
        return executed

    @staticmethod
    def _try(
        veh: V2CAV,
        lane: int,
        lane_count: int,
        req_by_id: dict[str, LCRequest],
        targeted: dict[int, list[float]],
        obstacles: dict[int, list[float]],
        snap: Snapshot,
        committed: dict[int, list[tuple[float, float]]],
        counts: DiscretionaryCounts,
    ) -> bool:
        """1 台について条件 1〜5 を見て、満たせば車線変更する。移ったら True。"""
        assert veh.lane_pos is not None
        pos, v = veh.lane_pos, veh.speed
        gains = []
        for new_lane in (lane - 1, lane + 1):
            if 0 <= new_lane < lane_count:
                lane_speed = Discretionary._lane_speed_ahead(snap, new_lane, pos)
                if lane_speed > v and lane_speed >= v * (1 + DLC_SPEED_GAIN):  # 条件 1
                    gains.append((lane_speed, new_lane))
        if not gains:
            return False
        offsets = Discretionary._route_lane_offsets(veh, snap.mainlane_edge)
        if lane not in offsets:
            return False

        op = veh.active_operation()  # まだ済んでいない操作のうち締切が最も近いもの（回避を含む）

        def needed(target: int) -> int:  # 車線 target から行き先までに要る車線変更の数（前方の障害物の回避を含む）
            n = offsets.get(target, lane_count) + int(any(p > pos for p in obstacles.get(target, [])))
            return max(n, abs(op.target_lane - target)) if op is not None else n

        for _, new_lane in sorted(gains, reverse=True):  # 速い方から
            if needed(new_lane) > needed(lane):
                continue  # 条件 3: 行き先までに要る車線変更が増える
            if veh.id in req_by_id:  # 条件 4: 要求車（移れる向きは必須の車線変更の向きだけ）
                return False
            if veh.status is CarStatus.YIELDING:  # 条件 4: 譲っている提供車
                if Discretionary._insertion_ok(veh, new_lane, lane, committed):
                    counts.blocked_provider += 1
                return False
            if any(abs(p - pos) <= DLC_REQUESTER_CLEARANCE for p in targeted.get(new_lane, [])):  # 条件 5
                if Discretionary._insertion_ok(veh, new_lane, lane, committed):
                    counts.blocked_clearance += 1
                continue
            if not Discretionary._insertion_ok(veh, new_lane, lane, committed):  # 条件 2
                continue
            traci.vehicle.changeLane(veh.id, new_lane, 0)
            committed.setdefault(new_lane, []).append((pos, v))
            veh.discretionary_cooldown_until = snap.sim_time + DLC_COOLDOWN
            counts.lane_changes += 1
            return True
        return False

    @staticmethod
    def _lane_speed_ahead(snap: Snapshot, lane: int, pos: float) -> float:
        """車線 lane の、縦位置 pos から前方 DLC_LOOKAHEAD m 以内を走る車の平均速度（いなければその車線の制限速度）。"""
        lane_id = f"{snap.mainlane_edge}_{lane}"
        speeds = [
            o.speed
            for vid in snap.lane_members.get(lane_id, [])
            if (o := snap.obs[vid]).lane_pos is not None and pos < o.lane_pos <= pos + DLC_LOOKAHEAD
        ]
        return sum(speeds) / len(speeds) if speeds else get_lane_max_speed(lane_id)

    @staticmethod
    def _route_lane_offsets(veh: V2CAV, edge: str) -> dict[int, int]:
        """車線ごとの「ルートを続けるのに要る車線変更の数」。同じ edge にいるあいだは最初に取った値を使う。"""
        if veh.route_lane_offsets_edge != edge:
            veh.route_lane_offsets = get_veh_best_lane_offsets(veh.id)
            veh.route_lane_offsets_edge = edge
        return veh.route_lane_offsets

    @staticmethod
    def _insertion_ok(veh: V2CAV, new_lane: int, lane: int, committed: dict[int, list[tuple[float, float]]]) -> bool:
        """条件 2: 挿入判定（必須の車線変更と同じ式）と、同じ step に同じ車線へ確定した車線変更との車間。"""
        assert veh.lane_pos is not None
        if not Layer2._slot_free(committed, new_lane, veh.lane_pos, veh.speed):
            return False
        return Layer2._insertion_check_live(veh.id, veh.speed, new_lane < lane).ok
