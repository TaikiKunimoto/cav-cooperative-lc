"""v2 の車両クラス（自己完結）。

``cav.base_cav`` / ``cav.custom_cav`` からは継承・import しない。縦方向追従（control_speed）と状態観測、
Layer2 の自車挙動・必須LC活性化・障害物化を持つ。Layer1 調停は ``rsu`` が外側で行う。
データ（状態）と振る舞いを1つの pydantic BaseModel にまとめる（``V2Simulation`` と同じ流儀）。
縦方向の追従ロジックは ``custom_cav`` の挙動を踏襲した自己完結実装。
"""

import math
import os
import sys
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from status.status import CarStatus
from utils.traci_wrapper import (
    get_lane_max_speed,
    get_sim_time,
    get_veh_departure,
    get_veh_lane_id,
    get_veh_lane_index,
    get_veh_lane_position,
    get_veh_leader,
    get_veh_pos,
    get_veh_road_id,
    get_veh_route_id,
    get_veh_speed,
    get_veh_type,
    slow_down,
)
from v2.constants import (
    ACTIVATION_MARGIN,
    MAX_ACCEL,
    MAX_DECEL,
    MIN_GAP,
    SUMO_DEFAULT_LC_MODE,
)
from v2.layer2.safety import Safety
from v2.lc_request import LCOperation, LCRequest

if TYPE_CHECKING:
    from simulationStatistics.simulation_statistics import SimulationStatistics

if "SUMO_HOME" in os.environ:
    tools = os.path.join(os.environ["SUMO_HOME"], "tools")
    sys.path.append(tools)
else:
    sys.exit("please declare environment variable 'SUMO_HOME'")
import traci


