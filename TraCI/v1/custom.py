"""
提案モデル, 車線変更開始位置を動的に決定 & 車線変更に優先度を付与
"""

import csv
from datetime import datetime
import optparse
import os
import random
import sys
from typing import Any

from v1.cav.custom_cav import CustomCAV
import matplotlib.pyplot as plt
from simulationStatistics.simulation_statistics import SimulationStatistics
from sumolib import checkBinary
import traci
from utils.traci_wrapper import get_sim_time

# 定数
SIMULATION_TIME: float = 600.0  # シミュレーション時間[s]
CONGESTION_SPEED: float = 11.1  # 渋滞判定速度 [m/s] (40 km/h)
MIN_CONGESTED_VEHICLES: int = 5  # 渋滞とみなす最低車両数
MAINLANE_LENGTH: float = 2500.0  # メインレーンの全長 [m]


# シミュレーション状態を保持するクラス
class CustomSimulationState:
    def __init__(self, simulation_time: float):
        self.simulation_time: float = simulation_time
        self.veh_id: int = 0
        self.departTime_r_pass: list[float] = []
        self.departTime_r_exit: list[float] = []
        self.vehicle_instance: list[CustomCAV] = []
        self.total_departed_vehicle: list[str] = []
        self.exit_vehicle: list[str] = []
        self.canceled_vehicle: list[str] = []
        self.collision_history: list[tuple[float, list[str]]] = []
        # タイムスペース図記録用データ
        self.lane_data: dict[str, list[tuple[float, float, float]]] = {
            "lane0": [],
            "lane1": [],
            "lane2": [],
        }
        # 各車線の待ち行列（車両IDリスト）
        self.lane0_queue: list[str] = []
        self.lane1_queue: list[str] = []
        self.lane2_queue: list[str] = []


