"""v2 のシミュレーション（状態保持＋実行）と毎step メインループ（2フェーズの骨格）。

``V2Simulation`` が1回のシミュレーションの状態保持と実行（run）・流入・到着処理・統計配線を担う。
調停（Layer1=RSU/EDF）・実行（Layer2）・観測（V2CAV）・障害物（Obstacle）は各クラスのメソッドに委譲する。
``custom.py`` の run() を踏襲しつつ V2CAV を用い、タイムスペース図(matplotlib)出力は省略する。
"""

from datetime import datetime
import os
import random
import sys
from typing import Any, NamedTuple

from pydantic import BaseModel, Field

from simulationStatistics.simulation_statistics import SimulationStatistics
from utils.traci_wrapper import (
    get_colliding_veh_id_list,
    get_edge_lane_number,
    get_lane_length,
    get_sim_arrived_veh_id_list,
    get_sim_departed_veh_id_list,
    get_sim_time,
    get_veh_id_list,
)
from v2.constants import (
    ACTIVATION_MARGIN,
    DRAIN_MAX,
    TC,
    TIME_STEP,
)
from v2.environment import Environment, Group
from v2.layer1.priority import EDF, FCFS
from v2.layer1.rsu import RSU, Assignment
from v2.layer2.pair_executor import Layer2
from v2.lc_request import LCOperation, LCRequest
from v2.obstacle import Obstacle
from v2.obstacle_metrics import ObstacleMetrics
from v2.policy import Policy
from v2.snapshot import Snapshot
from v2.v2_cav import V2CAV

if "SUMO_HOME" in os.environ:
    tools = os.path.join(os.environ["SUMO_HOME"], "tools")
    sys.path.append(tools)
else:
    sys.exit("please declare environment variable 'SUMO_HOME'")
import traci

OUTPUT_DIR = "simulationStatistics/statistics/v2"


class GroupDepartTimes(NamedTuple):
    """グループと、その流入時刻リストのペア（seed で決まる投入スケジュール）。``for group, times in ...`` 展開可。"""

    group: Group
    times: list[float]  # 投入時刻 [s] の昇順リスト


class CollisionEvent(NamedTuple):
    """衝突イベント（検出時刻と巻き込まれた車両ID群）。``for time, vehicle_ids in ...`` 展開可。"""

    time: float
    vehicle_ids: list[str]