class V2CAV(BaseModel):
    """1車両の状態（データ）とローカル制御（Layer2 の自車挙動）。Layer1 調停は ``rsu`` が外側で行う。

    生成時（SUMO に add 済みの前提）に ``model_post_init`` で traci から type/route/lane を取得し、SUMO の
    自律車線変更・速度制御を無効化する。静的スカラー優先度（0–7）は持たない（優先度は EDF の鍵で都度決まる）。
    """

    id: str
    type_id: str | None = None
    route: str | None = None  # SUMO ルート（net を通すための経路。機構は参照しない）
    # 必須LC操作（環境が生成時に与える / 障害物エスカレーションで append）。空なら必須LCなし(through)。
    # 未達成(lane!=target)のうち最も deadline が近いものが active_operation（要求を出す対象）。
    operations: list[LCOperation] = Field(default_factory=list)
    # 障害物（突発）: True の車は停止し続け、調停（要求・提供）から除外される。snapshot には載る（安全判定で回避）。
    is_obstacle: bool = False
    # 非協調モード（--policy off / off-late）: True なら SUMO 標準制御（LC2013・Krauss）を無効化せず残す。
    # traci からの車線変更・速度指令は一切行わない前提（観測・締切判定の計測だけが動く）。
    sumo_default_control: bool = False
    # 遅通知（--policy off-late）: 必須LC車は活性化窓に入るまで車線変更を凍結し、窓進入時に解禁する。
    # 提案と同じ「締切付き要求が発生してから行動を開始する」情報タイミングに揃えるための制約。
    mlc_notice_at_activation: bool = False
    lc_unlocked: bool = False  # off-late の解禁を一度だけ行うためのフラグ
    road: str | None = None
    lane_id: str | None = None
    lane: int | None = None
    lane_pos: float | None = None
    pos_x: float | None = None
    pos_y: float | None = None
    speed: float = 0.0
    leader: tuple[str, float] | None = None
    leader_distance: float | None = None
    leader_speed: float | None = None
    length: float = 5.0
    safety_gap: float = MIN_GAP
    status: CarStatus = CarStatus.NORMAL
    do_not_speed_up: bool = False
    # Layer1 調停の役割（毎Tc フル再構築）。提供車↔要求車の対応を保持する。
    providing_to_id: str | None = None  # 自分が gap を提供している相手（要求車）
    receiving_from_id: str | None = None  # 自分に gap を提供してくれる相手（提供車）
    sim_time: float = 0.0
    departure_time: float | None = None
    arrival_time: float | None = None
    speed_history: list[float] = Field(default_factory=list)
    emergency_brake_counter: int = 0

    def model_post_init(self, __context: Any) -> None:
        """生成直後（SUMO に車両 add 済み）に traci から属性取得＋SUMO の自律制御を無効化する。

        車線変更は traci.changeLane のみ、速度も traci で管理し、提供車の協調減速など制御介入を可能にする。
        """
        self.type_id = get_veh_type(self.id)
        self.route = get_veh_route_id(self.id)
        self.lane_id = get_veh_lane_id(self.id)
        if not self.sumo_default_control:
            traci.vehicle.setLaneChangeMode(self.id, 0)
            traci.vehicle.setSpeedMode(self.id, 0)
        elif self.mlc_notice_at_activation and self.operations:
            # off-late: 必須LC車は要求（活性化）を知るまで車線変更を凍結。速度は SUMO 標準のまま
            traci.vehicle.setLaneChangeMode(self.id, 0)
        # minGap・tau は車両物理の統一（提案/ベースライン共通、F4）なのでポリシーに依らず揃える
        traci.vehicle.setMinGap(self.id, MIN_GAP)
        traci.vehicle.setTau(self.id, 1.0)

    # --- 時刻記録 ---
    def record_departure_time(self) -> None:
        """出発（投入）時刻を記録する。出発後も snapshot には載るが、出発前は存在しない車両IDである点に注意。"""
        self.departure_time = get_veh_departure(self.id)

    def record_arrival_time(self) -> None:
        """到着（範囲外に出た）時刻を記録する。到着後も snapshot には載るが、以降の観測・制御は行わない。"""
        self.arrival_time = get_sim_time()

    def accumulate_exit_stats(self, stats: "SimulationStatistics", collided: bool) -> None:
        """範囲外に出た自車の走行時間・平均速度・締切達成を統計に加算する（全体のみ。route="" でグループ別には入れない）。"""
        if self.departure_time is not None and self.arrival_time is not None:
            stats.calculate_travel_time("", self.departure_time, self.arrival_time)
        stats.calculate_vehicle_average_speed("", self.speed_history)
        self.record_deadline_outcome(stats, collided)

    def record_deadline_outcome(self, stats: "SimulationStatistics", collided: bool) -> None:
        """活性化した非回避必須LC操作について、締切達成（完了数/要求数）を統計へ記録する（F3）。

        要求数＝活性化した非回避操作（spawn 時の本来の必須LC。回避操作・未活性は除く）、
        完了数＝うち締切位置までに目標レーンへ到達したもの（達成率の分子。2指標分離方式のため
        衝突は織り込まない）。衝突に関与した車の操作数は collided（安全性の参考列）として別掲する。
        stuck で running のまま終わった車は完了せず要求のみ計上＝未完了（incomplete）として現れる
        （テレポート無効方針 §2.4.1 と整合）。出口時とシミュレーション終了時の双方から呼ばれ、
        出口/残存いずれの車も一度だけ計上される。
        """
        requested = [op for op in self.operations if not op.is_avoidance and op.activated]
        if not requested:
            return
        completed = sum(1 for op in requested if op.completed_in_time)
        n_collided = len(requested) if collided else 0
        stats.record_deadline_achievement(len(requested), completed, n_collided, len(requested) - completed)

    def mandatory_failure_rows(self, env_name: str, collided: bool, phase: str) -> "list[dict[str, Any]]":
        """未完了または衝突関与の活性化済み非回避操作の個票行を返す（該当なしなら空）。

        個票の対象＝(a) 締切内に完了しなかった要求（達成率の失敗）と (b) 衝突関与車の要求
        （完了済みでも安全性の透明性のため記録。completed_in_time_raw 列で完了有無が分かる）。
        phase: "exit"=範囲外へ退出した時点 / "end"=終了時に running のまま。分類は
        COLLIDED（衝突関与）／TIMEOUT_STUCK（終了時未完了＝立ち往生）／
        EXITED_INCOMPLETE（未完了のまま退出。通常起きない計測異常の検知用）。
        """
        rows: list[dict[str, Any]] = []
        for op in self.operations:
            if op.is_avoidance or not op.activated:
                continue
            if op.completed_in_time and not collided:
                continue
            if collided:
                classification = "COLLIDED"
            elif phase == "end":
                classification = "TIMEOUT_STUCK"
            else:
                classification = "EXITED_INCOMPLETE"
            rows.append(
                {
                    "veh_id": self.id,
                    "env": env_name,
                    "classification": classification,
                    "phase": phase,
                    "route": self.route,
                    "target_lane": op.target_lane,
                    "deadline_pos": op.deadline_pos,
                    "activation_time": op.activation_time,
                    "activation_pos": op.activation_pos,
                    "completed_in_time_raw": op.completed_in_time,
                    "collided": collided,
                    "final_road": self.road,
                    "final_lane": self.lane,
                    "final_lane_pos": self.lane_pos,
                    "final_speed": round(self.speed, 2),
                }
            )
        return rows

    def active_operation(self) -> LCOperation | None:
        """未完了（is_done が False）の操作のうち、最も deadline が近いものを返す（なければ None）。"""
        pending = [op for op in self.operations if not op.is_done(self.lane, self.lane_pos)]
        if not pending:
            return None
        return min(pending, key=lambda op: op.deadline_pos)

    def update_activation(self, mainlane_edge: str, margin: float = ACTIVATION_MARGIN) -> None:
        """各未達成操作が活性化窓に初めて入った時刻を記録する（早め固定活性化、操作ごとに一度だけ）。

        off-late（遅通知の非協調）はここが「要求を知る」瞬間: 凍結していた車線変更を SUMO 標準
        （LC2013 の既定モード）へ解禁し、以降の完遂は LC2013 に委ねる。
        margin は既定で ACTIVATION_MARGIN（柱B-2 の猶予距離比較でのみ変える。解禁位置も連動する）。
        """
        for op in self.operations:
            if op.activated:
                continue
            if LCRequest.in_activation_window(
                mainlane_edge, self.road, op.target_lane, op.deadline_pos, self.lane, self.lane_pos, margin
            ):
                op.activated = True
                op.activation_time = self.sim_time
                op.activation_pos = self.lane_pos
        if self.mlc_notice_at_activation and not self.lc_unlocked and any(op.activated for op in self.operations):
            traci.vehicle.setLaneChangeMode(self.id, SUMO_DEFAULT_LC_MODE)
            self.lc_unlocked = True

    def update_deadline_achievement(self, mainlane_edge: str) -> None:
        """締切位置までに目標レーンへ到達した非回避操作を一度だけ記録する（締切達成率 F3 の分子判定）。

        本線上でのみ判定する（本線外は lane index が別 edge のもので target_lane と比較できない）。
        v2 の車線変更は活性化済み要求への調停実行（Layer2）でしか起きないため、到達は本線上で確定する。
        """
        if self.road != mainlane_edge:
            return
        for op in self.operations:
            if op.completed_in_time:
                continue
            if op.reached_target_in_time(self.lane, self.lane_pos):  # 回避操作は本メソッド内で False を返す
                op.completed_in_time = True

    def make_obstacle(self) -> None:
        """この車を障害物（突発）にする。停止し、is_obstacle で調停（要求・提供）から外れる。

        操作リストは捨てない（締切達成率 F3 の母数のため保持）。本来の必須LCが活性化済みのまま障害物化
        された車は、停止して目標へ到達できず終了時に未完了＝失敗として計上される（テレポート無効方針 §2.4.1）。
        要求生成側 ``LCRequest.from_obs`` が is_obstacle を除外するため、操作を残しても障害物が要求を出すことはない。

        非協調ポリシー（SUMO 標準制御が生きている車）でも障害物という摂動を提案と同一にするため、
        速度モード・車線変更を明示的に無効化してから停止させる: 既定 speedMode のままでは緩減速で
        指定位置より先に止まり、LC2013 が生きていると停止車が自力で車線変更して封鎖が解けてしまう。
        edf/none では生成時に設定済みの値の再設定（冪等・挙動不変）。
        """
        self.is_obstacle = True
        traci.vehicle.setSpeedMode(self.id, 0)
        traci.vehicle.setLaneChangeMode(self.id, 0)
        traci.vehicle.setSpeed(self.id, 0.0)

    # --- 状態観測 ---
    def update_self_observation(self) -> None:
        """毎step、自車の観測値を traci から取得して自身を更新する。"""
        self.sim_time = get_sim_time()
        pos = get_veh_pos(self.id)
        self.pos_x, self.pos_y = pos[0], pos[1]
        self.lane_pos = get_veh_lane_position(self.id)
        self.speed = get_veh_speed(self.id)
        self.speed_history.append(self.speed)
        self.road = get_veh_road_id(self.id)
        self.lane = get_veh_lane_index(self.id)
        self.lane_id = get_veh_lane_id(self.id)
        self.leader = get_veh_leader(self.id, 0)
        self.leader_distance = self.leader[1] if self.leader is not None else None
        self.leader_speed = get_veh_speed(self.leader[0]) if self.leader is not None else None
        self._calculate_safety_gap()

    # --- 縦方向制御（car-following）---
    def control_speed(self) -> None:
        """前方車両との車間に応じた速度制御。SUMO の安全制御は無効化済みのため自前で行う。"""
        if self.is_obstacle:
            traci.vehicle.setSpeed(self.id, 0.0)  # 障害物は停止し続ける
            return
        if self.leader_distance is not None and self.leader_distance < MIN_GAP:
            if self.leader_speed is not None:
                self._emergency_brake(self.leader_speed)
            return

        # 臨界制動バンド: 相対制動（最大減速で前車に追突しない）に必要な距離を割り込んだら、
        # 「前車速度 − 1 を追いかける」通常追従をやめ、最大減速で前車速度へ合わせにいく。通常追従は
        # 前車が強く減速し続けると 1〜2step 分の追跡遅れで車間を食い込み、rear-end グレーズ
        # （gap −0.0〜−1m の接触）になる。目標を 0 でなく前車速度にするのが重要: 全停止プロファイルは
        # 行列末尾への接近で過剰な早期停止（standoff）となり、衝撃波を増幅して織込み環境の流入を
        # 崩壊させる。余裕は固定 1m ＋ 離散制御ラグ分（自車速度×0.2s）。
        if self.leader_distance is not None and self.leader_speed is not None and self.speed > self.leader_speed:
            speed_diff = self.speed - self.leader_speed
            braking_needed = (self.speed**2 - self.leader_speed**2) / (2 * abs(MAX_DECEL)) + 1.0 + 0.2 * self.speed
            if self.leader_distance < braking_needed:
                slow_down(self.id, self.leader_speed, speed_diff / abs(MAX_DECEL))
                return

        # 協調・車線変更中は加速しない
        self.do_not_speed_up = self.status in (CarStatus.YIELDING, CarStatus.LANE_CHANGING)

        if self.lane_id is None:
            return
        speed_limit = get_lane_max_speed(self.lane_id)

        if self.leader is None or self.leader_speed is None or self.leader_distance is None:
            self._control_speed_by_speed_limit(speed_limit)
            return

        speed_diff = self.speed - self.leader_speed
        min_duration = self._safe_decel_duration(speed_diff)
        ttc = self._ttc(self.leader_distance, speed_diff)

        if self.leader_distance >= self.safety_gap:
            if speed_diff <= 0 or min_duration < ttc:
                self._control_speed_by_speed_limit(speed_limit)
            else:
                slow_down(self.id, self.leader_speed, min(ttc, min_duration))
        else:
            if self.do_not_speed_up:
                return
            if speed_diff >= 0:
                target_speed = self.leader_speed - 1 if self.leader_speed > 1 else 0.0
                slow_down(self.id, target_speed, min(ttc, min_duration))

    def _control_speed_by_speed_limit(self, speed_limit: float) -> None:
        """制限速度に合わせて加減速する。"""
        if self.speed > speed_limit:
            slow_down(self.id, speed_limit, self._safe_decel_duration(self.speed - speed_limit))
        elif not self.do_not_speed_up:
            slow_down(self.id, speed_limit, self._safe_accel_duration(speed_limit - self.speed))

    def _emergency_brake(self, target_speed: float) -> None:
        """衝突回避の緊急減速。"""
        self.emergency_brake_counter += 1
        traci.vehicle.setSpeed(self.id, min(target_speed, self.speed, 1.0))

    # --- 計算ヘルパ ---
    def _calculate_safety_gap(self) -> None:
        """追従の安全車間 ＝ 挿入と同一の安全ギャップ G_req（空走 v×δ + 制動距離 + minGap）。

        旧実装は空走項に人間の反応時間 REACTION_TIME(0.75s) を使い、挿入側 ``Safety.g_req``（δ系）と
        二系統に分裂していた。追従側も δ（通信遅延 DELAY）に統一し、安全車間の定義を1本化する
        （δ=0 の理想通信では空走項ゼロ。計画 T4「REACTION_TIME 依存を除去」の達成）。
        """
        self.safety_gap = Safety.g_req(self.speed)

    @staticmethod
    def _safe_decel_duration(speed_diff: float) -> float:
        """最大減速で速度差を0にするのに要する時間。"""
        return speed_diff / abs(MAX_DECEL) if speed_diff > 0 else 0.0

    @staticmethod
    def _safe_accel_duration(speed_diff: float) -> float:
        """最大加速で速度差を0にするのに要する時間。"""
        return speed_diff / abs(MAX_ACCEL) if speed_diff > 0 else 0.0

    @staticmethod
    def _ttc(distance: float, speed_diff: float) -> float:
        """現在の速度差で進んだ際の衝突までの時間（TTC）。"""
        return distance / speed_diff if speed_diff > 0 else math.inf
