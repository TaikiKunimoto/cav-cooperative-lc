"""Layer1（RSU）の Phase B: 鍵順の提供車割当。

鍵（Phase A の EDF 順）だけではデッドロックフリーは出ない。鍵 ＋「順序通りの割当」で初めて出る
（コア機構_決定事項.md §5）。鍵順（dist 昇順＝最も緊急から）に処理し、各要求車に対し
「次の1段LCの目標車線の後続のうち、鍵が自分より下位（劣位）かつ今Tc未占有 の最近傍」を提供車に確保する。

- 占有印（claimed）で同一提供車の二重割当を防ぐ＝**横取り禁止**。
- 既に上位車の提供車として確保された要求車は、今Tc は譲る側に回り自分のLCは見送る（譲歩の伝播・§6）。
- 要求車が止まっている（YIELD_MIN_SPEED 未満）ときは、入る場所の手前で HOLD_DECEL で止まれる最も近い後続を選ぶ
  （それより近い車はそのまま通り過ぎる。最高速度なら約 155 m 手前）。選ばれた車がそこで止まれる速度まで下がり、
  間の車が通り過ぎたところで要求車が入る（Layer2._yield_to_stopped）。
- 本線へ移る速度まで加速している途中の合流車（``LCRequest.accelerating``）には、まだ提供車を割り当てない
  （鍵は他の要求車の提供車選びに使う）。
"""

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from status.status import CarAction, CarStatus
from v2.constants import COOP_YIELD, MAX_DECEL, SWAP_WINDOW, YIELD_MIN_SPEED
from v2.layer1.priority import Key, KeyedRequest
from v2.layer2.safety import Safety
from v2.lc_request import LCRequest
from v2.snapshot import Snapshot

if TYPE_CHECKING:
    from v2.v2_cav import V2CAV


class Assignment(BaseModel):
    """1組の割当（要求車 ← 提供車）。"""

    model_config = ConfigDict(frozen=True)

    requester_id: str
    provider_id: str


