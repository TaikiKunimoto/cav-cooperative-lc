"""Layer2: 実行。要求車は目標車線に十分なギャップがあれば（協調不要でも）瞬時LCし、無ければ提供車が協調減速で gap を開ける。

挿入が安全（前後二方向 OK）なら ``changeLane`` で瞬時に1段車線変更する。これは**提供車の有無に依らない**：
目標車線が空（合流先の専用車線・疎な車線など）でも、ギャップが安全なら自力で移れる（provider が居ないと一切動けない
旧挙動の修正）。安全でない要求は次の 4 つで解消する::

    1. 対向の 2 台の順番 … 隣の車線どうしで互いに相手の車線を目指す 2 台（織込みの構造的な組）は、優先度（EDF の鍵）の
       低い方が高い方の後ろへ下がり、締切の位置を高い方に残す（加減速だけ）。高い方が入ったあとで低い方が入る。
       横に重なった 2 台を同じ step に互いの車線へ移す「入れ替え」は、現実の車にできないので使わない。
    2. 提供車の譲り … 後続側の隙間不足で入れなかった要求に、Phase B の割当提供車が挿入判定と同じ必要量まで
       減速だけで（有界に）下がる。前側の不足で入れないときも、要求車が下がる分を提供車の後ろの車の余裕で賄えるなら
       その分まで空ける（YIELD_ON_FRONT）。要求車が止まっていれば、入る場所の手前で HOLD_DECEL で止まれる後続が
       提供車になり（RSU._find_provider）、そこで止まれる速度まで下がる。間の車が通り過ぎたところで要求車が入る。
    3. 要求車のスロット整列 … 前方の隙間が足りなければ前方隣接車より ALIGN_DELTA 遅く走って後ろへずれ、
       後方だけ足りなければ前方余地の範囲で ALIGN_DELTA 速く走って前へずれる（提供車の譲りと対称）。
       「前方と同速に合わせる」は横並びを固定し（lane-drop 終端に並んで到達→相互ブロック）、整列しないと
       進入時に横並びになった車が最後まで入れない。
    4. 締切前の保持 … 締切 D までの実距離が HOLD_MARGIN 以下の要求車に「D で止まれる速度」を上限として与える。

合流ランプから続く車線（加速車線・補助車線）にいて、本線へ移る速度まで加速している途中の要求車
（``LCRequest.accelerating``）は、車線変更せず対向の順番にも加わらない（Layer1 も提供車を割り当てない）。整列と保持は続ける。

速度は ``V2CAV.propose`` の候補として出し、``V2CAV.apply_speed_command`` が追従の候補と合わせて毎 step 1 回だけ指令する
（最も遅い候補が勝つ。前車が近いときは追従の最大減速が、譲り・整列・保持より優先される）。
"""

from dataclasses import dataclass
import math
import os
import sys

from utils.traci_wrapper import (
    get_lane_last_step_veh_ids,
    get_lane_length,
    get_lane_max_speed,
    get_veh_neighbors,
    get_veh_speed,
)
from v2.constants import (
    ALIGN_DECEL,
    ALIGN_DELTA,
    FOLLOW_GAIN,
    FOLLOW_MIN_DECEL,
    FOLLOW_TAU,
    FRONT_YIELD_LOOKBACK,
    HOLD_DECEL,
    HOLD_MARGIN,
    LC_SAFETY_MARGIN,
    MAX_DECEL,
    MIN_GAP,
    OPPOSITE_ORDER,
    STAGE_HOLD,
    TIME_STEP,
    VEH_LENGTH,
    YIELD_COOLDOWN,
    YIELD_DECEL,
    YIELD_DELTA,
    YIELD_MAX_DURATION,
    YIELD_MIN_RATIO,
    YIELD_MIN_SPEED,
    YIELD_ON_FRONT,
)
from v2.layer1.rsu import Assignment
from v2.layer2.safety import Safety
from v2.lc_request import LCRequest
from v2.snapshot import Snapshot
from v2.v2_cav import V2CAV

