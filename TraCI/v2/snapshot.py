"""毎Tc の全車スナップショット S_t。

2フェーズ調停（Phase A 鍵計算 / Phase B 割当）は、step 途中の traci 再読み取りをせず本スナップショットのみを
参照することで「同一スナップショットで全要求車の鍵を計算」（コア機構_決定事項.md §5）を保証する。
``lane_members`` は目標車線の後続車を高速に引くための縦位置降順インデックス。各車は環境が与える
``target_lane``/``deadline_pos``（必須LC仕様）を持ち、route 名には依存しない（環境非依存）。
``mainlane_edge`` を持たせることで、下流（rsu/safety/lc_request）は環境を意識せず本線 edge を引ける。
"""

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from v2.v2_cav import V2CAV


class VehObs(BaseModel):
    """1車両の観測値（S_t での凍結値）。"""

    model_config = ConfigDict(frozen=True)

    veh_id: str
    target_lane: int | None  # 必須LC の目標レーン（None=必須LCなし）
    deadline_pos: float | None  # 締切位置
    road: str | None
    lane: int | None
    lane_pos: float | None
    speed: float
    activation_time: float | None
    is_obstacle: bool  # 障害物（停止車両）。安全判定では回避対象だが、提供車には選ばない
    yield_cooldown_until: float = 0.0  # この時刻までは提供車に選ばない（譲りの時間上限を超えて交代した直後）


class Snapshot(BaseModel):
    """ある時刻 S_t の全車観測の凍結＋本線の車線別 縦位置降順インデックス。"""

    model_config = ConfigDict(frozen=True)

    sim_time: float
    mainlane_edge: str  # 本線 edge（lane_members のキー・活性化窓判定の基準）
    obs: dict[str, VehObs]
    lane_members: dict[str, list[str]]  # lane_id -> 縦位置降順の車両idリスト

    @staticmethod
    def _lane_pos_or_zero(o: VehObs) -> float:
        """縦位置を取り出す（未観測 None は 0.0 とみなしソートキーに使う）。"""
        return o.lane_pos if o.lane_pos is not None else 0.0

    @classmethod
    def capture(cls, vehicles: "list[V2CAV]", sim_time: float, mainlane_edge: str) -> "Snapshot":
        """走行中の全車両の観測値を凍結し、本線 edge の車線別 縦位置降順インデックスを作る。"""
        obs: dict[str, VehObs] = {}
        lane_members: dict[str, list[str]] = {}
        for veh in vehicles:
            op = veh.active_operation()  # 未達成で最も deadline が近い必須LC操作（なければ None＝through）
            obs[veh.id] = VehObs(
                veh_id=veh.id,
                target_lane=op.target_lane if op is not None else None,
                deadline_pos=op.deadline_pos if op is not None else None,
                road=veh.road,
                lane=veh.lane,
                lane_pos=veh.lane_pos,
                speed=veh.speed,
                activation_time=op.activation_time if op is not None else None,
                is_obstacle=veh.is_obstacle,
                yield_cooldown_until=veh.yield_cooldown_until,
            )
            if veh.road == mainlane_edge and veh.lane is not None:
                lane_members.setdefault(f"{veh.road}_{veh.lane}", []).append(veh.id)
        for ids in lane_members.values():
            ids.sort(key=lambda i: cls._lane_pos_or_zero(obs[i]), reverse=True)
        return cls(sim_time=sim_time, mainlane_edge=mainlane_edge, obs=obs, lane_members=lane_members)
