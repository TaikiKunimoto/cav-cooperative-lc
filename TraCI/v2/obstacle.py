"""突発障害物（Obstacle）: 走行中の1台を停止させてブロック車両＝障害物にするシナリオイベント。

形状（Environment）非依存で、どの環境にも ``--obstacle 'lane,pos,time'`` で動的に付与できる。
配置（place）は「位置到達トリガ」: 指定レーンで、OBSTACLE_DECEL で減速すれば pos で止まれる地点に来た最初の車を
障害物にし、減速させて pos で停車させる（lane も pos も指定どおりに再現する。1 step で止めると直後の車が物理的に止まれない）。
配置後は escalate で、障害物より後方・同一レーンの through 車に隣レーンへの回避（必須LC）を動的付与する。
"""

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from v2.constants import OBSTACLE_DECEL
from v2.lc_request import LCOperation

if TYPE_CHECKING:
    from v2.v2_cav import V2CAV


class Obstacle(BaseModel):
    """突発障害物のパラメータと振る舞い（パース・検証・配置・エスカレーション）。"""

    model_config = ConfigDict(frozen=True)

    lane: int  # 障害物を発生させる本線レーン index（0=最下段）
    pos: float  # 障害物の位置 [m]（mainlane_edge 始端からの距離）
    appear_time: (
        float  # 突発時刻 [s]（この時刻以降、指定レーンで pos に止まれる地点に来た最初の車を減速させて障害物化）
    )

    @classmethod
    def from_spec(cls, spec: str) -> "Obstacle":
        """``'lane,pos,time'`` をパースする。要素数・数値が不正なら受け取った値つきで ValueError を投げる。"""
        parts = spec.split(",")
        if len(parts) != 3:
            raise ValueError(f"--obstacle は 'lane,pos,time' の3値で指定してください（受け取り: {spec!r}）")
        lane_s, pos_s, time_s = parts
        try:
            return cls(lane=int(lane_s), pos=float(pos_s), appear_time=float(time_s))
        except ValueError as e:
            raise ValueError(f"--obstacle の数値変換に失敗（lane=整数, pos/time=実数）: {spec!r}") from e

    def validate_for(self, mainlane_edge: str, num_lanes: int, mainlane_length: float) -> None:
        """環境に対してレーンindex・位置の範囲を検証する。範囲外なら受け取った値つきで ValueError を投げる。

        （メソッド名 validate は pydantic BaseModel の予約と紛れるため validate_for とする）
        """
        if not 0 <= self.lane < num_lanes:
            raise ValueError(
                f"--obstacle のレーン番号が範囲外です（{mainlane_edge} のレーン数={num_lanes}, "
                f"指定可能 0..{num_lanes - 1}, 受け取り: {self.lane}）"
            )
        if not 0.0 < self.pos < mainlane_length:
            raise ValueError(
                f"--obstacle の位置が範囲外です（{mainlane_edge} の長さ={mainlane_length}m, "
                f"指定可能 0..{mainlane_length}, 受け取り: {self.pos}）"
            )

    @staticmethod
    def stop_distance(speed: float) -> float:
        """OBSTACLE_DECEL で減速して止まるまでの距離 [m]。"""
        return speed**2 / (2 * OBSTACLE_DECEL)

    def place(self, active: "list[V2CAV]", edge: str, watched_id: str | None) -> tuple[str | None, float | None]:
        """指定レーンで pos に止まれる地点に来た最初の車を減速させ、pos で停車させて障害物にする（位置到達トリガ）。

        2 段階で進む::

            (1) pos の手前で「OBSTACLE_DECEL で減速すれば pos で止まれる」先頭車を 1 台監視し、その地点
                （lane_pos + 停止距離 ≥ pos）に来たら障害物にして減速を始める（調停から外し、車線変更を止める）。
            (2) 止まるまで毎 step 減速させ（非協調の車はここで速度を下げる。提案の車は control_speed の候補が
                同じ減速を入れ、前が詰まっていれば追従の安全側が勝つ）、止まったら停止を固定して配置完了とする。

        回避操作の付与（escalate）は配置完了（停車）から始まる。監視車が手前でレーンを抜けた／退出した場合は選び直す。
        戻り値: (監視中の車ID, 停止位置)。停車し終えていなければ停止位置は None。
        """
        by_id = {v.id: v for v in active}
        watched = by_id.get(watched_id) if watched_id is not None else None

        # (2) 停車中: 止まったら配置完了。止まるまでは減速させる
        if watched is not None and watched.is_obstacle and watched.obstacle_stop_pos is not None:
            if watched.speed <= 0.0 and watched.lane_pos is not None:
                watched.make_obstacle()
                placed = watched.lane_pos
                print(f"[obstacle] veh={watched.id} edge={edge} lane={self.lane} pos={placed:.1f}")
                return watched_id, placed
            watched.brake_as_obstacle()
            return watched_id, None

        # 指定 edge・指定レーンを走行中の CAV（障害物化済みは除く）。lane_pos でアクセスするので None は除外
        in_lane = [
            v
            for v in active
            if v.road == edge and v.lane == self.lane and v.lane_pos is not None and not v.is_obstacle
        ]
        in_lane_ids = {v.id for v in in_lane}

        # (1) 監視中の車が pos で止まれる地点に来ていたら、障害物にして減速を始める
        if watched is not None and watched.id in in_lane_ids and watched.lane_pos is not None:
            if watched.lane_pos + self.stop_distance(watched.speed) >= self.pos:
                watched.start_obstacle_stop(self.pos)
                watched.brake_as_obstacle()
                print(
                    f"[obstacle] veh={watched.id} starts stopping at pos={watched.lane_pos:.1f}"
                    f" speed={watched.speed:.1f} (stop at {self.pos:.1f})"
                )
                return watched_id, None

        # 監視対象を（再）選定: まだ pos の手前で止まれる車のうち最も前の車。1台もいなければ未選定のまま待機する
        approaching = [
            v for v in in_lane if v.lane_pos is not None and v.lane_pos + self.stop_distance(v.speed) < self.pos
        ]
        if approaching:
            lead = max(approaching, key=lambda v: (v.lane_pos or 0.0, -int(v.id)))
            return lead.id, None
        return None, None

    def escalate(
        self,
        active: "list[V2CAV]",
        edge: str,
        obstacle_pos: float,
        num_lanes: int,
        through_lanes: tuple[int, ...],
        return_deadline: float,
    ) -> None:
        """障害物より後方・同一レーンの車に、隣レーンへの回避操作（is_avoidance）を append する（through も既存MLC車も）。

        回避先は「最終目的地に近い方向」の隣レーン、不可なら逆側へ一旦退避。本来の目標が無い車（through）と最終目的地が
        障害物レーンの車は、本線へ続くレーン（through_lanes）を優先して選ぶ。元の必須LC操作は上書きせず保持され、
        回避操作（deadline=obstacle_pos）が deadline 最短で先に処理される。回避操作は「障害物を通過したら完了」なので
        退避レーンに保持され、通過後に元の操作が再アクティブになって最終目的地へ復帰する。
        through 車が本線へ続かないレーン（出口車線・加速車線）へしか退避できなかったときは、通過後に本線へ戻る操作
        （is_return、締切＝return_deadline＝本線 edge の終端）を併せて付与する（戻る操作が無いと、退避先のレーンが
        消える環境で終端に取り残される）。毎step呼ばれるため、付与済みの車は skip（重複防止）。
        """
        for veh in active:
            if veh.is_obstacle or veh.road != edge or veh.lane != self.lane or veh.lane_pos is None:
                continue
            if veh.lane_pos >= obstacle_pos:
                continue  # 障害物より前（先）
            if any(op.is_avoidance and op.deadline_pos == obstacle_pos for op in veh.operations):
                continue  # この障害物の回避操作は付与済み
            avoid_lane = self._avoid_lane(veh, num_lanes, through_lanes)
            if avoid_lane is None:
                continue  # 両隣とも範囲外（＝退避先なし）＝待機
            veh.operations.append(LCOperation(target_lane=avoid_lane, deadline_pos=obstacle_pos, is_avoidance=True))
            has_goal = any(not op.is_avoidance and not op.is_return for op in veh.operations)
            if not has_goal and avoid_lane not in through_lanes and not any(op.is_return for op in veh.operations):
                back_lane = min(through_lanes, key=lambda lane: (abs(lane - avoid_lane), lane))
                veh.operations.append(LCOperation(target_lane=back_lane, deadline_pos=return_deadline, is_return=True))

    def _avoid_lane(self, veh: "V2CAV", num_lanes: int, through_lanes: tuple[int, ...]) -> int | None:
        """障害物レーンからの退避先隣レーン。最終目的地側を優先し、不可なら逆側へ（両隣とも範囲外なら None）。

        レーン index と左右（SUMO 慣習: index 0 が右端、index が増えるほど左。CHANGE_LEFT が index 増加方向）::

            進行方向＝紙面奥
                  左 (CHANGE_LEFT, index+1)
            ┌────────┐
            │ lane N-1 │   index 大 = 左端
            │   ...    │
            │ lane 1   │
            │ lane 0   │   index 0  = 右端（最下段）
            └────────┘
                  右 (CHANGE_RIGHT, index-1)

        したがって  right = lane-1（index 小・右） / left = lane+1（index 大・左）。
        """
        right = self.lane - 1
        left = self.lane + 1
        right_ok = right >= 0
        left_ok = left < num_lanes
        # 最終目的地 = 本来の目標（非回避・非復帰）操作のうち最も deadline が遠い target_lane（無ければ through 車）
        goals = [op for op in veh.operations if not op.is_avoidance and not op.is_return]
        final_target = max(goals, key=lambda op: op.deadline_pos).target_lane if goals else None
        if final_target is not None and final_target > self.lane:
            prefer, prefer_ok, other, other_ok = left, left_ok, right, right_ok  # 目的地が左（上）
        elif final_target is not None and final_target < self.lane:
            prefer, prefer_ok, other, other_ok = right, right_ok, left, left_ok  # 目的地が右（下）
        else:
            # through 車 or 最終目的地＝障害物レーン: 本線へ続くレーンを優先。候補が 2 つなら id 偶奇、1 つならそちら
            candidates = [lane for lane, ok in ((right, right_ok), (left, left_ok)) if ok]
            preferred = [lane for lane in candidates if lane in through_lanes] or candidates
            if not preferred:
                return None
            if len(preferred) == 1:
                return preferred[0]
            return preferred[0] if int(veh.id) % 2 == 0 else preferred[1]
        if prefer_ok:
            return prefer
        return other if other_ok else None  # 本来の方向が不可なら逆側へ一旦退避