class V2Simulation(BaseModel):
    """1回のシミュレーションの状態保持と実行（run）・流入・到着処理を担う。

    可変フィールドは pydantic の default_factory でインスタンスごとに生成され共有されない。
    heavily mutated なので frozen にしない（fields は V2CAV/Environment 等すべて pydantic ネイティブ）。
    """

    simulation_time: float
    env: Environment
    total_inflow: float  # 総流入量 Q [veh/h]
    mlc_ratio: float  # 必須LC車の比率 f（0..1）
    seed: str  # 乱数シード（統計ラベル用。random.seed の実行はエントリ側）
    obstacle: Obstacle | None = None  # 突発障害物（指定レーン・位置・時刻）。None なら障害物なし
    policy: Policy = Policy.EDF  # 調停ポリシー（アブレーション比較の切替軸。柱B）
    # 活性化窓 [m]（締切Dの何m手前から要求を活性化するか）。柱B-2 の猶予距離比較でのみ既定から変える。
    # off-late の車線変更解禁位置も本値に連動する
    activation_margin: float = ACTIVATION_MARGIN

    veh_id: int = 0  # 次に投入する車両へ振る連番ID
    # グループ別の流入時刻（環境のグループ定義順を保持）
    group_depart_times: list[GroupDepartTimes] = Field(default_factory=list)
    inflow_through: int = 0  # CSV 用: 必須LCなし車の流入量
    inflow_mlc: int = 0  # CSV 用: 必須LC車の流入量
    vehicles: list[V2CAV] = Field(default_factory=list)
    total_departed: list[str] = Field(default_factory=list)
    exit_vehicles: list[str] = Field(default_factory=list)
    canceled_vehicles: list[str] = Field(default_factory=list)
    collision_history: list[CollisionEvent] = Field(default_factory=list)
    collided_ids: set[str] = Field(default_factory=set)  # 衝突に一度でも関与した車両ID（達成率の未達成判定に使う）
    # 各車線の待ち行列（流入レーン選択の負荷分散に使う）。レーンは環境により可変なので動的に作る
    lane_queues: dict[str, list[str]] = Field(default_factory=dict)

    def run(self, stats: SimulationStatistics) -> None:
        self._set_environment()
        # 突発障害物。発生後、本線レーン数はエスカレーションの回避先選択に使う
        obstacle_num_lanes = get_edge_lane_number(self.env.mainlane_edge) if self.obstacle is not None else 0
        obstacle_placed_pos: float | None = None
        obstacle_target_id: str | None = None  # 位置到達トリガで pos 手前から監視中の車（pos 到達で停止＝障害物化）
        if self.obstacle is not None:
            self.obstacle.validate_for(self.env.mainlane_edge, obstacle_num_lanes, self.env.mainlane_length)
        # 柱B-2: 障害物 run の方式間比較指標（観測のみ・挙動不変。sidecar CSV へ出力）
        obstacle_metrics = (
            ObstacleMetrics(appear_time=self.obstacle.appear_time, obstacle_lane=self.obstacle.lane)
            if self.obstacle is not None
            else None
        )

        running_list: list[str] = []
        tc_accumulator = 0.0
        mandatory_failures: list[dict[str, Any]] = []  # 新定義で未達成となった必須LC要求の個票（run 終了時に CSV へ）
        inflow_closed = False  # simulation_time 到達時に一度だけ未発進車を除去（ドレーン開始）
        last_request_log_sec = -1
        tie_events = 0  # Phase A の鍵に同点が出た Tc ラウンド数（デッドロックフリーなら 0）
        double_assign_events = 0  # 同一提供車が二重割当された Tc ラウンド数（横取り禁止なら 0）
        total_lc = 0  # Layer2 で実行された瞬時LCの総数
        snap: Snapshot | None = None  # 直近 Tc のスナップショット（Layer2 実行で参照）
        assignments: list[Assignment] = []  # 直近 Tc の割当（Layer2 で実行）
        req_by_id: dict[str, LCRequest] = {}  # 直近 Tc の要求（id 引き）

        while self._should_continue():
            traci.simulationStep()
            self._check_collision()

            arrived_list = get_sim_arrived_veh_id_list()  # 直近stepで範囲外に出た（到着した）車両ID
            departed_list = get_sim_departed_veh_id_list()  # 直近stepで投入された（出発した）車両ID
            running_list = get_veh_id_list()  # 現在ネットワーク上を走行中の全車両ID

            current_time = get_sim_time()
            current_sec = int(current_time)

            # --- 流入締切（ドレーン開始）。混雑で挿入待ちのままの車両を SUMO の挿入キューから除去する。
            # 除去車は canceled（needs 未充足）として計上済みのまま残り、以降はネット上の車両だけを掃き出す ---
            if not inflow_closed and current_time >= self.simulation_time:
                inflow_closed = True
                self._remove_pending_insertions(running_list, arrived_list)

            # --- 到着/未発進/出発処理 と 観測。全車を先に観測し、スナップショット S_t の一貫性を保つ ---
            poplist: list[int] = []  # このstepで到着し self.vehicles から削除する要素インデックス
            active: list[V2CAV] = []  # このstep走行中で観測・調停・制御の対象となる V2CAV
            for index, veh in enumerate(self.vehicles):
                vid = veh.id

                # シミュレーション範囲を出た車両
                if vid in arrived_list:
                    poplist.append(index)
                    if veh.departure_time is None and vid not in departed_list:
                        # 未発進のまま流入締切で remove した車の arrival 通知。退出ではないので統計に入れない
                        # （canceled として計上済みのまま確定する）
                        continue
                    self.exit_vehicles.append(vid)
                    veh.record_arrival_time()
                    veh.accumulate_exit_stats(stats, vid in self.collided_ids)
                    mandatory_failures += veh.mandatory_failure_rows(self.env.name, vid in self.collided_ids, "exit")
                    if obstacle_metrics is not None:
                        obstacle_metrics.on_exit(veh)
                    continue

                # 混雑で未発進の車両
                if vid not in running_list:
                    if vid not in self.canceled_vehicles:
                        self.canceled_vehicles.append(vid)
                    continue

                # 発進した車両の出発時刻記録
                if vid in departed_list:
                    veh.record_departure_time()
                    self.total_departed.append(vid)
                    if vid in self.canceled_vehicles:
                        self.canceled_vehicles.remove(vid)

                veh.update_self_observation()  # 自車両の状態更新
                # TODO: ここ本当に必要か，一回で良いのか？
                veh.update_activation(self.env.mainlane_edge, self.activation_margin)  # 活性化を一度だけ記録
                veh.update_deadline_achievement(self.env.mainlane_edge)  # 締切までに目標到達したら一度だけ記録（F3）
                active.append(veh)
                self._update_lane_queue(vid)

                if veh.leader_distance is not None and veh.leader_speed is not None:
                    stats.calculate_TTC(veh.leader_distance, veh.leader_speed, veh.speed)

            # --- 突発障害物（位置到達トリガ）。appear_time 以降、指定レーンで pos に到達した最初の車を停止＝障害物化 ---
            if self.obstacle is not None and obstacle_placed_pos is None and current_time >= self.obstacle.appear_time:
                obstacle_target_id, obstacle_placed_pos = self.obstacle.place(
                    active, self.env.mainlane_edge, obstacle_target_id
                )
                if obstacle_metrics is not None and obstacle_placed_pos is not None:
                    obstacle_metrics.on_placed(obstacle_placed_pos, current_time)
            # 障害物より後方・同一レーンの through 車に必須LC（回避）を動的付与＝エスカレーション（コア機構 §4）
            if self.obstacle is not None and obstacle_placed_pos is not None:
                self.obstacle.escalate(active, self.env.mainlane_edge, obstacle_placed_pos, obstacle_num_lanes)
            if obstacle_metrics is not None:
                obstacle_metrics.step(active, self.env.mainlane_edge)

            # --- 毎Tc 2フェーズ調停。Phase A（鍵計算）→ Phase B（割当＋役割付与）。Layer2 実行は制御後に行う ---
            # 非協調（off/off-late）は Layer1/Layer2・縦制御を丸ごと行わず SUMO 標準（LC2013・Krauss）に委ねる。
            # 観測・活性化・締切判定・衝突検出（上の per-step 処理）は全ポリシー共通に動き続ける。
            tc_accumulator += TIME_STEP
            if not self.policy.is_noncooperative and tc_accumulator + 1e-9 >= TC:
                tc_accumulator = 0.0
                snap = Snapshot.capture(active, current_time, self.env.mainlane_edge)
                requests = LCRequest.build_all(snap, self.activation_margin)
                if self.policy is Policy.EDF:
                    keyed = EDF.order_requests(requests)  # Phase A: 全要求車の鍵を計算し EDF（dist昇順）にソート
                    assignments = RSU.arbitrate(keyed, snap)  # Phase B: 鍵順に提供車を占有印つきで確保
                else:
                    keyed = FCFS.order_requests(requests)  # Phase A': 発生順（早い者勝ち）にソート
                    assignments = RSU.arbitrate_fcfs(keyed, snap)  # Phase B': 最近傍後続の素朴割当
                req_by_id = {r.veh_id: r for _, r in keyed}
                RSU.apply_roles(active, assignments)  # 毎Tc フル再構築（提供車=YIELDING / 要求車=LANE_CHANGING）
                if not RSU.keys_unique(keyed):
                    tie_events += 1
                if not RSU.providers_unique(assignments):
                    double_assign_events += 1
                if current_sec % 50 == 0 and current_sec != last_request_log_sec:
                    RSU.log_assignments(current_time, keyed, assignments)
                    last_request_log_sec = current_sec

            # --- 制御（速度）。traci の速度指令は次 step に反映されるため観測順と独立 ---
            if not self.policy.is_noncooperative:
                for veh in active:
                    veh.control_speed()

            # --- Layer2 実行。制御後に呼び、協調減速の slowDown と changeLane が最後の指令になるようにする ---
            if snap is not None:
                total_lc += Layer2.execute_pairs(assignments, req_by_id, {veh.id: veh for veh in active}, snap)

            for i in sorted(poplist, reverse=True):
                self.vehicles.pop(i)

            self._add_vehicle()

        # 障害物指定があったのに最後まで配置できなければ、黙って no-op にせず原因つきで失敗させる
        if self.obstacle is not None and obstacle_placed_pos is None:
            raise RuntimeError(
                f"障害物を配置できませんでした: 指定レーン {self.obstacle.lane} で pos {self.obstacle.pos}m に到達する車両が "
                f"appear_time {self.obstacle.appear_time}s 以降シミュレーション終了まで現れませんでした。"
                "流入量(inflow)・レーン・位置・時刻の指定を確認してください。"
            )

        # 終了時、残車両（running のまま終わった車）の統計を更新。
        # 締切達成も計上＝未完了の stuck 車が要求のみ計上され失敗として現れる（テレポート無効方針 §2.4.1）。
        for veh in self.vehicles:
            if veh.id not in running_list:
                continue
            stats.calculate_vehicle_average_speed("", veh.speed_history)
            veh.record_deadline_outcome(stats, veh.id in self.collided_ids)
            mandatory_failures += veh.mandatory_failure_rows(self.env.name, veh.id in self.collided_ids, "end")

        stats.write_mandatory_failures(mandatory_failures)
        if obstacle_metrics is not None:
            still_running = [veh for veh in self.vehicles if veh.id in running_list]
            for suffix, rows in obstacle_metrics.summary_rows(still_running, get_sim_time()).items():
                stats.write_sidecar(suffix, rows)
        canceled_without_collision = [v for v in self.canceled_vehicles if v not in self.collided_ids]

        self._print_simulation_info(running_list)
        print(f"Phase A: Tc rounds with key ties (should be 0): {tie_events}")
        print(f"Phase B: Tc rounds with double-assigned providers (should be 0): {double_assign_events}")
        print(f"Layer2: total instant lane changes executed: {total_lc}")
        requested, completed, rate = stats.deadline_summary()
        rate_str = f"{rate:.3f}" if rate is not None else "-"
        print(f"Deadline: mandatory-LC completed/requested = {completed}/{requested} (rate {rate_str})")
        total_collisions, total_involved = self._print_collision_summary()

        results = {
            "total_generated_vehicle": self.veh_id,
            "total_departed_vehicle": self.total_departed,
            "running_vehicle": running_list,
            "exit_vehicle": self.exit_vehicles,
            "canceled_vehicle": canceled_without_collision,
            "traffic_volume": len(self.total_departed) * (3600 / self.simulation_time),
            "total_collisions": total_collisions,
            "total_vehicles_involved": total_involved,
        }
        stats.add_result(self.simulation_time, self.seed, self.inflow_through, self.inflow_mlc, results)
        traci.close()

    def _set_environment(self) -> None:
        """環境のグループ別流入量（総流入 Q × 必須LC比率 f から展開）に従い、流入時刻を乱数で決定（seed で決定的）。"""
        # 締切 D は「lane-drop 端＝本線エッジの終端」。公称値でなく net の実エッジ長を測って適用する
        # （公称〜実長の数m の差で、物理的に成功した終端間際の必須LC が失敗誤計上されるのを防ぐ）。
        actual_length = get_lane_length(f"{self.env.mainlane_edge}_0")
        self.env = self.env.with_measured_length(actual_length)
        print(f"[env] mainlane {self.env.mainlane_edge}: measured length {actual_length:.2f} m")
        for group, rate in self.env.group_rates(self.total_inflow, self.mlc_ratio):
            k = int((self.simulation_time / 3600) * rate)

            # ENVIRONMENTS のグループ定義の順序が変わると 同じseed でも流入時刻が変わるため注意
            seconds = sorted(random.sample(range(int(self.simulation_time)), k))

            # 1秒以上間隔を確保（同一stepへの偏りを避ける）
            times: list[float] = [round(n, 1) + 0.1 for n in seconds]
            self.group_depart_times.append(GroupDepartTimes(group, times))
            if group.target_lane is not None:
                self.inflow_mlc += int(rate)  # CSV 用: 必須LC車の流入量
            else:
                self.inflow_through += int(rate)  # CSV 用: 必須LCなし車の流入量
            print(f"depart_times[{group.name}]:", times)

    def _get_depart_lane(self, edge_id: str, allowed_lanes: tuple[int, ...] | None) -> str:
        """グループの投入レーン候補（None=全レーン）の中で待ち行列が最短のレーンを選ぶ（負荷分散）。"""
        lanes_total: int = get_edge_lane_number(edge_id)
        candidates = [str(i) for i in (allowed_lanes if allowed_lanes is not None else range(lanes_total))]
        queue_length = {lane: len(self.lane_queues.get(lane, [])) for lane in candidates}
        lanes_without_queue = [lane for lane in candidates if queue_length[lane] == 0]
        if lanes_without_queue:
            return random.choice(lanes_without_queue)
        min_length = min(queue_length.values())
        min_lanes = [lane for lane, length in queue_length.items() if length == min_length]
        return random.choice(min_lanes)

    def _add_vehicle(self) -> None:
        """流入時刻に到達した車両を SUMO に追加し V2CAV を生成する。各車は環境のグループから必須LC仕様を受け取る。"""
        sumo_time = get_sim_time()
        for group, depart_times in self.group_depart_times:
            if sumo_time not in depart_times:
                continue
            depart_edge = group.depart_edge if group.depart_edge is not None else self.env.mainlane_edge
            depart_lane = self._get_depart_lane(depart_edge, group.depart_lanes)
            traci.vehicle.add(
                vehID=str(self.veh_id),
                routeID=group.route,
                typeID="CAV",
                departLane=depart_lane,
                departPos="base",
                departSpeed="last",
            )
            operations: list[LCOperation] = []
            if group.target_lane is not None and group.deadline_pos is not None:
                operations.append(LCOperation(target_lane=group.target_lane, deadline_pos=group.deadline_pos))
            self.vehicles.append(
                V2CAV(
                    id=str(self.veh_id),
                    operations=operations,
                    sumo_default_control=self.policy.is_noncooperative,
                    mlc_notice_at_activation=self.policy is Policy.OFF_LATE,
                )
            )
            self.lane_queues.setdefault(depart_lane, []).append(str(self.veh_id))
            self.veh_id += 1

    def _update_lane_queue(self, veh_id: str) -> None:
        """走行を開始した車両を待ち行列から外す。"""
        for queue in self.lane_queues.values():
            if veh_id in queue:
                queue.remove(veh_id)
                return

    def _check_collision(self) -> None:
        """衝突を検出して記録（重複記録は抑制）。"""
        colliding_ids: list[str] = get_colliding_veh_id_list()
        if not colliding_ids:
            return
        collision_time = get_sim_time() - 0.1
        for time_val, vehicles in self.collision_history:
            if abs(time_val - collision_time) < 1.0 and set(vehicles) == set(colliding_ids):
                return
        self.collision_history.append(CollisionEvent(collision_time, colliding_ids))
        self.collided_ids.update(colliding_ids)
        print(f"Collision detected at {collision_time:.1f} between: {', '.join(colliding_ids)}")

    def _should_continue(self) -> bool:
        """流入期間中は常に継続。以降は必須LC が残る間だけドレーン継続（上限 DRAIN_MAX）。

        シミュ終了時刻で打ち切ると、終了直前にゾーンへ入った走行中の必須LC車が「失敗」として
        誤計上される（打ち切りバイアス）。流入は simulation_time で締め切り、ネット上の
        必須LC・回避操作が完了するまで掃き出してから終了する（障害物化した車の操作は対象外）。
        """
        sumo_time = get_sim_time()
        if sumo_time % 10 == 0:
            print("====================================================")
            print("TIME:", sumo_time, " Now:", datetime.now().time())
            print("====================================================")
        if sumo_time < self.simulation_time:
            return True
        if sumo_time >= self.simulation_time + DRAIN_MAX:
            return False
        return self._has_pending_operations()

    def _has_pending_operations(self) -> bool:
        """発進済み・非障害物の車両に未完了の LC 操作（必須・回避）が残っているか（ドレーン終了判定）。"""
        return any(
            veh.departure_time is not None and not veh.is_obstacle and veh.active_operation() is not None
            for veh in self.vehicles
        )

    def _remove_pending_insertions(self, running_list: list[str], arrived_list: list[str]) -> None:
        """流入締切時点で SUMO の挿入キューに残る（未発進の）車両を除去する。

        ドレーン中に遅延挿入されると需要期間（simulation_time）の外で流入が続いてしまうため、
        締切時点で走行中でも到着済みでもない車両＝挿入待ちを SUMO から取り除く。該当車は
        canceled（混雑で投入できなかった需要）として毎step の未発進処理で計上済みのまま確定する。
        """
        on_network = set(running_list) | set(arrived_list)
        removed = 0
        for veh in self.vehicles:
            if veh.id in on_network:
                continue
            traci.vehicle.remove(veh.id)
            removed += 1
        if removed:
            print(f"[drain] inflow closed at t={self.simulation_time:.0f}: removed {removed} pending insertions")

    def _print_simulation_info(self, running_list: list[str]) -> None:
        print("=====================================")
        print("simulation end")
        print("total generated vehicles :", self.veh_id)
        print("running vehicles :", len(running_list))
        print("exit vehicles :", len(self.exit_vehicles))
        print("total departed vehicles :", len(self.total_departed))
        print(f"traffic volume: {len(self.total_departed) * (3600 / self.simulation_time)} pcu/h")
        print("canceled vehicles :", len(self.canceled_vehicles))
        print("=====================================")

    def _print_collision_summary(self) -> tuple[int, int]:
        total_collisions = len(self.collision_history)
        total_vehicles_involved = sum(len(vehicles) for _, vehicles in self.collision_history)
        print("\n=== Collision Summary ===")
        print(f"Total collision events: {total_collisions}")
        print(f"Total vehicles involved: {total_vehicles_involved}")
        for time_val, vehicles in self.collision_history:
            print(f"Time {time_val:.1f}: Collision between vehicles: {', '.join(vehicles)}")
        return total_collisions, total_vehicles_involved
