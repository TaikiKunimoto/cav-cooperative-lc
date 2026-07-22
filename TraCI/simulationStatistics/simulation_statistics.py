import csv
from datetime import datetime
import os
from typing import Any, Optional

from pydantic import BaseModel

TIME_STEP = 0.1  # [s]
TTC_THRESHOLD = 2.0  # TTCの閾値 [s]


class SimulationStatsData(BaseModel):
    total_travel_time: float = 0.0
    r_pass_total_travel_time: float = 0.0
    r_exit_total_travel_time: float = 0.0
    vehcile_speed_data: list[float] = []
    r_exit_vehicle_speed_data: list[float] = []
    r_pass_vehicle_speed_data: list[float] = []
    total_TET: float = 0.0  # Time Exposed TTC (TET) の累積値
    min_TTC: float = float("inf")  # 記録された最小TTC
    emergency_brake_count: int = 0  # 急ブレーキ回数
    mandatory_lc_total: int = 0  # 締切達成率の母数: 活性化した非回避必須LC操作の数（要求数）
    mandatory_lc_completed: int = 0  # 締切達成率の分子: うち締切までに目標レーンへ到達した数（完了数）


class SimulationStatistics:
    def __init__(
        self,
        filename: str,
        output_dir: str = "simulationStatistics/statistics",
        track_deadline_achievement: bool = False,
    ):
        # track_deadline_achievement: 締切達成率（必須LC完了率）の列をCSVに出すか。提案(v2)のみ True。
        # v1(default/simple/custom)は False のままで列を出さず、既存CSVスキーマ（golden基準）をバイト不変に保つ。
        self.data = SimulationStatsData()
        # 評価スイープ用フック（環境変数）。未設定なら従来動作と完全一致（golden 不変）。
        # EVAL_OUTPUT_DIR: 出力先を一括で別ディレクトリへ集約。EVAL_OUTPUT_NAME: ファイル名を決定的に固定
        # （タイムスタンプを付けず一意名で上書き＝冪等・再開可能・並列でも衝突なし。run_sweep が解決に使う）。
        self.output_dir = os.environ.get("EVAL_OUTPUT_DIR") or output_dir
        self.track_deadline_achievement = track_deadline_achievement
        os.makedirs(self.output_dir, exist_ok=True)
        self.filename = self._create_filename(filename)
        self._create_csv_with_headers()

    def calculate_travel_time(self, route: str, departure_time: float, arrival_time: float) -> float:
        """車両ごとの走行時間を計算し、累積値を更新"""
        travel_time = arrival_time - departure_time
        self.data.total_travel_time += travel_time

        if route == "r_pass":
            self.data.r_pass_total_travel_time += travel_time
        elif route == "r_exit":
            self.data.r_exit_total_travel_time += travel_time

        return travel_time

    def _calculate_average_travel_time(self, total_travel_time: float, exit_vehicle: list[str]) -> Optional[float]:
        """全体または各グループの平均走行時間を計算"""
        if not exit_vehicle:
            return None
        return total_travel_time / len(exit_vehicle)

    def calculate_vehicle_average_speed(self, route: str, speed_history: list[float]) -> Optional[float]:
        """車両ごとに平均速度を計算し、各グループに保存"""
        if not speed_history:
            return None

        average_speed = sum(speed_history) / len(speed_history)
        self.data.vehcile_speed_data.append(average_speed)

        if route == "r_pass":
            self.data.r_pass_vehicle_speed_data.append(average_speed)
        elif route == "r_exit":
            self.data.r_exit_vehicle_speed_data.append(average_speed)

        return average_speed

    def _calculate_average_speed(self, vehicle_speed_data: list[float]) -> Optional[float]:
        """リストに蓄積された速度データから平均速度を計算"""
        if not vehicle_speed_data:
            return None
        return sum(vehicle_speed_data) / len(vehicle_speed_data)

    def _calculate_fairness_index(self, results: dict[str, Any]) -> tuple[int, int, float]:
        """
        追い越し回数と公平性指標を計算
        ・results["r_exit_departed_vehicle"] の順序とresults["r_exit_exit_vehicle"] および results["r_exit_running_vehicle"] をもとに算出
        """
        overtake_count = 0
        overtaking_vehicles = set()

        departed_order = {veh_id: i for i, veh_id in enumerate(results.get("r_exit_departed_vehicle", []))}
        combined = results.get("r_exit_exit_vehicle", []) + results.get("r_exit_running_vehicle", [])
        combined_order = {veh_id: i for i, veh_id in enumerate(combined)}

        for veh_id, dep_order in departed_order.items():
            if veh_id in combined_order:
                current_order = combined_order[veh_id]
                if current_order < dep_order:
                    overtake_count += dep_order - current_order
                    overtaking_vehicles.add(veh_id)

        fairness_index = overtake_count / len(overtaking_vehicles) if overtaking_vehicles else 0
        return overtake_count, len(overtaking_vehicles), fairness_index

    def calculate_TTC(self, distance: float, leader_speed: float, follower_speed: float) -> Optional[float]:
        """Time-to-collision (TTC) を計算し、閾値以下の場合は TET を更新"""
        relative_speed = follower_speed - leader_speed
        if relative_speed <= 0:
            return None

        ttc = distance / relative_speed

        if ttc < self.data.min_TTC:
            self.data.min_TTC = ttc

        if ttc < TTC_THRESHOLD:
            self.data.total_TET += TIME_STEP

        return ttc

    def increment_emergency_brake(self) -> None:
        """急ブレーキの回数をインクリメント"""
        self.data.emergency_brake_count += 1

    def record_deadline_achievement(self, requested: int, completed: int) -> None:
        """必須LC（非回避・活性化済み）の要求数と完了数を累積する（締切達成率の母数/分子）。"""
        self.data.mandatory_lc_total += requested
        self.data.mandatory_lc_completed += completed

    def _deadline_achievement_rate(self) -> Optional[float]:
        """締切達成率＝完了数/要求数（要求が無ければ None）。"""
        if self.data.mandatory_lc_total == 0:
            return None
        return self.data.mandatory_lc_completed / self.data.mandatory_lc_total

    def deadline_summary(self) -> tuple[int, int, Optional[float]]:
        """締切達成の (要求数, 完了数, 達成率) を返す（達成率は要求が無ければ None）。表示・ログ用の公開アクセサ。"""
        return self.data.mandatory_lc_total, self.data.mandatory_lc_completed, self._deadline_achievement_rate()

    def _create_filename(self, filename: str) -> str:
        override = os.environ.get("EVAL_OUTPUT_NAME")
        if override:
            # 決定的な一意名（タイムスタンプなし）。run_sweep がパラメータを符号化して渡し、出力を確実に解決する。
            return f"{self.output_dir}/{override}.csv"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        return f"{self.output_dir}/{filename}_{timestamp}.csv"

    def _create_csv_with_headers(self) -> None:
        headers = [
            "simulation_time",
            "seed",
            "inflow_pass",
            "inflow_exit",
            "total_generated_vehicles",
            "total_departed_vehicles",
            "r_pass_departed_vehicles",
            "r_exit_departed_vehicles",
            "running_vehicles",
            "exited_vehicles",
            "canceled_vehicles",
            "traffic volume",
            "average_travel_time",
            "average_r_pass_travel_time",
            "average_r_exit_travel_time",
            "average_speed",
            "average_r_pass_speed",
            "average_r_exit_speed",
            "overtaking_count",
            "overtaking_vehicle_count",
            "fairness_index",
            "min_TTC",
            "TET",
            "total_collisions",
            "total_vehicles_involved",
            "max_tail_position",
        ]
        if self.track_deadline_achievement:
            # 提案(v2)のみ。締切達成率（必須LC完了率）= mandatory_lc_completed / mandatory_lc_total
            headers += ["mandatory_lc_total", "mandatory_lc_completed", "deadline_achievement_rate"]
        with open(self.filename, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(headers)

    def add_result(
        self, simulation_time: float, seed: str, inflow_pass: int, inflow_exit: int, results: dict[str, Any]
    ) -> None:
        """
        シミュレーション結果を CSV に追記する
        ※ results は各種車両の情報や衝突、交通量などを含む辞書とする
        """
        overtake_count, overtaking_vehicle_count, fairness_index = self._calculate_fairness_index(results)
        row = [
            simulation_time,
            seed,
            inflow_pass,
            inflow_exit,
            results.get("total_generated_vehicle"),
            len(results.get("total_departed_vehicle", [])),
            len(results.get("r_pass_departed_vehicle", [])),
            len(results.get("r_exit_departed_vehicle", [])),
            len(results.get("running_vehicle", [])),
            len(results.get("exit_vehicle", [])),
            len(results.get("canceled_vehicle", [])),
            results.get("traffic_volume"),
            self._calculate_average_travel_time(self.data.total_travel_time, results.get("exit_vehicle", [])),
            self._calculate_average_travel_time(
                self.data.r_pass_total_travel_time, results.get("r_pass_exit_vehicle", [])
            ),
            self._calculate_average_travel_time(
                self.data.r_exit_total_travel_time, results.get("r_exit_exit_vehicle", [])
            ),
            self._calculate_average_speed(self.data.vehcile_speed_data),
            self._calculate_average_speed(self.data.r_pass_vehicle_speed_data),
            self._calculate_average_speed(self.data.r_exit_vehicle_speed_data),
            overtake_count,
            overtaking_vehicle_count,
            fairness_index,
            self.data.min_TTC,
            self.data.total_TET,
            results.get("total_collisions"),
            results.get("total_vehicles_involved"),
            results.get("max_tail_position"),
        ]
        if self.track_deadline_achievement:
            row += [
                self.data.mandatory_lc_total,
                self.data.mandatory_lc_completed,
                self._deadline_achievement_rate(),
            ]
        with open(self.filename, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(row)
