"""TraCI 関数の薄いラッパ。

戻り値を ``cast`` で型付けし、mypy strict 下でも扱いやすくする。v1/v2 共有。
位置・距離は [m]、速度は [m/s]、時刻は [s]。
"""

import math
from typing import cast

import traci

# slowDown の最小継続時間 [s]（1 step 相当）。異常な duration のフォールバック。
_MIN_SLOWDOWN_DURATION = 0.1
# slowDown 継続時間の上限。有限でも桁外れ（例: 微速時の v/decel ≈ 10^6 s）だと SUMO 側の時間表現を
# 溢れさせ 'Invalid time interval' で接続ごと落ちる。シミュレーション実時間より十分長い1時間に丸める。
_MAX_SLOWDOWN_DURATION = 3600.0


# Vehicle系の関数
def get_veh_id_list() -> list[str]:
    """シミュレーション中（ネットワーク上）の全車両IDのリストを返す。"""
    return cast(list[str], traci.vehicle.getIDList())


def get_veh_departure(id: str) -> float:
    """車両の出発（投入）時刻 [s] を返す。"""
    return cast(float, traci.vehicle.getDeparture(id))


def get_veh_type(id: str) -> str:
    """車両のタイプID（例 "CAV"）を返す。"""
    return cast(str, traci.vehicle.getTypeID(id))


def get_veh_pos(id: str) -> tuple[float, float]:
    """車両の (x, y) 座標 [m] を返す。"""
    return cast(tuple[float, float], traci.vehicle.getPosition(id))


def get_veh_lane_position(id: str) -> float:
    """車両のレーン内縦位置 [m]（現在 edge の始端からの距離）を返す。"""
    return cast(float, traci.vehicle.getLanePosition(id))


def get_veh_route_id(id: str) -> str:
    """車両のルートID（例 "r_pass" / "r_exit"）を返す。"""
    return cast(str, traci.vehicle.getRouteID(id))


def get_veh_lane_id(id: str) -> str:
    """車両の現在レーンID（例 "MainLane1_0"）を返す。"""
    return cast(str, traci.vehicle.getLaneID(id))


def get_veh_lane_index(id: str) -> int:
    """車両の現在レーンインデックス（0 始まり。0=最下段）を返す。"""
    return cast(int, traci.vehicle.getLaneIndex(id))


def get_veh_speed(id: str) -> float:
    """車両の現在速度 [m/s] を返す。"""
    return cast(float, traci.vehicle.getSpeed(id))


def get_veh_acceleration(id: str) -> float:
    """車両の現在加速度 [m/s^2] を返す。"""
    return cast(float, traci.vehicle.getAcceleration(id))


def get_veh_leader(id: str, setting: int) -> tuple[str, float] | None:
    """前方車両の (ID, 車間距離 [m]) を返す。``setting`` は探索の look-ahead 距離 [m]。前方車がいなければ None。"""
    return cast(tuple[str, float] | None, traci.vehicle.getLeader(id, setting))


def get_veh_neighbors(id: str, mode: int) -> list[tuple[str, float]]:
    """指定方向の隣接車線の前走/後続車を (ID, ギャップ距離 [m]) のリストで返す（ジャンクション・edge境界を跨ぐ）。

    ``mode`` はビットセット: bit1 横方向(左=0 / 右=1)、bit2 縦方向(後続=0 / 先行=1)、bit3 blocking。
    dist は minGap 込みの実ギャップ（重なりは負）。フィーダーedge・内部ジャンクション車線から流入する車も
    遡って返るため、自前スナップショット(mainlane_edge限定)のブラインドスポットを補える。
    """
    return cast(list[tuple[str, float]], list(traci.vehicle.getNeighbors(id, mode)))


def slow_down(id: str, speed: float, duration: float) -> None:
    """``traci.vehicle.slowDown`` の安全ラッパ。速度を非負・有限、継続時間を正・有限・上限内にクランプする。

    衝突/テレポート直後の異常状態で duration が ≤0・NaN・inf になると、SUMO が
    command 0xc4 'Invalid time interval' を返し TraCI 接続が落ちて run ごと中断する。
    有限でも桁外れに大きい duration（微速時の v/decel など）も同エラーになるため上限で丸める。
    正常な正の duration はそのまま通すため、通常挙動は不変（異常値のみクランプ）。
    """
    safe_speed = speed if (math.isfinite(speed) and speed >= 0.0) else 0.0
    safe_duration = duration if (math.isfinite(duration) and duration > 0.0) else _MIN_SLOWDOWN_DURATION
    safe_duration = min(safe_duration, _MAX_SLOWDOWN_DURATION)
    traci.vehicle.slowDown(id, safe_speed, safe_duration)


def get_veh_road_id(id: str) -> str:
    """車両の現在 edge ID（例 "MainLane1"）を返す。"""
    return cast(str, traci.vehicle.getRoadID(id))


# Simulation系の関数
def get_sim_departed_veh_id_list() -> list[str]:
    """直近 step で投入（出発）された車両IDのリストを返す。"""
    return cast(list[str], traci.simulation.getDepartedIDList())


def get_sim_arrived_veh_id_list() -> list[str]:
    """直近 step で範囲外に出た（到着した）車両IDのリストを返す。"""
    return cast(list[str], traci.simulation.getArrivedIDList())


def get_sim_time() -> float:
    """現在のシミュレーション時刻 [s] を返す。"""
    return cast(float, traci.simulation.getTime())


def get_colliding_veh_id_list() -> list[str]:
    """直近 step で衝突した車両IDのリストを返す。"""
    return cast(list[str], traci.simulation.getCollidingVehiclesIDList())


# Lane系の関数
def get_lane_max_speed(lane_id: str) -> float:
    """レーンの制限速度 [m/s] を返す。"""
    return cast(float, traci.lane.getMaxSpeed(lane_id))


def get_lane_last_step_veh_ids(lane_id: str) -> list[str]:
    """直近 step でそのレーン上にいた車両IDのリストを返す。"""
    return cast(list[str], traci.lane.getLastStepVehicleIDs(lane_id))


# Edge系の関数
def get_edge_lane_number(edge_id: str) -> int:
    """edge のレーン数を返す。"""
    return cast(int, traci.edge.getLaneNumber(edge_id))


def get_lane_length(lane_id: str) -> float:
    """lane の実長 [m] を返す（net 上の実エッジ長。公称値とはジャンクション形状分ずれる）。"""
    return cast(float, traci.lane.getLength(lane_id))
