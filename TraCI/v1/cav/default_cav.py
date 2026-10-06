import os
import sys
from typing import Optional

from utils.traci_wrapper import (
    get_lane_max_speed,
    get_sim_time,
    get_veh_acceleration,
    get_veh_departure,
    get_veh_lane_id,
    get_veh_lane_index,
    get_veh_leader,
    get_veh_pos,
    get_veh_route_id,
    get_veh_speed,
    get_veh_type,
)

if "SUMO_HOME" in os.environ:
    tools = os.path.join(os.environ["SUMO_HOME"], "tools")
    sys.path.append(tools)
else:
    sys.exit("please declare environment variable 'SUMO_HOME'")
from pydantic import BaseModel

import traci

MAX_SPEED = 27  # [m/s]
MAX_ACCEL = 3.0  # [m/ss]
MAX_DECEL = -5.0  # [m/ss]
LANE_WIDTH = 3.2  # [m]

TIME_STEP = 0.1  # [s]

MERGE_STAET_POS = 2100  # [m] 単純な手法の場合の車線変更開始地点(2500 - x)


class DefaultCAVParams(BaseModel):
    id: str
    typeID: Optional[str] = None
    route: Optional[str] = None
    road: Optional[str] = None
    lane: Optional[int] = None
    laneID: Optional[str] = None
    status: Optional[str] = None
    distance: Optional[float] = None
    leader_speed: Optional[float] = None
    pos_x: float = 0.0
    pos_y: float = 0.0
    speed: float = 0.0
    accel: float = 0.0
    leader: Optional[tuple[str, float]] = None
    leadPath: Optional[str] = None
    length: float = 5.0
    width: float = 1.8
    simTime: float = 0.0
    departure_time: float = 0.0
    arrival_time: float = 0.0
    speed_history: list[float] = []


class DefaultCAV:
    # constructor
    def __init__(self, vehID: int):
        self.params = DefaultCAVParams(
            id=str(vehID),
            typeID=get_veh_type(str(vehID)),
            route=get_veh_route_id(str(vehID)),
            laneID=get_veh_lane_id(str(vehID)),
        )

        # 初期設定: 車線変更モードと速度制御モードの設定
        traci.vehicle.setLaneChangeMode(vehID=self.params.id, lcm=0b000000000000)
        # traci.vehicle.setLaneChangeMode(vehID=self.params.id, lcm=0b000000010100)
        # traci.vehicle.setLaneChangeMode(vehID=self.params.id, lcm=0b000000010000)

        # 制御車両の速度は traci で管理する
        traci.vehicle.setSpeedMode(vehID=self.params.id, sm=0b000000)

    """ 車輌の実際の出発時刻を取得 """

    def get_departure_time(self) -> None:
        self.params.departure_time = get_veh_departure(self.params.id)

    """ 車輌の実際の到着時刻を取得 """

    def get_arrival_time(self) -> None:
        self.params.arrival_time = get_sim_time()

    """ 自身のステータスを更新 """

    def updateStatus(self) -> None:
        self.params.simTime = get_sim_time()
        self.params.speed_history.append(get_veh_speed(self.params.id))

        # 車線変更が可能なポイントを通過したら車線変更を可能にする
        if self.hasPassedLaneChangePoint():
            traci.vehicle.setLaneChangeMode(vehID=self.params.id, laneChangeMode=0b011000010101)

        # update own position
        pos: tuple[float, float] = get_veh_pos(self.params.id)
        self.params.pos_x = pos[0]
        self.params.pos_y = pos[1]

        self.params.speed = min(get_veh_speed(self.params.id), MAX_SPEED)
        self.params.accel = min(get_veh_acceleration(self.params.id), MAX_ACCEL)
        self.params.road = get_veh_route_id(self.params.id)
        self.params.lane = get_veh_lane_index(self.params.id)
        self.params.laneID = get_veh_lane_id(self.params.id)
        self.params.leader = get_veh_leader(self.params.id, 0)  # 0 にすると制動距離より短い距離の先行車を取得
        self.params.distance = self.params.leader[1] if self.params.leader is not None else None
        self.params.leader_speed = (
            traci.vehicle.getSpeed(self.params.leader[0]) if self.params.leader is not None else None
        )

    """ 車線変更が可能なポイントを通過したかどうか """

    def hasPassedLaneChangePoint(self) -> bool:
        if traci.vehicle.getLanePosition(self.params.id) > MERGE_STAET_POS:
            return True
        return False

    """ 車両の速度を制限速度に基づいて調整 """

    def controlSpeed(self) -> None:
        # 無効な道路上の場合は制御しない
        if self.params.road is None or self.params.road.startswith(":"):
            return

        # 現在のレーンと制限速度を取得
        if self.params.laneID is None:
            return
        speed_limit = get_lane_max_speed(self.params.laneID)

        # 制限速度を超えている場合は減速
        if self.params.speed > speed_limit:
            safe_duration = self._calculateSafeDuration(self.params.speed - speed_limit)
            traci.vehicle.slowDown(self.params.id, speed_limit, safe_duration)
        return

    """ 最大減速で速度差を0にするために必要な時間を計算 """

    def _calculateSafeDuration(self, speed_diff: float) -> float:
        if speed_diff <= 0:
            return 0
        return speed_diff / abs(MAX_DECEL)
