"""柱B-2: 突発障害物シナリオの追加計測（挙動不変の観測のみ）。

封鎖発生時刻（appear_time）以降の窓で、方式間比較のための指標を観測値だけから集計する::

    - 封鎖通過スループット … 窓内に範囲外へ退出した台数（30s ビンの時系列つき）
    - 平均速度 … 窓内の全速度サンプル平均（障害物車は除く）
    - 最大待ち行列長 … 封鎖車線上流の停止車（v<1）の最遠尾端までの距離 [m] の最大値
    - 急減速イベント … 加速度 ≤ HARD_BRAKE_DECEL となった延べ step 数（speed_history から後計算）
    - 回避LC完了位置 … 回避操作を付与された車が封鎖車線を離れた時点の「障害物までの残距離」[m]

traci を一切呼ばず、毎 step の既存観測（V2CAV のフィールド）と speed_history から計算するため、
シミュレーション挙動・乱数消費・決定性に影響しない。結果はメイン CSV に列を足さず
sidecar CSV（``__obstacle_summary/__obstacle_avoidance/__obstacle_passages``）に書く
（メイン CSV のスキーマ不変＝既存 run とのバイト一致回帰を保つため）。
"""

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from v2.constants import TIME_STEP

if TYPE_CHECKING:
    from v2.v2_cav import V2CAV

HARD_BRAKE_DECEL: float = -3.0  # [m/s^2] 急減速イベントの閾値
QUEUE_SPEED_EPS: float = 1.0  # [m/s] これ未満を「停止（待ち行列）」とみなす
# 待ち行列の連鎖判定: 前の停止車（前端位置）との間隔がこの値以下なら同じ行列とみなす。
# 渋滞中の前端間隔 ≈ 車長5m + minGap2.8m。余裕を持たせつつ、流入端で徐行する投入直後の車など
# 行列と無関係な停止車を拾わないための上限
QUEUE_CHAIN_GAP_M: float = 15.0
PASSAGE_BIN_S: float = 30.0  # [s] 通過時系列のビン幅


