"""任意の車線変更（速度利得）: 提供車を付けず（協調しない）、必須の車線変更の邪魔をしない範囲で、速く流れている隣の車線へ移る。

本線 edge（調停の対象の区間）にいる車が、次のすべてを満たすときだけ隣の車線へ移る::

    1. 速度利得: SUMO の LC2013（1.27.0 の MSLCM_LC2013.cpp）と同じ形で、左右の車線ごとの積み上げが
       DLC_GAIN_THRESHOLD を超え、隣の車線の方が速い。車線ごとの速さは前方 DLC_LOOKAHEAD m 以内を走る車の平均速度
       （車がいなければその車線の制限速度）。差の割合 g = (v_隣 − v_自車線) / max(v_隣, DLC_GAIN_MIN_SPEED) を毎 step
       Δt·g ずつ積み上げ、自車線の方が速ければ 0.5^Δt、同じなら 0.8^Δt を掛けて減らす。積み上げは条件 2〜6 に
       関わらず本線 edge にいる間は毎 step 進め、必須・任意を問わず車線を移ったら 0 に戻す（LC2013 の changed()）
    2. 挿入判定（必須の車線変更と同じ式。``Layer2._insertion_check_live``）が通り、同じ step に同じ車線へ確定した
       車線変更とも同じ式の車間が取れる（``Layer2._slot_free``）
    3. 移った先でも、行き先までに要る車線変更の数が増えない（ルートを続けるのに要る数＝getBestLanes に、前方に
       障害物（停止車両）がいる車線ではその回避の 1 回を足して数える）
    4. 要求車と、まだ済んでいない必須の操作（必須の車線変更・回避・戻り）を持つ車（要求が始まる前でも）と、必須の
       車線変更・戻りを済ませた車と、譲っている提供車ではない。必須の操作を持つ車の車線変更は、要求として必須の側
       （``Layer2.execute_pairs``）が行う。必須の車線変更・戻りの完了は「今その目標車線にいるか」で決まる
       （``LCOperation.is_done``）ので、済ませた車が目標車線から出ると操作が未完了に戻り、次の step に必須の側で
       戻ってしまう（回避は障害物を通過したら完了で、車線に依らない）
    5. 移る先の車線を次の目標にしている要求車の前後 DLC_REQUESTER_CLEARANCE m に入らない
    6. 前の任意の車線変更から DLC_COOLDOWN 秒たっている

LC2013 から入れないもの: 走行車線側（右）へ移るときの別のしきい値と速さの割引、キープライト、移った先の車線が
続く時間の条件（条件 3 と区間内に限ることで代える）、急ぎの印。

必須の車線変更（``Layer2.execute_pairs``）のあとに呼び、同じ step の確定（``committed``）を共有する。車は前から順に
（本線の車線ごとに縦位置の降順で）見る。両側とも条件 1 を満たせば、積み上げの大きい方から条件 2〜6 を見る。
"""

from bisect import bisect_right
from dataclasses import dataclass
import os
import sys