def run(
    state: CustomSimulationState, inflow_pass: int, inflow_exit: int, stats: SimulationStatistics, seed: str
) -> None:
    _set_environment(state, inflow_pass, inflow_exit)

    # ローカル変数：タイムスペース図記録用
    last_recorded_second = -1
    tail_position_list: list[tuple[float, float]] = []
    max_tail_position = 0.0

    # 各種統計記録用リスト（経路別）
    r_pass_departed_vehicle: list[str] = []
    r_exit_departed_vehicle: list[str] = []
    r_pass_exit_vehicle: list[str] = []
    r_exit_exit_vehicle: list[str] = []
    r_exit_running_vehicle_dict: dict[str, float] = {}

    while _should_continue_simulation(state):
        traci.simulationStep()
        _check_collision(state)

        # 各stepごとの車線変更操作履歴（各車両内で更新）
        lane_change_history: dict[str, Any] = {}

        # シミュレーション状態取得
        arrived_list: list[str] = traci.simulation.getArrivedIDList()
        departed_list: list[str] = traci.simulation.getDepartedIDList()
        running_list: list[str] = traci.vehicle.getIDList()

        # tail positionの記録（タイムスペース図用）
        current_time: float = traci.simulation.getTime()
        current_sec = int(current_time)
        if current_sec != last_recorded_second:
            congestion_point = _get_congestion_point()
            tail_pos = MAINLANE_LENGTH - congestion_point
            tail_position_list.append((current_time, tail_pos))
            if tail_pos > max_tail_position:
                max_tail_position = tail_pos
            last_recorded_second = current_sec

        poplist = []

        # 各車両インスタンスの更新処理
        for index, veh in enumerate(state.vehicle_instance):
            # シミュレーション範囲を出た車両の処理
            if veh.params.id in arrived_list:
                poplist.append(index)
                state.exit_vehicle.append(veh.params.id)
                veh.get_arrival_time()
                if veh.params.route == "r_pass":
                    # TODO
                    if veh.params.departure_time is not None and veh.params.arrival_time is not None:
                        stats.calculate_travel_time("r_pass", veh.params.departure_time, veh.params.arrival_time)
                    stats.calculate_vehicle_average_speed("r_pass", veh.params.speed_history)
                    r_pass_exit_vehicle.append(veh.params.id)
                elif veh.params.route == "r_exit":
                    # TODO
                    if veh.params.departure_time is not None and veh.params.arrival_time is not None:
                        stats.calculate_travel_time("r_exit", veh.params.departure_time, veh.params.arrival_time)
                    stats.calculate_vehicle_average_speed("r_exit", veh.params.speed_history)
                    r_exit_exit_vehicle.append(veh.params.id)
                continue

            # 走行中でない車両（混雑により未発進）の処理
            if veh.params.id not in running_list:
                if veh.params.id not in state.canceled_vehicle:
                    state.canceled_vehicle.append(veh.params.id)
                continue

            # シミュレーション範囲に入った車両の場合、出発時刻の記録
            if veh.params.id in departed_list:
                veh.get_departure_time()
                state.total_departed_vehicle.append(veh.params.id)
                if veh.params.route == "r_pass":
                    r_pass_departed_vehicle.append(veh.params.id)
                elif veh.params.route == "r_exit":
                    r_exit_departed_vehicle.append(veh.params.id)
                if veh.params.id in state.canceled_vehicle:
                    state.canceled_vehicle.remove(veh.params.id)

            # 車両の状態更新（位置、速度、優先度、車線変更など）
            congestion_point = _get_congestion_point()
            veh.update_status(congestion_point)
            veh.decide_next_action_and_priority()
            veh.execute_lane_change(lane_change_history)
            veh.control_speed()

            _update_lane_queue(state, veh.params.id)

            # TODO
            if veh.params.leader_distance is not None and veh.params.leader_speed is not None:
                stats.calculate_TTC(veh.params.leader_distance, veh.params.leader_speed, veh.params.speed)

            # TODO
            if veh.params.lane_id is not None and veh.params.lane_pos is not None:
                _record_lane_data(state, veh.params.lane_id, veh.params.lane_pos, veh.params.speed)

        # 車両インスタンスの削除
        if poplist:
            for i in sorted(poplist, reverse=True):
                state.vehicle_instance.pop(i)

        _add_vehicle(state)

    # シミュレーション終了時、残車両の統計を更新
    for veh in state.vehicle_instance:
        if veh.params.id in running_list:
            if veh.params.route == "r_pass":
                stats.calculate_vehicle_average_speed("r_pass", veh.params.speed_history)
            elif veh.params.route == "r_exit":
                stats.calculate_vehicle_average_speed("r_exit", veh.params.speed_history)
                # TODO
                if veh.params.pos_x is not None:
                    r_exit_running_vehicle_dict[veh.params.id] = veh.params.pos_x

    # r_exitの走行中車両を位置順にソート
    r_exit_running_vehicle_list = sorted(
        r_exit_running_vehicle_dict.keys(), key=lambda vid: r_exit_running_vehicle_dict[vid], reverse=True
    )

    # 衝突に関わった車両以外のキャンセル車両を抽出
    collided_vehicles = set()
    for _, vehicles in state.collision_history:
        collided_vehicles.update(vehicles)
    canceled_veh_without_collied = [vid for vid in state.canceled_vehicle if vid not in collided_vehicles]

    _print_simulation_info(state, running_list)
    total_collisions, total_vehicles_involved = _print_collision_summary(state)

    # tail positionのCSV出力
    tail_csv = (
        f"simulationStatistics/statistics/custom/tail_positions_pass{inflow_pass}_exit{inflow_exit}_seed{seed}.csv"
    )
    with open(tail_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["time", "tail_position"])
        for time_step, tail_pos in tail_position_list:
            writer.writerow([time_step, tail_pos])

    # EVAL_NO_PLOT=1（評価スイープ）では time-space 図出力を抑止して高速化する。
    if not os.environ.get("EVAL_NO_PLOT"):
        _plot_time_space_diagram(state, inflow_pass, inflow_exit)

    results = {
        "total_generated_vehicle": state.veh_id,
        "total_departed_vehicle": state.total_departed_vehicle,
        "running_vehicle": running_list,
        "exit_vehicle": state.exit_vehicle,
        "r_pass_departed_vehicle": r_pass_departed_vehicle,
        "r_exit_departed_vehicle": r_exit_departed_vehicle,
        "r_pass_exit_vehicle": r_pass_exit_vehicle,
        "r_exit_exit_vehicle": r_exit_exit_vehicle,
        "r_exit_running_vehicle": r_exit_running_vehicle_list,
        "canceled_vehicle": canceled_veh_without_collied,
        "traffic_volume": len(state.total_departed_vehicle) * (3600 / state.simulation_time),
        "total_collisions": total_collisions,
        "total_vehicles_involved": total_vehicles_involved,
        "max_tail_position": max_tail_position,
    }
    stats.add_result(state.simulation_time, seed, inflow_pass, inflow_exit, results)
    traci.close()


def _start_sim(sumoBinary: str) -> None:
    # EVAL_SUMOCFG（評価スイープ）で設定ファイルを差し替え可能（既定はオリジナルの high-way）。
    cfg = os.environ.get("EVAL_SUMOCFG", "../config/v1/high-way.sumocfg")
    traci.start([sumoBinary, "-c", cfg])
    print("Simulation started")