class ObstacleMetrics(BaseModel):
    """突発障害物 run の方式間比較指標を、既存観測から集計する（sidecar CSV 出力用）。"""

    appear_time: float  # 封鎖発生時刻＝集計窓の開始 [s]
    obstacle_lane: int

    placed_pos: float | None = None  # 障害物の実配置位置（place 成功時に設定）
    placed_time: float | None = None
    max_queue_m: float = 0.0
    hard_brake_events: int = 0
    speed_sum: float = 0.0
    speed_samples: int = 0
    passage_times: list[float] = Field(default_factory=list)  # 窓内の退出時刻
    # 回避LCの完了記録: veh_id -> 封鎖車線を離れた時点の障害物までの残距離 [m]
    avoid_leave_remaining: dict[str, float] = Field(default_factory=dict)
    avoid_ids: set[str] = Field(default_factory=set)  # 回避操作を付与された車のID
    counted_ids: set[str] = Field(default_factory=set)  # speed/brake 集計済みの車（二重計上防止）

    def on_placed(self, pos: float, time: float) -> None:
        self.placed_pos = pos
        self.placed_time = time

    def step(self, active: "list[V2CAV]", mainlane_edge: str) -> None:
        """毎 step、配置済み障害物に対する待ち行列長と回避LC完了位置を観測から更新する。"""
        if self.placed_pos is None:
            return
        stopped_pos: list[float] = []  # 封鎖車線上流の停止車の前端位置
        for veh in active:
            if veh.is_obstacle or veh.road != mainlane_edge or veh.lane_pos is None or veh.lane is None:
                continue
            has_avoid = any(op.is_avoidance and op.deadline_pos == self.placed_pos for op in veh.operations)
            if has_avoid:
                self.avoid_ids.add(veh.id)
                # 封鎖車線を離れた最初の step で「障害物までの残距離」を記録（回避LCの完了位置）
                if (
                    veh.id not in self.avoid_leave_remaining
                    and veh.lane != self.obstacle_lane
                    and veh.lane_pos < self.placed_pos
                ):
                    self.avoid_leave_remaining[veh.id] = self.placed_pos - veh.lane_pos
            if veh.lane == self.obstacle_lane and veh.lane_pos < self.placed_pos and veh.speed < QUEUE_SPEED_EPS:
                stopped_pos.append(veh.lane_pos)
        # 障害物から連鎖した停止列の最遠尾端まで（流入端の徐行車など行列と無関係の停止車は数えない）
        tail = self.placed_pos
        for p in sorted(stopped_pos, reverse=True):
            if tail - p > QUEUE_CHAIN_GAP_M:
                break
            tail = p
        self.max_queue_m = max(self.max_queue_m, self.placed_pos - tail)

    def account_vehicle(self, veh: "V2CAV") -> None:
        """1台分の速度サンプル・急減速イベントを窓（t ≥ appear_time）で集計する（退出時と終了時に呼ぶ）。

        speed_history はサンプル i が時刻 ≒ departure_time + i·TIME_STEP の観測。障害物車は摂動側なので除く。
        """
        if veh.is_obstacle or veh.departure_time is None or veh.id in self.counted_ids:
            return
        self.counted_ids.add(veh.id)
        start_idx = max(0, int((self.appear_time - veh.departure_time) / TIME_STEP))
        hist = veh.speed_history
        window = hist[start_idx:]
        self.speed_sum += sum(window)
        self.speed_samples += len(window)
        dv_threshold = HARD_BRAKE_DECEL * TIME_STEP
        for i in range(max(start_idx, 1), len(hist)):
            if hist[i] - hist[i - 1] <= dv_threshold:
                self.hard_brake_events += 1

    def on_exit(self, veh: "V2CAV") -> None:
        """範囲外へ退出した車の通過（スループット）と速度・急減速を計上する。"""
        if veh.arrival_time is not None and veh.arrival_time >= self.appear_time and not veh.is_obstacle:
            self.passage_times.append(veh.arrival_time)
        self.account_vehicle(veh)

    def summary_rows(self, running: "list[V2CAV]", end_time: float) -> dict[str, list[dict[str, Any]]]:
        """終了時に sidecar 3種の行データを返す（suffix -> rows）。running は終了時点の走行中車両。"""
        for veh in running:
            self.account_vehicle(veh)
        stuck_avoid = [
            veh
            for veh in running
            if not veh.is_obstacle
            and any(op.is_avoidance and not op.is_done(veh.lane, veh.lane_pos) for op in veh.operations)
        ]
        window_s = end_time - self.appear_time
        summary = {
            "placed_pos": self.placed_pos,
            "placed_time": self.placed_time,
            "window_start_s": self.appear_time,
            "window_end_s": end_time,
            "throughput_post_vph": round(len(self.passage_times) / window_s * 3600.0, 1) if window_s > 0 else None,
            "passages_post": len(self.passage_times),
            "avg_speed_post_ms": round(self.speed_sum / self.speed_samples, 3) if self.speed_samples else None,
            "max_queue_m": round(self.max_queue_m, 1),
            "hard_brake_events": self.hard_brake_events,
            "avoid_requests": len(self.avoid_ids),
            "avoid_lane_left": len(self.avoid_leave_remaining),
            "avoid_stuck_at_end": len(stuck_avoid),
        }
        avoidance = [
            {"veh_id": vid, "remaining_to_obstacle_m": round(rem, 1)}
            for vid, rem in sorted(self.avoid_leave_remaining.items(), key=lambda kv: int(kv[0]))
        ]
        bins: dict[int, int] = {}
        for t in self.passage_times:
            bins[int((t - self.appear_time) // PASSAGE_BIN_S)] = (
                bins.get(int((t - self.appear_time) // PASSAGE_BIN_S), 0) + 1
            )
        passages = [
            {"bin_start_s": self.appear_time + k * PASSAGE_BIN_S, "arrivals": v} for k, v in sorted(bins.items())
        ]
        return {
            "__obstacle_summary.csv": [summary],
            "__obstacle_avoidance.csv": avoidance,
            "__obstacle_passages.csv": passages,
        }
