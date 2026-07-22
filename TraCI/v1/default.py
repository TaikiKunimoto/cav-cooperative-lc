"""
SUMOのデフォルトを使用したモデル
"""

from datetime import datetime
import optparse
import os
import random
import sys

from v1.cav.default_cav import DefaultCAV
from simulationStatistics.simulation_statistics import SimulationStatistics
from sumolib import checkBinary
from utils.traci_wrapper import (
    get_sim_arrived_veh_id_list,
    get_sim_departed_veh_id_list,
    get_veh_id_list,
)

import traci

# シミュレーションの総時間
SIM_TIME: float = 600.0  # 5min


# シミュレーションに関する状態を保存するクラス
class DefaultSimulationState:
    def __init__(self, simulation_time: float):
        self.simulation_time: float = simulation_time  # シミュレーションの総時間
        self.veh_id: int = 0
        self.departTime_r_pass: list[float] = []
        self.departTime_r_exit: list[float] = []
        self.vehicle_instance: list[DefaultCAV] = []
        self.total_departed_vehicle: list[str] = []
        self.exit_vehicle: list[str] = []
        self.r_pass_departed_vehicle: list[str] = []
        self.r_exit_departed_vehicle: list[str] = []
        self.r_pass_exit_vehicle: list[str] = []
        self.r_exit_exit_vehicle: list[str] = []
        self.canceled_vehicle: list[str] = []
        self.lane0_queue: list[str] = []
        self.lane1_queue: list[str] = []
        self.lane2_queue: list[str] = []


def run(
    state: DefaultSimulationState, inflow_pass: int, inflow_exit: int, stats: SimulationStatistics, seed: str
) -> None:
    _set_environment(state, inflow_pass, inflow_exit)

    while _shouldContinueSimWithSimulationTime(state):
        traci.simulationStep()

        # このstepでシミュレーション範囲を出た車輌のリスト
        arrived_list: list[str] = get_sim_arrived_veh_id_list()
        # このstepでシミュレーション範囲に入った車輌のリスト
        departed_list: list[str] = get_sim_departed_veh_id_list()
        # このstepで走行中の車輌のリスト
        running_list: list[str] = get_veh_id_list()

        poplist = []

        for index, ins in enumerate(state.vehicle_instance):
            # シミュレーション範囲を出た車両をリスト化
            if ins.params.id in arrived_list:
                poplist.append(index)
                state.exit_vehicle.append(ins.params.id)

                ins.get_arrival_time()
                # 車輌の travel time と average speed を計算
                if ins.params.route == "r_pass":
                    stats.calculate_travel_time("r_pass", ins.params.departure_time, ins.params.arrival_time)
                    stats.calculate_vehicle_average_speed("r_pass", ins.params.speed_history)
                    state.r_pass_exit_vehicle.append(ins.params.id)
                elif ins.params.route == "r_exit":
                    stats.calculate_travel_time("r_exit", ins.params.departure_time, ins.params.arrival_time)
                    stats.calculate_vehicle_average_speed("r_exit", ins.params.speed_history)
                    state.r_exit_exit_vehicle.append(ins.params.id)
                continue

            # 混雑でまだ道路に入れていない車両はcontinue
            if ins.params.id not in running_list:
                # キャンセルリストに入っていない場合は追加
                if ins.params.id not in state.canceled_vehicle:
                    state.canceled_vehicle.append(ins.params.id)
                continue

            # シミュレーション範囲に入った車両をリスト化し、キャンセルリストから削除
            if ins.params.id in departed_list:
                ins.get_departure_time()
                state.total_departed_vehicle.append(ins.params.id)

                if ins.params.route == "r_pass":
                    state.r_pass_departed_vehicle.append(ins.params.id)
                elif ins.params.route == "r_exit":
                    state.r_exit_departed_vehicle.append(ins.params.id)

                if ins.params.id in state.canceled_vehicle:
                    state.canceled_vehicle.remove(ins.params.id)

            # 自車両の情報（位置や速度）を更新
            ins.updateStatus()

            # Laneごとのキューから車両を削除
            _updateLaneQueue(state, ins.params.id)

            # 車両の速度を更新
            ins.controlSpeed()

            # TTCを計算
            if ins.params.distance is not None and ins.params.leader_speed is not None:
                stats.calculate_TTC(ins.params.distance, ins.params.leader_speed, ins.params.speed)

        # 車両インスタンスを削除
        if poplist:
            for i in sorted(poplist, reverse=True):
                state.vehicle_instance.pop(i)

        # 車両の追加
        _add_vehicle(state)

    _printSImulationInfoAtEnd(state, running_list)

    # シミュレーション結果をcsvファイルに保存
    results = {
        "total_generated_vehicle": state.veh_id,
        "total_departed_vehicle": state.total_departed_vehicle,
        "running_vehicle": running_list,
        "exit_vehicle": state.exit_vehicle,
        "r_pass_departed_vehicle": state.r_pass_departed_vehicle,
        "r_exit_departed_vehicle": state.r_exit_departed_vehicle,
        "r_pass_exit_vehicle": state.r_pass_exit_vehicle,
        "r_exit_exit_vehicle": state.r_exit_exit_vehicle,
        "canceled_vehicle": state.canceled_vehicle,
        # "lane0_queue": lane0_queue,
        # "lane1_queue": lane1_queue,
        # "lane2_queue": lane2_queue,
        "traffic_volume": len(state.total_departed_vehicle) * (3600 / state.simulation_time),
    }
    stats.add_result(state.simulation_time, seed, inflow_pass, inflow_exit, results)

    traci.close()