from status.status import CarStatus
from utils.traci_wrapper import get_edge_lane_number, get_lane_max_speed, get_veh_best_lane_offsets
from v2.constants import (
    DISCRETIONARY_LC,
    DLC_COOLDOWN,
    DLC_GAIN_MIN_SPEED,
    DLC_GAIN_THRESHOLD,
    DLC_LOOKAHEAD,
    DLC_REQUESTER_CLEARANCE,
    TIME_STEP,
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

# LC2013 の積み上げの減り方（1 s あたり。自車線の方が速いとき・同じとき）
GAIN_DECAY_WORSE = 0.5
GAIN_DECAY_EQUAL = 0.8


@dataclass
class DiscretionaryCounts:
    """任意の車線変更の集計（run の終わりに出す）。止めた回数は、条件 1・3・6 を満たした車線で、条件 2 も満たしていた回数。

    条件 4 で止めた回数は、譲っている提供車（必須の操作を持たない車）の分だけを数える（条件 5 は見ない）。必須の操作を
    持つ車の車線変更は必須の側が扱うので、任意の車線変更を止めたことにはならない。条件 5 で止めた回数は候補の車線ごとに
    数えるので、両側の車線が候補なら 1 台・1 step に 2 回数えることがある。
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


@dataclass(frozen=True)
class LaneSpeeds:
    """本線 edge の車線ごとの、縦位置の昇順の位置と速度の累積和（前方 DLC_LOOKAHEAD m の平均速度を引くため）。"""

    edge: str
    positions: dict[int, list[float]]
    speed_prefix: dict[int, list[float]]

    @classmethod
    def from_snapshot(cls, snap: Snapshot, lane_count: int) -> "LaneSpeeds":
        positions: dict[int, list[float]] = {}
        prefix: dict[int, list[float]] = {}
        for lane in range(lane_count):
            obs = [snap.obs[vid] for vid in reversed(snap.lane_members.get(f"{snap.mainlane_edge}_{lane}", []))]
            pairs = [(o.lane_pos, o.speed) for o in obs if o.lane_pos is not None]  # 縦位置の昇順
            positions[lane] = [p for p, _ in pairs]
            acc = [0.0]
            for _, v in pairs:
                acc.append(acc[-1] + v)
            prefix[lane] = acc
        return cls(edge=snap.mainlane_edge, positions=positions, speed_prefix=prefix)

    def ahead(self, lane: int, pos: float) -> float:
        """車線 lane の、縦位置 pos より前で pos + DLC_LOOKAHEAD m 以内を走る車の平均速度（いなければ制限速度）。"""
        xs = self.positions[lane]
        lo, hi = bisect_right(xs, pos), bisect_right(xs, pos + DLC_LOOKAHEAD)
        if hi > lo:
            acc = self.speed_prefix[lane]
            return (acc[hi] - acc[lo]) / (hi - lo)
        return get_lane_max_speed(f"{self.edge}_{lane}")


class Discretionary:
    """任意の車線変更（状態を持たない静的ロジック。車ごとの積み上げと待ち時間は ``V2CAV`` が持つ）。"""

    @staticmethod
    def execute(
        by_id: dict[str, V2CAV],
        req_by_id: dict[str, LCRequest],
        snap: Snapshot,
        committed: dict[int, list[tuple[float, float]]],
        counts: DiscretionaryCounts,
    ) -> int:
        """本線 edge の車の速度利得の積み上げを進め、任意の車線変更を判定して実行する。実行した数を返す（``counts`` に集計を足す）。"""
        if DISCRETIONARY_LC <= 0:
            return 0
        targeted: dict[int, list[float]] = {}  # 車線 -> その車線を次の目標にしている要求車の縦位置
        for req in req_by_id.values():
            targeted.setdefault(Safety.next_lane(req), []).append(req.current_pos)
        obstacles: dict[int, list[float]] = {}  # 車線 -> 障害物（停止車両）の縦位置
        for o in snap.obs.values():
            if o.is_obstacle and o.road == snap.mainlane_edge and o.lane is not None and o.lane_pos is not None:
                obstacles.setdefault(o.lane, []).append(o.lane_pos)
        lane_count = get_edge_lane_number(snap.mainlane_edge)
        speeds = LaneSpeeds.from_snapshot(snap, lane_count)
        executed = 0
        for lane in range(lane_count):
            for vid in snap.lane_members.get(f"{snap.mainlane_edge}_{lane}", []):  # 縦位置の降順（前から）
                veh = by_id.get(vid)
                if veh is None or veh.is_obstacle or veh.lane != lane or veh.lane_pos is None:
                    continue
                candidates = Discretionary._update_speed_gain(veh, lane, lane_count, speeds)
                if not candidates or snap.sim_time < veh.discretionary_cooldown_until:  # 条件 1・6
                    continue
                if Discretionary._try(
                    veh, lane, candidates, lane_count, req_by_id, targeted, obstacles, snap, committed, counts
                ):
                    executed += 1
        return executed

    @staticmethod
    def _update_speed_gain(veh: V2CAV, lane: int, lane_count: int, speeds: LaneSpeeds) -> list[int]:
        """速度利得の積み上げを 1 step 進め、条件 1 を満たす車線を積み上げの大きい順に返す（LC2013 の左側の規則を左右に使う）。"""
        assert veh.lane_pos is not None
        if veh.speed_gain_lane != (speeds.edge, lane):
            veh.speed_gain_left = veh.speed_gain_right = 0.0  # 車線を移ったら 0 に戻す（LC2013 の changed()）
            veh.speed_gain_lane = (speeds.edge, lane)
        v_own = speeds.ahead(lane, veh.lane_pos)
        candidates: list[tuple[float, int]] = []
        for new_lane in (lane - 1, lane + 1):
            if not 0 <= new_lane < lane_count:
                continue
            v_nb = speeds.ahead(new_lane, veh.lane_pos)
            gain = (v_nb - v_own) / max(v_nb, DLC_GAIN_MIN_SPEED)
            p = veh.speed_gain_left if new_lane > lane else veh.speed_gain_right
            if v_own > v_nb:
                p *= GAIN_DECAY_WORSE**TIME_STEP
            elif v_own == v_nb:
                p *= GAIN_DECAY_EQUAL**TIME_STEP
            else:
                p += TIME_STEP * gain
            if new_lane > lane:
                veh.speed_gain_left = p
            else:
                veh.speed_gain_right = p
            if p > DLC_GAIN_THRESHOLD and gain > 0:
                candidates.append((p, new_lane))
        return [new_lane for _, new_lane in sorted(candidates, reverse=True)]

    @staticmethod
    def _try(
        veh: V2CAV,
        lane: int,
        candidates: list[int],
        lane_count: int,
        req_by_id: dict[str, LCRequest],
        targeted: dict[int, list[float]],
        obstacles: dict[int, list[float]],
        snap: Snapshot,
        committed: dict[int, list[tuple[float, float]]],
        counts: DiscretionaryCounts,
    ) -> bool:
        """条件 1 を満たす車線（積み上げの大きい順）について条件 2〜5 を見て、満たせば車線変更する。移ったら True。"""
        if veh.id in req_by_id or veh.active_operation() is not None:
            return False  # 条件 4: 要求車と、まだ済んでいない必須の操作を持つ車（要求が始まる前も）
        if any(not op.is_avoidance for op in veh.operations):
            return False  # 条件 4: 必須の車線変更・戻りを済ませた車（目標車線から出ると操作が未完了に戻る）
        assert veh.lane_pos is not None
        pos = veh.lane_pos
        offsets = Discretionary._route_lane_offsets(veh, snap.mainlane_edge)
        if lane not in offsets:
            return False

        def needed(target: int) -> int:  # 車線 target から行き先までに要る車線変更の数（前方の障害物の回避を含む）
            return offsets.get(target, lane_count) + int(any(p > pos for p in obstacles.get(target, [])))

        for new_lane in candidates:
            if needed(new_lane) > needed(lane):
                continue  # 条件 3: 行き先までに要る車線変更が増える
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
            committed.setdefault(new_lane, []).append((pos, veh.speed))
            veh.discretionary_cooldown_until = snap.sim_time + DLC_COOLDOWN
            veh.speed_gain_left = veh.speed_gain_right = 0.0  # LC2013 の changed()
            counts.lane_changes += 1
            return True
        return False

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