def _set_environment(state: CustomSimulationState, inflow_pass: int, inflow_exit: int) -> None:
    k_pass = int((state.simulation_time / 3600) * inflow_pass)
    k_exit = int((state.simulation_time / 3600) * inflow_exit)

    state.departTime_r_pass = sorted(random.sample(range(int(state.simulation_time)), k_pass))
    state.departTime_r_exit = sorted(random.sample(range(int(state.simulation_time)), k_exit))
    # 1秒以上間隔を確保
    state.departTime_r_pass = [round(n, 1) + 0.1 for n in state.departTime_r_pass]
    state.departTime_r_exit = [round(n, 1) + 0.1 for n in state.departTime_r_exit]

    print("departTime_r_pass:", state.departTime_r_pass)
    print("departTime_r_exit:", state.departTime_r_exit)


def _print_simulation_info(state: CustomSimulationState, running_list: list[str]) -> None:
    print("=====================================")
    print("simulation end")
    print("vehicle_instance Length :", state.veh_id)
    print("running_list Length :", len(running_list))
    print("exit_vehicle Length :", len(state.exit_vehicle))
    print("total_departed_vehicle Length :", len(state.total_departed_vehicle))
    print(f"traffic volume: {len(state.total_departed_vehicle) * (3600 / state.simulation_time)} pcu/h")
    print("canceled_vehicle Length :", len(state.canceled_vehicle))
    print("lane0_queue Length :", len(state.lane0_queue))
    print("lane1_queue Length :", len(state.lane1_queue))
    print("lane2_queue Length :", len(state.lane2_queue))
    print("=====================================")


def _print_collision_summary(state: CustomSimulationState) -> tuple[int, int]:
    total_collisions = len(state.collision_history)
    total_vehicles_involved = sum(len(vehicles) for _, vehicles in state.collision_history)
    print("\n=== Collision Summary ===")
    print(f"Total collision events: {total_collisions}")
    print(f"Total vehicles involved: {total_vehicles_involved}")
    for time_val, vehicles in state.collision_history:
        print(f"Time {time_val:.1f}: Collision between vehicles: {', '.join(vehicles)}")
    return total_collisions, total_vehicles_involved


def _check_collision(state: CustomSimulationState) -> None:
    colliding_ids: list[str] = traci.simulation.getCollidingVehiclesIDList()
    if colliding_ids:
        collision_time = traci.simulation.getTime() - 0.1
        # 重複記録防止のチェック
        for time_val, vehicles in state.collision_history:
            if abs(time_val - collision_time) < 1.0 and set(vehicles) == set(colliding_ids):
                return
        state.collision_history.append((collision_time, colliding_ids))
        print(f"Collision detected at {collision_time:.1f} between: {', '.join(colliding_ids)}")


def _update_lane_queue(state: CustomSimulationState, veh_id: str) -> None:
    if veh_id in state.lane0_queue:
        state.lane0_queue.remove(veh_id)
    elif veh_id in state.lane1_queue:
        state.lane1_queue.remove(veh_id)
    elif veh_id in state.lane2_queue:
        state.lane2_queue.remove(veh_id)


def _get_depart_lane(state: CustomSimulationState, edge_id: str) -> str:
    lanes = traci.edge.getLaneNumber(edge_id)
    available_lanes = [str(i) for i in range(lanes)]
    queue_length = {
        "0": len(state.lane0_queue),
        "1": len(state.lane1_queue),
        "2": len(state.lane2_queue),
    }
    lanes_without_queue = [lane for lane in available_lanes if queue_length[lane] == 0]
    if lanes_without_queue:
        depart_lane = random.choice(lanes_without_queue)
    else:
        min_length = min(queue_length.values())
        min_lanes = [lane for lane, length in queue_length.items() if length == min_length]
        depart_lane = random.choice(min_lanes)
    return depart_lane


def _get_congestion_point() -> float:
    lane2_vehicles: list[str] = traci.lane.getLastStepVehicleIDs("MainLane1_2")
    if len(lane2_vehicles) < MIN_CONGESTED_VEHICLES:
        return MAINLANE_LENGTH

    sorted_vehicles = sorted(
        lane2_vehicles,
        key=lambda vid: traci.vehicle.getLanePosition(vid),
        reverse=True,
    )
    congested_sequence = []
    tail_position = MAINLANE_LENGTH
    for vid in sorted_vehicles:
        speed = traci.vehicle.getSpeed(vid)
        if speed <= CONGESTION_SPEED:
            congested_sequence.append(vid)
            if len(congested_sequence) >= MIN_CONGESTED_VEHICLES:
                tail_position = traci.vehicle.getLanePosition(congested_sequence[-1])
        else:
            congested_sequence = []
    return tail_position