if "SUMO_HOME" in os.environ:
    tools = os.path.join(os.environ["SUMO_HOME"], "tools")
    sys.path.append(tools)
else:
    sys.exit("please declare environment variable 'SUMO_HOME'")
import traci


@dataclass(frozen=True)
class InsertionCheck:
    """自力挿入の安全判定の結果（目標車線の先行側・後続側それぞれの隙間が足りたか）。"""

    front_ok: bool
    rear_ok: bool
    front_short: float = 0.0  # 先行側の不足量 [m]（必要量 − 実ギャップの最大。足りていれば 0）

    @property
    def ok(self) -> bool:
        return self.front_ok and self.rear_ok


class Layer2:
    """Layer2（実行層）：対向の順番・協調減速・スロット整列で gap を確保し、安全なら要求車を瞬時LCする（状態を持たない静的ロジック）。"""

    @staticmethod
    def execute_pairs(
        assignments: list[Assignment],
        req_by_id: dict[str, LCRequest],
        by_id: dict[str, V2CAV],
        snap: Snapshot,
        committed: dict[int, list[tuple[float, float]]] | None = None,
    ) -> int:
        """各要求車を EDF 順に処理し、目標車線に十分なギャップがあれば瞬時LC、残る要求は
        対向の 2 台の順番 → 提供車の協調減速 ＋ 要求車のスロット整列で解消を図る。実行したLC数を返す。

        要求は EDF 順（``req_by_id`` は鍵昇順の keyed から構築済み）に処理し、より緊急な要求車が先にスロットを
        確定する。前後二方向の安全（``_insertion_safe_live``＝getNeighbors でジャンクション跨ぎに実測）が満たされていれば
        提供車の有無に依らず changeLane する（空車線・疎な車線へも自力で移れる）。
        同一stepに同じ目標車線へ入る車どうしも、挿入判定と同じ安全車間を取る（``_slot_free``。両側から同じ車線へ入る 2 台が
        挿入判定ではお互いを見ないため）。先に確定するのは鍵の上位の車。``committed`` を渡すと、この step の確定を
        呼び出し側と共有する（あとで任意の車線変更が同じ確定を使う）。
        上位の要求車の提供車を兼ねる要求車が同じ step に自力で車線を移ったときは、その提供車には譲りを出さない
        （目標車線から出ていくので、譲っても上位の要求車の隙間は変わらない）。
        """
        provider_of = {a.requester_id: a.provider_id for a in assignments}
        lc_count = 0
        if committed is None:
            committed = {}  # 今stepで確定した target_lane -> (縦位置, 速度) のリスト
        changed: set[str] = set()  # 今stepで車線を移った要求車
        unresolved: list[LCRequest] = []  # 通常挿入が不可だった要求（EDF 順を保持）
        checks: dict[str, InsertionCheck] = {}  # 通常挿入が不可だった要求の、前後どちらの隙間が足りなかったか
        for req in req_by_id.values():  # EDF 順（鍵昇順）
            requester = by_id.get(req.veh_id)
            if requester is None:
                continue
            if requester.road != snap.mainlane_edge or requester.lane != req.current_lane:
                # 要求の鮮度: 調停（Tc）の後に本線を出た／レーンが変わった車の古い要求は実行しない
                # （Tc > step のとき。古い要求の changeLane は車線数の少ない edge で例外になる）
                continue
            target_lane = Safety.next_lane(req)
            check = Layer2._insertion_check_live(requester.id, requester.speed, target_lane < req.current_lane)
            # 加速の途中の合流車は入らない（整列のために判定の結果だけ残す）
            if (
                not req.accelerating
                and check.ok
                and Layer2._slot_free(committed, target_lane, req.current_pos, requester.speed)
            ):
                traci.vehicle.changeLane(requester.id, target_lane, 0)
                committed.setdefault(target_lane, []).append((req.current_pos, requester.speed))
                changed.add(requester.id)
                lc_count += 1
            else:
                unresolved.append(req)
                checks[req.veh_id] = check

        yielding_to: dict[str, frozenset[str]] = {}  # 優先度の高い要求車 -> 自分のために下がる対向の低い要求車
        if OPPOSITE_ORDER > 0:
            yielding_to = Layer2._order_opposite_pairs([r for r in unresolved if not r.accelerating], by_id)

        for req in unresolved:
            requester = by_id[req.veh_id]
            provider_id = provider_of.get(req.veh_id)
            provider = by_id.get(provider_id) if provider_id is not None else None
            if provider is not None and provider.id in changed:
                # 提供車を兼ねる要求車が今stepに自力で車線を移った（もう目標車線にいないので譲らせない）
                provider = None
            check = checks[req.veh_id]
            yield_on_front = YIELD_ON_FRONT > 0 and not check.front_ok
            if provider is not None and yield_on_front and YIELD_ON_FRONT >= 2:
                yield_on_front = Layer2._front_room_from_rear_slack(provider, requester, check.front_short)
            if provider is not None and (not check.rear_ok or yield_on_front):
                # 必要なときだけ譲る: 後続側の隙間不足で入れなかった要求に提供車が下がる。YIELD_ON_FRONT>0 なら前側の不足でも
                # 譲り、要求車が前の車の後ろへ下がる分（前側の不足量）だけ広く空ける
                extra = check.front_short if yield_on_front else 0.0
                Layer2._provider_yield(provider, requester, snap.sim_time, extra)
            # 目標レーンのスロットに自分を縦方向で整列させ、挿入成功率を上げる
            Layer2._requester_align_to_slot(requester, req, check, yielding_to.get(req.veh_id, frozenset()))
            if Layer2._hold_pos(req) - req.current_pos <= HOLD_MARGIN:
                # 締切間近でなお挿入不可: 保持の位置で止まれる速度を上限にして lane-drop 端の急停止を防ぐ（整列は続ける）
                Layer2._hold_before_deadline(requester, req)
        return lc_count

    @staticmethod
    def _slot_free(
        committed: dict[int, list[tuple[float, float]]], target_lane: int, pos: float, speed: float
    ) -> bool:
        """今step、同じ目標車線へ確定した車線変更と、挿入判定と同じ安全車間が取れるか（同時LC衝突を防ぐ）。

        確定した車（縦位置 q・速度 w）は目標車線にいるものとみなし、前にいれば ``Safety.net_required(v, w)``、後ろにいれば
        ``Safety.net_required(w, v)`` を minGap 控除済みの車間に求める（縦位置は前端基準）。両側の車線から同じ車線へ入る
        2 台は、挿入判定（getNeighbors）ではお互いが見えない。位置の重なりだけを見ていたときは、間が 4 m・速度差 8 m/s の
        2 台が同じ step に入って追突した。
        """
        for q, w in committed.get(target_lane, []):
            if q >= pos:
                if (q - VEH_LENGTH) - pos - MIN_GAP < Safety.net_required(speed, w):
                    return False
            elif (pos - VEH_LENGTH) - q - MIN_GAP < Safety.net_required(w, speed):
                return False
        return True

    @staticmethod
    def _hold_pos(req: LCRequest) -> float:
        """要求車が保持で止まる位置。STAGE_HOLD のときは次の 1 段の締切（中間の締切 D − (k − 1)·R）、でなければ締切 D。"""
        return req.stage_deadline if STAGE_HOLD > 0 else req.deadline_pos

    @staticmethod
    def _insertion_check_live(veh_id: str, ego_speed: float, going_right: bool) -> InsertionCheck:
        """目標車線（going_right で左右決定）の実・後続/前走を getNeighbors で取得し、前後二方向の安全ギャップを満たすか判定する。

        snapshot（mainlane_edge 限定）と違い、フィーダーedge・内部ジャンクション車線から流入してくる車も
        getNeighbors がジャンクションを跨いで返すため、入口で流入車を見落とすブラインドスポット衝突を防ぐ。
        dist は minGap 込みの実ギャップ（重なりは負）。

        必要ギャップは **相対制動モデル**で求める（旧実装の ``base=MIN_GAP*0.5`` 固定＋``g_req×(Δv/MAX_SPEED)`` 割引は
        物理的に過小で、混雑＝低速度差ほど必要量が ~1.4m に潰れ危険な隙間挿入を許していた）。両車が最大減速 |MAX_DECEL|
        で止まると仮定し、追突しない最小車間（バンパー間 ≥ minGap + 制動項）を要求する。
        **getNeighbors の dist は minGap 控除済み**（バンパー間 − minGap。重なりは負）なので、
        dist と比較する閾値は制動項のみとする（minGap を足すと二重取りになり、低速・高密度域で
        バンパー間 2×minGap を要求してしまう＝詰まった車列への挿入が構造的に不可能になる）::

            dist ≥ max(0, (v_back² − v_front²) / (2·|MAX_DECEL|))

        - 後続側: v_back=後続速度, v_front=ego 速度（ego が最大減速しても後続が止まれる車間）。
        - 先行側: v_back=ego 速度, v_front=先行速度（ego が先行へ追突しない車間）。
        満たさない側を結果に残す（後続側の不足なら提供車の譲りで、先行側の不足なら要求車の整列で解く）。
        """
        lat = 1 if going_right else 0  # bit1: 左=0 / 右=1
        rear_ok = all(
            dist >= Safety.net_required(get_veh_speed(nid), ego_speed)
            for nid, dist in get_veh_neighbors(veh_id, lat)  # 後続（bit2=0）: ego が止まっても後続が止まれる車間
        )
        front_short = max(
            (
                Safety.net_required(ego_speed, get_veh_speed(nid)) - dist
                for nid, dist in Layer2._front_neighbors(
                    veh_id, going_right, ego_speed
                )  # 先行: ego が先行へ追突しない車間
            ),
            default=0.0,
        )
        return InsertionCheck(front_ok=front_short <= 0.0, rear_ok=rear_ok, front_short=max(front_short, 0.0))

    @staticmethod
    def _front_neighbors(veh_id: str, going_right: bool, ego_speed: float) -> list[tuple[str, float]]:
        """目標車線の先行車を (ID, minGap 控除済みの距離) で返す: getNeighbors の先行に、目標車線の先
        （ジャンクションの内部レーンと次の edge）にいる車を足す（同じ車は近い方の距離を採る）。

        getNeighbors は、今のレーンがルート上で続かない車（合流の加速車線・織り込みの出口側レーンの終端で止まった
        要求車など）に対して、目標車線の先でジャンクションに入った車を返さないことがある。見落とすと、その車の
        すぐ後ろに入って junction collision になる（385 run の衝突 1 件。壁で止まった要求車が、ジャンクションに
        入りかけた先行車の 2.6 m 後ろに入った）。
        """
        lat = 1 if going_right else 0
        fronts = dict(get_veh_neighbors(veh_id, lat | 2))  # 先行（bit2=1）
        for nid, dist in Layer2._leaders_beyond_edge(veh_id, going_right, ego_speed):
            if nid not in fronts or dist < fronts[nid]:
                fronts[nid] = dist
        return list(fronts.items())

    @staticmethod
    def _leaders_beyond_edge(veh_id: str, going_right: bool, ego_speed: float) -> list[tuple[str, float]]:
        """目標車線の先（ルート上で続くジャンクションの内部レーンと次の edge）で最も手前にいる車を、link ごとに 1 台返す。

        距離は getNeighbors と同じく minGap 控除済み（目標車線の残り長 ＋ その車の後端までの位置 − minGap）。
        目標車線の終端までが、どれだけ近い先行車でも判定に効かない距離（停止した先行車に対する必要量 ＋ minGap）より
        遠いときは調べない。
        """
        edge = traci.vehicle.getRoadID(veh_id)
        target_lane = traci.vehicle.getLaneIndex(veh_id) + (-1 if going_right else 1)
        lane_id = f"{edge}_{target_lane}"
        remaining = get_lane_length(lane_id) - traci.vehicle.getLanePosition(veh_id)
        if remaining > Safety.net_required(ego_speed, 0.0) + MIN_GAP:
            return []
        route = set(traci.vehicle.getRoute(veh_id))
        found: list[tuple[str, float]] = []
        for link in traci.lane.getLinks(lane_id):
            approached, via = str(link[0]), str(link[4])
            if approached.rsplit("_", 1)[0] not in route:
                continue  # ルート上で続かない分岐（出口側など）の車は先行車ではない
            via_length = get_lane_length(via) if via else 0.0
            for lane, offset in ((via, remaining), (approached, remaining + via_length)):
                ids = get_lane_last_step_veh_ids(lane) if lane else []
                if not ids:
                    continue
                nid = min(ids, key=lambda v: float(traci.vehicle.getLanePosition(v)))
                gap = (
                    offset + float(traci.vehicle.getLanePosition(nid)) - float(traci.vehicle.getLength(nid)) - MIN_GAP
                )
                found.append((nid, gap))
                break  # この link では最も手前の 1 台だけ
        return found

    # --- 対向の 2 台の順番（優先度の低い方が下がる） ---

    @staticmethod
    def _order_opposite_pairs(unresolved: list[LCRequest], by_id: dict[str, V2CAV]) -> dict[str, frozenset[str]]:
        """隣の車線どうしで互いに相手の車線を目指す 2 台（対向の 2 台）の順番を、優先度（EDF の鍵）で決める（OPPOSITE_ORDER）。

        ``unresolved`` は鍵の昇順（優先度の高い順）。各組で優先度の低い方が ``_stay_behind`` で高い方の後ろへ下がり、
        高い方が入る場所を自分の前に残す。使う操作は加減速だけで車線は変えない（入れ替えのように車体がすり抜けない）。
        戻り値は「優先度の高い要求車 -> そのために下がる低い要求車の集合」（高い方の整列で使う）。
        """
        yielding_to: dict[str, set[str]] = {}
        for j, low in enumerate(unresolved):
            r = by_id.get(low.veh_id)
            if r is None:
                continue
            for high in unresolved[:j]:
                if not Layer2._is_opposite(low, high):
                    continue
                h = by_id.get(high.veh_id)
                if h is not None and Layer2._stay_behind(r, low, h, high):
                    yielding_to.setdefault(high.veh_id, set()).add(low.veh_id)
        return {vid: frozenset(lows) for vid, lows in yielding_to.items()}

    @staticmethod
    def _is_opposite(a: LCRequest, b: LCRequest) -> bool:
        """隣の車線どうしで、互いに相手の今の車線へ移りたい 2 台か。"""
        return Safety.next_lane(a) == b.current_lane and Safety.next_lane(b) == a.current_lane

    @staticmethod
    def _stay_behind(low: V2CAV, low_req: LCRequest, high: V2CAV, high_req: LCRequest) -> bool:
        """優先度の低い車を、優先度の高い車の後ろへ下げる。高い車の挿入を妨げる位置にいれば True を返す。

        締切の位置は高い車に残す: 2 台とも締切まで来ると同じ位置で止まり、前にいた低い車も追いつかれて横に並ぶ。
        そこで締切前の保持の区間では、低い車は締切の 1 台分手前（車長 ＋ minGap ＋ 余裕 2·LC_SAFETY_MARGIN）で
        HOLD_DECEL で止まれる速度を上限にする（締切前の保持と同じ形）。高い車が車線変更を終えれば外れる。

        高い車が低い車の車線へ入るには、低い車がその後ろに ``Safety.net_required(v_低, v_高)``（挿入判定の後続側と
        同じ式）だけ離れているか、前に ``Safety.net_required(v_高, v_低)`` だけ離れている必要がある。後ろ側には
        1 step 分の余裕 LC_SAFETY_MARGIN を足し、止まるときも挿入判定の 0.5 m に余裕 0.5 m を足した車間を残す。

        - 十分前・十分後ろにいる: 何もしない。
        - 後ろにいるが近い: 不足に比例して高い車の速度より遅い目標を取る（追従と同じ形。高い車が締切で止まっても、
          その真横や、高い車が入れない近さでは止まらない）。
        - 横に重なっている（または前寄り）で高い車が走行中: 高い車の速度 − YIELD_DELTA を目標に YIELD_DECEL 以下で
          下がって後ろへ回る（提供車の譲りと同じ則）。
        - 高い車が止まっていて低い車が前寄り: 前へ出て空ける（前が詰まっていれば追従の候補が勝つ）。
        - 高い車が止まっていて低い車が後ろ寄りで重なっている: その場で止まり、それ以上前へ出ない。
        """
        low_pos, high_pos = low_req.current_pos, high_req.current_pos
        high_hold = Layer2._hold_pos(high_req)  # 高い車が保持で止まる位置
        slot = high_hold - (VEH_LENGTH + MIN_GAP + 2 * LC_SAFETY_MARGIN)  # 低い車が止まってよい一番前
        reserved = high_hold - low_pos <= HOLD_MARGIN
        if reserved:
            low.propose("order", math.sqrt(2 * HOLD_DECEL * max(slot - low_pos, 0.0)), abs(MAX_DECEL))
        front_gap = (low_pos - VEH_LENGTH) - high_pos - MIN_GAP  # 高い車の前端から低い車の後端まで（minGap 控除済み）
        if front_gap >= Safety.net_required(high.speed, low.speed):
            return reserved
        rear_gap = (high_pos - VEH_LENGTH) - low_pos - MIN_GAP  # 低い車の前端から高い車の後端まで（minGap 控除済み）
        need = Safety.net_required(low.speed, high.speed) + LC_SAFETY_MARGIN
        if rear_gap >= need:
            return reserved
        if rear_gap >= 0.0:
            target = max(0.0, high.speed + FOLLOW_GAIN * (rear_gap - need))
            room = rear_gap - 2 * LC_SAFETY_MARGIN  # 止まり切ったときに残す車間（挿入判定の 0.5 m ＋ 余裕 0.5 m）
            if low.speed > high.speed:
                decel = (low.speed - high.speed) ** 2 / (2 * room) if room > 0 else abs(MAX_DECEL)
                decel = min(max(decel, FOLLOW_MIN_DECEL), abs(MAX_DECEL))
            else:
                decel = FOLLOW_MIN_DECEL
            low.propose("order", min(target, high.speed), decel)
        elif high.speed >= YIELD_MIN_SPEED:
            low.propose("order", max(high.speed - YIELD_DELTA, 0.0), YIELD_DECEL)
        elif low_pos > high_pos:
            low.propose("order", low.speed + ALIGN_DELTA, ALIGN_DECEL)
        else:
            low.propose("order", 0.0, abs(MAX_DECEL))
        return True

    # --- 提供車の協調減速 ---

    @staticmethod
    def _provider_yield(provider: V2CAV, requester: V2CAV, now: float, extra: float = 0.0) -> None:
        """提供車が要求車の後方に挿入可能な隙間を作る（必要なときだけ・有界に譲る）。

        呼ばれるのは、要求車の自力挿入が「後続側の隙間不足」で落ちたときだけ（先行側の不足や整列で解ける場合は
        譲らない）。必要量は挿入判定と同じ相対制動 ``Safety.net_required(v_提供, v_要求)``（提供車が後続）で測り、
        自分との隙間が既に足りていれば何もしない（足りないのは別の後続＝譲っても解けない）。

        - 要求車が走行中: 「要求車速度 − YIELD_DELTA」（ただし要求車速度の YIELD_MIN_RATIO 以上）を目標に、
          減速度 YIELD_DECEL 以下で下がる。車線内で停止はしない。
        - 要求車が止まっている（< YIELD_MIN_SPEED）: ``_yield_to_stopped``（入る場所の手前で止まれる速度まで下がる）。
        - 同じ走行中の要求車に YIELD_MAX_DURATION 以上譲り続けても入れなければ譲りをやめ、YIELD_COOLDOWN の間は
          提供車に選ばれない（RSU が次の後続を選ぶ）。

        旧実装（不足率に比例して要求車速度の最大 7 割まで減速、前方が詰まれば常にその場で停止、毎 step 無条件に
        呼ぶ）は、譲っている車が動くボトルネックになっていた。指令は候補（``propose``）として出す。
        """
        p = provider
        r = requester
        if r.lane_pos is None or p.lane_pos is None:
            return
        if r.speed < YIELD_MIN_SPEED:
            Layer2._yield_to_stopped(p, r)
            return
        if p.yield_for != r.id:
            p.yield_for = r.id
            p.yield_time = 0.0
        if p.yield_time >= YIELD_MAX_DURATION:
            p.yield_cooldown_until = now + YIELD_COOLDOWN  # 交代: 次の Tc から別の後続が提供車になる
            p.yield_for = None
            p.yield_time = 0.0
            return
        # 控除済みギャップ（要求車後端 − 提供車前端 − minGap。snapshot の縦位置は前端基準）
        current_net = (r.lane_pos - VEH_LENGTH - p.lane_pos) - MIN_GAP
        if current_net >= Safety.net_required(p.speed, r.speed) + extra:
            return  # 自分との隙間は足りている（extra: 要求車が前側の不足で後ろへ下がる分）
        p.yield_time += TIME_STEP
        target = max(r.speed - YIELD_DELTA, YIELD_MIN_RATIO * r.speed, 0.0)
        p.propose("yield", target, YIELD_DECEL)

    @staticmethod
    def _yield_to_stopped(provider: V2CAV, requester: V2CAV) -> None:
        """止まった要求車を入れる: 提供車は、入る場所の手前（``Safety.stop_pos_behind``）で HOLD_DECEL の減速で止まれる速度を
        上限にして走る（締切前の保持と同じ式）。間の車が通り過ぎて隙間ができたら、要求車が挿入判定どおりに入る。

        提供車は RSU が「そこで HOLD_DECEL で止まれる最も近い後続」として選ぶので、上限に達してからの減速は HOLD_DECEL 以下で
        済む。新たに割り当てられた車が既に止まれない速さなら譲らず通り過ぎる（優先度なしの割当で最近傍が選ばれた場合など）。
        譲りは入る場所の手前で止まるところまでなので、走行中の要求車への譲りの時間上限（YIELD_MAX_DURATION）は当てない。
        """
        if requester.lane_pos is None or provider.lane_pos is None:
            return
        v_cap = Safety.stop_speed(Safety.stop_pos_behind(requester.lane_pos) - provider.lane_pos)
        if provider.yield_for != requester.id:
            if provider.speed > v_cap:
                return  # 止まれない: 通り過ぎる（次の Tc で RSU がその後ろの車を選ぶ）
            provider.yield_for = requester.id
            provider.yield_time = 0.0
        provider.propose("yield", v_cap, abs(MAX_DECEL))

    @staticmethod
    def _front_room_from_rear_slack(provider: V2CAV, requester: V2CAV, front_short: float) -> bool:
        """前側の不足分まで空ける譲り（YIELD_ON_FRONT=2）を、提供車の後ろの車の余裕で賄えるか。

        提供車が下がって要求車との間に空ける量（必要量 ＋ 前側の不足量 − 今の隙間）を、提供車の後続が
        追従の安全車間（``V2CAV.control_speed`` と同じ式）を超えて持っている余裕から出せるときだけ True。
        後ろが詰まっていれば前側の分は空けない（後続側の不足のときだけ譲る規則に戻る）。後続がいなければ True。
        getFollower の距離は後続の minGap 控除済み（getLeader と同じ）。
        """
        if requester.lane_pos is None or provider.lane_pos is None:
            return False
        current_net = (requester.lane_pos - VEH_LENGTH - provider.lane_pos) - MIN_GAP
        need = Safety.net_required(provider.speed, requester.speed) + front_short - current_net
        if need <= 0:
            return True  # 既に足りている（_provider_yield は何もしない）
        follower = traci.vehicle.getFollower(provider.id, FRONT_YIELD_LOOKBACK)
        if not follower or not follower[0]:
            return True
        follower_id, gap = follower
        v_f = get_veh_speed(follower_id)
        safety_gap = max(Safety.net_required(v_f, provider.speed), v_f * FOLLOW_TAU) + MIN_GAP
        return bool(gap - safety_gap >= need)

    @staticmethod
    def _hold_before_deadline(requester: V2CAV, req: LCRequest) -> None:
        """挿入できていない要求車に、保持の位置（締切 D。STAGE_HOLD のときは次の 1 段の締切）で HOLD_DECEL の減速で
        止まれる速度 √(2·HOLD_DECEL·残距離) を上限として与える。

        分岐直前まで巡航して SUMO トポロジー（teleport 無効の lane-drop）で急停止するのを避ける挙動品質（F5）。
        旧実装の「D で停止する減速で目標 0 を指令」は、100 m 手前から全車が止まる計画で減速して流れを落とし、
        減速した要求車と後続の速度差が開いて後続側の必要車間が広がる（かえって入れなくなる）副作用があった。
        上限なので、整列・挿入はそのまま続く。候補として出すので前車が近ければ追従の最大減速が勝つ。
        2 回以上移る車が中間の締切を越えてしまったときは、そこで止まる（上限 0）。
        """
        remaining = (
            Layer2._hold_pos(req) - req.current_pos
        )  # 保持の位置までの残距離（要求は road==mainlane の同一フレームで生成）
        if remaining <= 0:
            if req.remaining_k >= 2 and STAGE_HOLD > 0:
                requester.propose("hold", 0.0, abs(MAX_DECEL))  # 中間の締切を越えた多段の車
            return  # 既に D を越えていれば SUMO トポロジーに任せる
        v_hold = math.sqrt(2 * HOLD_DECEL * remaining)
        requester.propose("hold", v_hold, abs(MAX_DECEL))

    @staticmethod
    def _requester_align_to_slot(
        requester: V2CAV, req: LCRequest, check: InsertionCheck, yielding: frozenset[str] = frozenset()
    ) -> None:
        """要求車自身を目標レーンのスロットに縦方向で整列させる（合流のための自己速度調整）。

        自力挿入の判定でどちら側が足りなかったかで挙動を分ける::

            前方が足りない … 足りない前方隣接車（対向要求車か通常の流れかを問わない）のうち最も遅いものより
                ALIGN_DELTA 遅く走って相対的に後ろへずれる。同速で並走すると横並びが固定され、進入時に横並びに
                なった車は最後まで入れない（lane-drop 終端に並んで到達→相互ブロック）。
            後方だけ足りない … 前方余地と制限速度の範囲で ALIGN_DELTA 速く走って前へずれる（提供車の譲りと対称。
                後続より速くなれば相対制動の必要量も縮む）。

        減速度は ALIGN_DECEL 以下（快適減速）。候補として出すので own leader が近ければ追従の最大減速が勝つ。
        ``yielding`` は、この要求車のために下がる優先度の低い対向の要求車（OPPOSITE_ORDER）。前を塞いでいても
        相手が下がるので、それに合わせて自分は下がらない（両方が下がり合うと 2 台そろって減速し続ける）。
        """
        if requester.speed <= 0:
            return
        going_right = Safety.next_lane(req) < req.current_lane
        fronts = [
            (nid, dist, get_veh_speed(nid))
            for nid, dist in Layer2._front_neighbors(requester.id, going_right, requester.speed)
        ]
        if not check.front_ok:
            blocking = [
                v for nid, dist, v in fronts if nid not in yielding and dist < Safety.net_required(requester.speed, v)
            ]
            if blocking:
                requester.propose("align", max(min(blocking) - ALIGN_DELTA, 0.0), ALIGN_DECEL)
            return
        if not check.rear_ok and requester.lane_id is not None:
            target = min(requester.speed + ALIGN_DELTA, get_lane_max_speed(requester.lane_id))
            if fronts:
                target = min(target, min(v for _, _, v in fronts) + ALIGN_DELTA)
            if target > requester.speed:
                requester.propose("align", target, ALIGN_DECEL)