# シミュレーションを開始する
def _startSim(sumoBinary: str) -> None:
    # EVAL_SUMOCFG（評価スイープ）で設定ファイルを差し替え可能（既定はオリジナルの high-way）。
    cfg = os.environ.get("EVAL_SUMOCFG", "../config/v1/high-way.sumocfg")
    traci.start([sumoBinary, "-c", cfg])
    print("Simulation started")


# 初期設定（車両の流入時間の設定）
def _set_environment(state: DefaultSimulationState, inflow_pass: int, inflow_exit: int) -> None:
    k_0 = int((state.simulation_time / 3600) * inflow_pass)
    k_1 = int((state.simulation_time / 3600) * inflow_exit)

    # 車両の流入時刻を決定
    decided_departTime_r_pass: list[float] = sorted(random.sample(range(int(state.simulation_time)), k_0))
    decided_departTime_r_exit: list[float] = sorted(random.sample(range(int(state.simulation_time)), k_1))

    # 1秒以上開けて流入
    state.departTime_r_pass = [round(n, 1) + 0.1 for n in decided_departTime_r_pass]
    state.departTime_r_exit = [round(n, 1) + 0.1 for n in decided_departTime_r_exit]

    print("deparTime_r_pass", state.departTime_r_pass)
    print("departTime_r_exit", state.departTime_r_exit)


def _printSImulationInfoAtEnd(state: DefaultSimulationState, running_list: list[str]) -> None:
    print("=====================================")
    print("simulation end")

    # 生成された車輌インスタンスの数
    print("vehicle_instance Length :", state.veh_id)
    # 最後までシミュレーション内部に残っている車輌の数
    print("running_list Length :", len(running_list))
    # シミュレーション中に正常に終了した車両の数
    print("exit_vehicle Length :", len(state.exit_vehicle))
    # シミュレーションに入った車輌の数
    print("total_departed_vehicle Length :", len(state.total_departed_vehicle))
    # １時間あたりの交通量
    print(f"traffic volume: {len(state.total_departed_vehicle) * (3600 / state.simulation_time)} pcu/h")
    # シミュレーション中に混雑で道路に入れなかった車両の数
    print("canceled_vehicle Length :", len(state.canceled_vehicle))
    # シミュレーション終了時の各レーンのキューの長さ
    print("lane0_queue Length :", len(state.lane0_queue))
    print("lane1_queue Length :", len(state.lane1_queue))
    print("lane2_queue Length :", len(state.lane2_queue))

    print("=====================================")


def _updateLaneQueue(state: DefaultSimulationState, id: str) -> None:
    if id in state.lane0_queue:
        state.lane0_queue.remove(id)
    elif id in state.lane1_queue:
        state.lane1_queue.remove(id)
    elif id in state.lane2_queue:
        state.lane2_queue.remove(id)