class RSU:
    """Layer1（路側機）：Phase B の鍵順 提供車割当を行う（状態を持たない静的ロジック）。"""

    @staticmethod
    def arbitrate(keyed: list[KeyedRequest], snap: Snapshot) -> list[Assignment]:
        """Phase B。鍵昇順（dist小から）に提供車を占有印つきで確保し、割当のリストを返す。"""
        if COOP_YIELD <= 0:
            return []  # 要素の除去: 提供車を割り当てない（要求車は自力挿入・スワップ・整列・保持のみ）
        request_key: dict[str, Key] = {req.veh_id: key for key, req in keyed}
        request_by_id: dict[str, LCRequest] = {req.veh_id: req for _, req in keyed}
        claimed: set[str] = set()
        assignments: list[Assignment] = []
        for key, req in keyed:
            if req.veh_id in claimed:
                # 既に上位車の提供車として確保済み → 今Tc は譲る側。自分のLCは見送る（譲歩の伝播）
                continue
            if req.accelerating:
                continue  # 加速の途中の合流車は、まだ隙間を要さない（加速しきってから割り当てる）
            provider = RSU._find_provider(req, key, snap, claimed, request_key, request_by_id)
            if provider is not None:
                claimed.add(provider)  # 占有印（横取り禁止）
                assignments.append(Assignment(requester_id=req.veh_id, provider_id=provider))
            # provider が無い＝譲れる枠なし（今Tc は割当なし。次Tc 再試行）
        return assignments

    @staticmethod
    def arbitrate_fcfs(keyed: list[KeyedRequest], snap: Snapshot) -> list[Assignment]:
        """優先度なし（``--policy none``）の Phase B: 発生順に、目標車線の最近傍後続をそのまま提供車にする。

        提案手法の調停要素をすべて外した素朴な協調ペア形成（アブレーション比較の対照条件）::

            - 鍵劣位判定なし … 相手が自分より緊急でも譲らせる
            - 占有印（claimed）なし … 同一提供車の二重割当（横取り）を許す
            - 譲歩の伝播なし … 提供車に確保された要求車も自分のLCを見送らない（displacement なし）
            - 対向スワップ相手の除外なし … 織込みの構造的ペアも最近傍なら提供車に選ぶ
            - 停車中の2番目選択なし … 常に最近傍

        障害物（停止車両）だけは除外する（gap を物理的に作れないため。優先度機構とは無関係）。加速の途中の合流車にも
        割り当てない（合流の速度は車の動きの前提で、優先度機構とは無関係）。
        """
        if COOP_YIELD <= 0:
            return []
        assignments: list[Assignment] = []
        for _, req in keyed:
            if req.accelerating:
                continue
            step = 1 if req.direction == CarAction.CHANGE_LEFT else -1
            members = snap.lane_members.get(f"{snap.mainlane_edge}_{req.current_lane + step}", [])  # 縦位置降順
            for vid in members:
                o = snap.obs[vid]
                if o.lane_pos is None or o.lane_pos >= req.current_pos:
                    continue  # 後続（自分より後ろ）のみ
                if o.is_obstacle:
                    continue  # 障害物（停止車両）は gap を作れないので提供車にしない
                assignments.append(Assignment(requester_id=req.veh_id, provider_id=vid))
                break  # 縦位置降順の最初の後続＝最近傍
        return assignments

    @staticmethod
    def _is_swap_partner(req: LCRequest, other: LCRequest) -> bool:
        """互いに相手のレーンを目指し、縦位置が SWAP_WINDOW 内に重なる対向要求車か。

        対向スワップ相手を提供車にすると、要求車と提供車の速度が結合して重なりが固定され、
        lane-drop 終端まで並走→相互ブロックの永久デッドロックに陥る（織込み環境のグリッドロック起点）。
        このペアは Layer2 の対向スワップで解消するため、提供車候補から除外する。
        """
        other_step = 1 if other.direction == CarAction.CHANGE_LEFT else -1
        return (
            other.current_lane + other_step == req.current_lane
            and abs(other.current_pos - req.current_pos) <= SWAP_WINDOW
        )

    @staticmethod
    def _find_provider(
        req: LCRequest,
        my_key: Key,
        snap: Snapshot,
        claimed: set[str],
        request_key: dict[str, Key],
        request_by_id: dict[str, LCRequest],
    ) -> str | None:
        """次の1段LCの目標車線の後続から、鍵劣位かつ未占有の最近傍を選ぶ。

        要求車が止まっているときは、入る場所の手前（``Safety.stop_pos_behind``）で HOLD_DECEL で止まれる最も近い後続を選ぶ。
        """
        step = 1 if req.direction == CarAction.CHANGE_LEFT else -1
        next_lane = req.current_lane + step
        members = snap.lane_members.get(f"{snap.mainlane_edge}_{next_lane}", [])  # 縦位置降順

        viable: list[str] = []
        for vid in members:
            o = snap.obs[vid]
            if o.lane_pos is None or o.lane_pos >= req.current_pos:
                continue  # 後続（自分より後ろ）のみ
            if o.is_obstacle:
                continue  # 障害物（停止車両）は gap を作れないので提供車にしない
            if o.yield_cooldown_until > snap.sim_time:
                continue  # 譲りの時間上限を超えて交代した直後（次の後続に回す）
            if vid in claimed:
                continue  # 占有印（横取り禁止）
            other_key = request_key.get(vid)
            if other_key is not None and other_key < my_key:
                continue  # 相手が自分より緊急（鍵上位）→ 譲ってもらえない。要求なし車は常に譲れる
            other_req = request_by_id.get(vid)
            if other_req is not None and RSU._is_swap_partner(req, other_req):
                continue  # 対向スワップ相手（Layer2 で交換により解消）は提供車にしない
            viable.append(vid)

        if not viable:
            return None
        # members は縦位置降順なので viable も降順（先頭＝最近傍）
        if snap.obs[req.veh_id].speed >= YIELD_MIN_SPEED:
            return viable[0]
        stop_pos = Safety.stop_pos_behind(req.current_pos)
        for vid in viable:
            o = snap.obs[vid]
            # 今この要求車に譲っている車は、最大減速で止まれるうちは続ける。上限の速度で近づく車は 1 step 分だけ
            # HOLD_DECEL の曲線を上回って見えるので、毎回選び直すと役が入れ替わり、外れた車が加速して通り過ぎる
            if o.yield_for == req.veh_id and o.lane_pos is not None:
                if o.speed <= Safety.stop_speed(stop_pos - o.lane_pos, abs(MAX_DECEL)):
                    return vid
        for vid in viable:
            o = snap.obs[vid]
            if o.lane_pos is not None and o.speed <= Safety.stop_speed(stop_pos - o.lane_pos):
                return vid  # 入る場所の手前で HOLD_DECEL で止まれる最も近い後続
        return None

    @staticmethod
    def apply_roles(active: "list[V2CAV]", assignments: list[Assignment]) -> None:
        """毎Tc フル再構築: 全車の役割を NORMAL にリセットしてから割当結果を反映する。"""
        by_id = {veh.id: veh for veh in active}
        for veh in active:
            veh.status = CarStatus.NORMAL
            veh.providing_to_id = None
            veh.receiving_from_id = None
        for a in assignments:
            requester = by_id.get(a.requester_id)
            provider = by_id.get(a.provider_id)
            if requester is None or provider is None:
                continue
            requester.status = CarStatus.LANE_CHANGING
            requester.receiving_from_id = a.provider_id
            provider.status = CarStatus.YIELDING
            provider.providing_to_id = a.requester_id
        for veh in active:
            if veh.status is not CarStatus.YIELDING:
                veh.yield_for = None  # 提供車でなくなったら譲りの継続時間をリセット
                veh.yield_time = 0.0

    @staticmethod
    def keys_unique(keyed: list[KeyedRequest]) -> bool:
        """鍵がすべて相異なるか（=同点なし）。ID が一意なので常に True のはず（デッドロックフリー）。"""
        keys = [k for k, _ in keyed]
        return len(set(keys)) == len(keys)

    @staticmethod
    def providers_unique(assignments: list[Assignment]) -> bool:
        """同一提供車が複数の要求車に割り当たっていないか（横取り禁止なら常に True）。"""
        providers = [a.provider_id for a in assignments]
        return len(set(providers)) == len(providers)

    @staticmethod
    def log_assignments(sim_time: float, keyed: list[KeyedRequest], assignments: list[Assignment]) -> None:
        """Phase A/B の結果（EDF順とどの要求車が提供車を得たか）をログ出力する（B4 の検証用）。"""
        amap = {a.requester_id: a.provider_id for a in assignments}
        print(f"[Tc t={sim_time:.1f}] requests={len(keyed)} assigned={len(assignments)}")
        for key, r in keyed:
            print(f"    veh={r.veh_id} dist={key[0]:.1f} k={r.remaining_k} <- provider={amap.get(r.veh_id, '-')}")