def _should_continue_simulation(state: CustomSimulationState) -> bool:
    sumo_time = get_sim_time()
    if sumo_time % 10 == 0:
        now = datetime.now().time()
        print("====================================================")
        print("TIME:", sumo_time)
        print("Now:", now)
        print("====================================================")
    return sumo_time < state.simulation_time


def _add_vehicle(state: CustomSimulationState) -> None:
    sumo_time = traci.simulation.getTime()
    # 車両追加（r_pass）
    if sumo_time in state.departTime_r_pass:
        depart_lane = _get_depart_lane(state, "MainLane1")
        traci.vehicle.add(
            vehID=str(state.veh_id),
            routeID="r_pass",
            typeID="CAV",
            departLane=depart_lane,
            departPos="base",
            departSpeed="last",
        )
        instance = CustomCAV(state.veh_id)
        state.vehicle_instance.append(instance)
        if depart_lane == "0":
            state.lane0_queue.append(str(state.veh_id))
        elif depart_lane == "1":
            state.lane1_queue.append(str(state.veh_id))
        elif depart_lane == "2":
            state.lane2_queue.append(str(state.veh_id))
        state.veh_id += 1

    # 車両追加（r_exit）
    if sumo_time in state.departTime_r_exit:
        depart_lane = _get_depart_lane(state, "MainLane1")
        traci.vehicle.add(
            vehID=str(state.veh_id),
            routeID="r_exit",
            typeID="CAV",
            departLane=depart_lane,
            departPos="base",
            departSpeed="last",
        )
        instance = CustomCAV(state.veh_id)
        state.vehicle_instance.append(instance)
        if depart_lane == "0":
            state.lane0_queue.append(str(state.veh_id))
        elif depart_lane == "1":
            state.lane1_queue.append(str(state.veh_id))
        elif depart_lane == "2":
            state.lane2_queue.append(str(state.veh_id))
        state.veh_id += 1


def _record_lane_data(state: CustomSimulationState, lane_id: str, pos: float, speed: float) -> None:
    # seedが"1"の場合のみ記録（デバッグ用）
    if seed != "1":
        return
    current_time = traci.simulation.getTime()
    if "MainLane1_0" in lane_id:
        state.lane_data["lane0"].append((current_time, pos, speed))
    elif "MainLane1_1" in lane_id:
        state.lane_data["lane1"].append((current_time, pos, speed))
    elif "MainLane1_2" in lane_id:
        state.lane_data["lane2"].append((current_time, pos, speed))


def _plot_time_space_diagram(
    state: CustomSimulationState,
    inflow_pass: int,
    inflow_exit: int,
    output_dir: str = "simulationStatistics/statistics/custom",
) -> None:
    # seedが"1"の場合のみプロット（デバッグ用）
    if seed != "1":
        return
    os.makedirs(output_dir, exist_ok=True)
    for lane, data in state.lane_data.items():
        if not data:
            continue
        times, positions, speeds = zip(*data, strict=False)
        plt.figure(figsize=(12, 6))
        sc = plt.scatter(times, positions, c=speeds, cmap="jet_r", s=1)
        plt.colorbar(sc, label="Speed (m/s)")
        plt.xlabel("Time (s)")
        plt.ylabel("Position (m)")
        plt.title(f"Time-Space Diagram for {lane}")
        output_path = os.path.join(output_dir, f"custom_{inflow_pass}_{inflow_exit}_{lane}_time_space_diagram.png")
        plt.savefig(output_path)
        plt.close()


def _get_options() -> optparse.Values:
    optParser = optparse.OptionParser()
    optParser.add_option(
        "--nogui",
        action="store_true",
        default=False,
        help="run the commandline version of sumo",
    )
    options, _ = optParser.parse_args()
    return options


def _create_file_name(inflow_pass: int, inflow_exit: int, seed: str) -> str:
    return f"custom_pass{inflow_pass}_exit{inflow_exit}_seed{seed}"


if __name__ == "__main__":
    options = _get_options()
    args = sys.argv
    seed = args[1]  # 乱数シード
    random.seed(seed)
    inflow_pass = int(args[2])  # pass側車両流入数
    inflow_exit = int(args[3])  # exit側車両流入数

    filename = _create_file_name(inflow_pass, inflow_exit, seed)
    stats = SimulationStatistics(filename=filename, output_dir="simulationStatistics/statistics/custom")

    if options.nogui:
        sumoBinary = checkBinary("sumo")
    else:
        sumoBinary = checkBinary("sumo-gui")

    _start_sim(sumoBinary)
    state = CustomSimulationState(SIMULATION_TIME)
    run(state, inflow_pass, inflow_exit, stats, seed)