# 車輌が侵入するレーンをランダムに決定
def _getDepartLane(state: DefaultSimulationState, edge_id: str) -> str:
    lanes = traci.edge.getLaneNumber(edge_id)
    # 除外するレーンを指定
    exclude_lanes: list[str] = []  # 路肩がないので除外なし
    available_lanes = [str(i) for i in range(lanes) if str(i) not in exclude_lanes]

    # 各レーンのキューの長さを取得
    queue_length = {"0": len(state.lane0_queue), "1": len(state.lane1_queue), "2": len(state.lane2_queue)}

    # キャンセルキューがないレーンを取得
    lanes_without_queue = [lane for lane in available_lanes if queue_length[lane] == 0]

    if lanes_without_queue:
        # キャンセルキューがないレーンがあればその中からランダムに選択
        departLane = random.choice(lanes_without_queue)
    else:
        # 最小キューのレーンを取得し、その中からランダムに選択
        min_queue_length = min(queue_length.values())
        min_queue_lanes = [lane for lane, length in queue_length.items() if length == min_queue_length]
        departLane = random.choice(min_queue_lanes)

    return departLane


def _shouldContinueSimWithVehiclesCount() -> bool:
    numVehicles = traci.simulation.getMinExpectedNumber()
    return True if numVehicles > 0 else False


def _shouldContinueSimWithSimulationTime(state: DefaultSimulationState) -> bool:
    sumo_time = traci.simulation.getTime()
    if sumo_time % 10 == 0:
        now = datetime.now().time()
        print(
            "====================================================",
            "\nTIME:",
            sumo_time,
            "\nNow:",
            now,
            "\n====================================================",
        )
    return True if sumo_time < state.simulation_time else False


def _add_vehicle(state: DefaultSimulationState) -> None:
    sumo_time = traci.simulation.getTime()

    # if sumo_time in departTime_r_pass:
    if sumo_time in state.departTime_r_pass:
        departLane = _getDepartLane(state, "MainLane1")
        traci.vehicle.add(
            vehID=str(state.veh_id),
            routeID="r_pass",
            typeID="CAV",
            departLane=departLane,
            departPos="base",
            departSpeed="last",
        )
        instance = DefaultCAV(state.veh_id)
        state.vehicle_instance.append(instance)

        if departLane == "0":
            state.lane0_queue.append(str(state.veh_id))
        elif departLane == "1":
            state.lane1_queue.append(str(state.veh_id))
        elif departLane == "2":
            state.lane2_queue.append(str(state.veh_id))

        state.veh_id += 1

    # if sumo_time in departTime_r_exit:
    if sumo_time in state.departTime_r_exit:
        departLane = _getDepartLane(state, "MainLane1")
        traci.vehicle.add(
            vehID=str(state.veh_id),
            routeID="r_exit",
            typeID="CAV",
            departLane=departLane,
            departPos="base",
            departSpeed="last",
        )
        instance = DefaultCAV(state.veh_id)
        state.vehicle_instance.append(instance)

        if departLane == "0":
            state.lane0_queue.append(str(state.veh_id))
        elif departLane == "1":
            state.lane1_queue.append(str(state.veh_id))
        elif departLane == "2":
            state.lane2_queue.append(str(state.veh_id))

        state.veh_id += 1


def _get_options() -> optparse.Values:
    # define options for this script and interpret the command line
    optParser = optparse.OptionParser()
    optParser.add_option(
        "--nogui",
        action="store_true",
        default=False,
        help="run the commandline version of sumo",
    )
    options, _ = optParser.parse_args()
    return options


if __name__ == "__main__":
    # コマンドライン引数を取得
    options = _get_options()
    args = sys.argv
    seed = args[1]  # 乱数のシード(等しいseedで実行すると同じ結果が得られる)
    random.seed(seed)
    inflow_pass = int(args[2])  # 車両の流入数 pass
    inflow_exit = int(args[3])  # 車両の流入数 exit

    stats = SimulationStatistics(filename="default", output_dir="simulationStatistics/statistics/default")

    # this script has been called from the command line. It will start sumo as a server, then connect and run
    # グローバル変数sumoBinaryもmain内で決定し、その後各関数に渡す
    if options.nogui:
        sumoBinary = checkBinary("sumo")
    else:
        sumoBinary = checkBinary("sumo-gui")

    _startSim(sumoBinary)
    state = DefaultSimulationState(SIM_TIME)
    run(state, inflow_pass, inflow_exit, stats, seed)
