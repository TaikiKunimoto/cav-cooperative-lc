"""Layer2: 実行。要求車は目標車線に十分なギャップがあれば（協調不要でも）瞬時LCし、無ければ提供車が協調減速で gap を開ける。

挿入が安全（前後二方向 OK）なら ``changeLane`` で瞬時に1段車線変更する。これは**提供車の有無に依らない**：
目標車線が空（合流先の専用車線・疎な車線など）でも、ギャップが安全なら自力で移れる（provider が居ないと一切動けない
旧挙動の修正）。安全でない要求は3段構えで解消する::

    1. 対向スワップ … 隣接レーンで互いのレーンを目指し重なった2要求車（織込みの構造的ペア）は、
       どちらも通常挿入が幾何学的に不可能。同一stepの相互 changeLane で原子的に交換する。
    2. 提供車の譲り … 後続側の隙間不足で入れなかった要求にだけ、Phase B の割当提供車が挿入判定と同じ必要量まで
       減速だけで（有界に）下がる。要求車が停止していれば通過して後方を空けるか、締切直前に限り停止して守る。
    3. 要求車のスロット整列 … 前方の隙間が足りなければ前方隣接車より ALIGN_DELTA 遅く走って後ろへずれ、
       後方だけ足りなければ前方余地の範囲で ALIGN_DELTA 速く走って前へずれる（提供車の譲りと対称）。
       「前方と同速に合わせる」は横並びを固定し（lane-drop 終端に並んで到達→相互ブロック）、整列しないと
       進入時に横並びになった車が最後まで入れない。
    4. 締切前の保持 … 締切 D までの実距離が HOLD_MARGIN 以下の要求車に「D で止まれる速度」を上限として与える。

合流ランプから続く車線（加速車線・補助車線）にいて、本線へ移る速度まで加速している途中の要求車
（``LCRequest.accelerating``）は、挿入もスワップもしない（Layer1 も提供車を割り当てない）。整列と保持は続ける。

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
    HOLD_DECEL,
    HOLD_MARGIN,
    MAX_DECEL,
    MIN_GAP,
    SWAP_STOPPED_EPS,
    SWAP_WINDOW,
    TIME_STEP,
    VEH_LENGTH,
    YIELD_COOLDOWN,
    YIELD_DECEL,
    YIELD_DELTA,
    YIELD_MAX_DURATION,
    YIELD_MIN_RATIO,
    YIELD_MIN_SPEED,
)
from v2.layer1.rsu import Assignment
from v2.layer2.safety import Safety
from v2.lc_request import LCRequest
from v2.snapshot import Snapshot, VehObs
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

    @property
    def ok(self) -> bool:
        return self.front_ok and self.rear_ok


class Layer2:
    """Layer2（実行層）：スワップ・協調減速・スロット整列で gap を確保し、安全なら要求車を瞬時LCする（状態を持たない静的ロジック）。"""

    @staticmethod
    def execute_pairs(
        assignments: list[Assignment],
        req_by_id: dict[str, LCRequest],
        by_id: dict[str, V2CAV],
        snap: Snapshot,
    ) -> int:
        """各要求車を EDF 順に処理し、目標車線に十分なギャップがあれば瞬時LC、残る要求は
        対向スワップ → 提供車の協調減速 ＋ 要求車のスロット整列で解消を図る。実行したLC数を返す。

        要求は EDF 順（``req_by_id`` は鍵昇順の keyed から構築済み）に処理し、より緊急な要求車が先にスロットを
        確定する。前後二方向の安全（``_insertion_safe_live``＝getNeighbors でジャンクション跨ぎに実測）が満たされていれば
        提供車の有無に依らず changeLane する（空車線・疎な車線へも自力で移れる）。
        同一stepで同じ目標車線の重なる位置への二重挿入は ``committed`` で弾く（custom_cav の同一step同一レーンチェックに相当）。
        """
        provider_of = {a.requester_id: a.provider_id for a in assignments}
        lc_count = 0
        committed: dict[int, list[float]] = {}  # 今stepで確定した target_lane -> 縦位置のリスト
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
            if not req.accelerating and check.ok and Layer2._slot_free(committed, target_lane, req.current_pos):
                traci.vehicle.changeLane(requester.id, target_lane, 0)
                committed.setdefault(target_lane, []).append(req.current_pos)
                lc_count += 1
            else:
                unresolved.append(req)
                checks[req.veh_id] = check

        swapped = Layer2._execute_swaps([r for r in unresolved if not r.accelerating], snap, committed)
        lc_count += len(swapped)

        for req in unresolved:
            if req.veh_id in swapped:
                continue
            requester = by_id[req.veh_id]
            provider_id = provider_of.get(req.veh_id)
            provider = by_id.get(provider_id) if provider_id is not None else None
            check = checks[req.veh_id]
            if provider is not None and not check.rear_ok:
                # 必要なときだけ譲る: 後続側の隙間不足で入れなかった要求にだけ提供車が下がる
                Layer2._provider_yield(provider, requester, req, snap.sim_time)
            # 目標レーンのスロットに自分を縦方向で整列させ、挿入成功率を上げる
            Layer2._requester_align_to_slot(requester, req, check)
            if req.deadline_pos - req.current_pos <= HOLD_MARGIN:
                # 締切間近でなお挿入不可: D で止まれる速度を上限にして lane-drop 端の急停止を防ぐ（整列は続ける）
                Layer2._hold_before_deadline(requester, req)
        return lc_count

    @staticmethod
    def _slot_free(committed: dict[int, list[float]], target_lane: int, pos: float) -> bool:
        """今step、同じ目標車線で重なる位置への挿入が既に確定していないか（同時LC衝突を防ぐ）。"""
        check_range = VEH_LENGTH + MIN_GAP
        return all(abs(p - pos) >= check_range for p in committed.get(target_lane, []))

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
        front_ok = all(
            dist >= Safety.net_required(ego_speed, get_veh_speed(nid))
            for nid, dist in Layer2._front_neighbors(
                veh_id, going_right, ego_speed
            )  # 先行: ego が先行へ追突しない車間
        )
        return InsertionCheck(front_ok=front_ok, rear_ok=rear_ok)

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

    # --- 対向スワップ（織込みデッドロックの解消）---

    @staticmethod
    def _execute_swaps(unresolved: list[LCRequest], snap: Snapshot, committed: dict[int, list[float]]) -> set[str]:
        """互いに相手のレーンを目指し縦位置が重なった2要求車を、同一stepの相互 changeLane で交換する。

        織込み環境では合流（上へ）と分流（下へ）の要求車が隣接レーンで対向し、縦位置が
        SWAP_WINDOW 内に重なると前後どちらの安全ギャップも取れず、通常挿入・協調減速では
        双方永久に解消できない（速度を合わせても下げても重なりが残る）。交換後は互いに別レーンの
        同じ縦位置に収まるため相互干渉は消える。パートナー以外の前後車とは通常と同じ相対制動
        モデルの安全ギャップを要求し、満たすペアのみ EDF 順に交換する。交換した要求車IDの集合を返す。
        """
        swapped: set[str] = set()
        for i, a in enumerate(unresolved):
            if a.veh_id in swapped:
                continue
            a_next = Safety.next_lane(a)
            for b in unresolved[i + 1 :]:
                if b.veh_id in swapped:
                    continue
                if b.current_lane != a_next or Safety.next_lane(b) != a.current_lane:
                    continue
                if abs(b.current_pos - a.current_pos) > SWAP_WINDOW:
                    continue
                if not (
                    Layer2._slot_free(committed, a_next, a.current_pos)
                    and Layer2._slot_free(committed, a.current_lane, b.current_pos)
                ):
                    continue
                a_obs = snap.obs[a.veh_id]
                b_obs = snap.obs[b.veh_id]
                # 壁位置（両者とも締切の直前）でのみ、停止車列に対する live 検査の minGap 緩和を許す。
                # 壁ペアは前方余地が無く他の解消手段（前進整列・提供車の譲歩）が幾何学的に存在しない一方、
                # 中間帯の停止ペアまで緩和すると流全体が組み変わり別条件へ失敗が移動する（47run比較で実証）。
                at_wall = (
                    a.current_pos >= a.deadline_pos - SWAP_WINDOW and b.current_pos >= b.deadline_pos - SWAP_WINDOW
                )
                if not (
                    Layer2._swap_gap_ok(snap, a_next, a.current_pos, a_obs.speed, ignore_id=b.veh_id)
                    and Layer2._swap_gap_ok(snap, a.current_lane, b.current_pos, b_obs.speed, ignore_id=a.veh_id)
                    and Layer2._swap_live_gap_ok(a.veh_id, a_obs.speed, a_next < a.current_lane, b.veh_id, at_wall)
                    and Layer2._swap_live_gap_ok(
                        b.veh_id, b_obs.speed, a.current_lane < b.current_lane, a.veh_id, at_wall
                    )
                ):
                    continue
                traci.vehicle.changeLane(a.veh_id, a_next, 0)
                traci.vehicle.changeLane(b.veh_id, a.current_lane, 0)
                committed.setdefault(a_next, []).append(a.current_pos)
                committed.setdefault(a.current_lane, []).append(b.current_pos)
                swapped.update((a.veh_id, b.veh_id))
                print(
                    f"[swap] t={snap.sim_time:.1f} {a.veh_id}(lane{a.current_lane}->{a_next})"
                    f" <-> {b.veh_id}(lane{b.current_lane}->{a.current_lane})"
                    f" pos {a.current_pos:.1f}/{b.current_pos:.1f}"
                )
                break
        return swapped

    @staticmethod
    def _swap_live_gap_ok(veh_id: str, ego_speed: float, going_right: bool, partner_id: str, at_wall: bool) -> bool:
        """スワップ相手を除く実測（getNeighbors）の前後ギャップ検査。

        スナップショットの ``lane_members`` は本線 edge 上の車両しか含まず、車体がジャンクション
        内部レーンに跨る車両（前端が junction に入り後端が本線に残る車・流入直前の車）が見えない。
        その盲点の第三者と重なって交換すると side collision になるため、通常挿入と同じ
        junction 越境の実測検査を追加で行う（相手＝partner は交換で居なくなるので除外）。

        要求量は原則、通常挿入と同じ「バンパー間 minGap+余裕」。ただし**壁ペア（at_wall=両者とも
        締切直前）かつ自車・第三者とも事実上停止（< SWAP_STOPPED_EPS）の場合に限り** minGap 分を
        差し引いた物理ギャップ基準に緩和する: 停止車列の自然間隔（バンパー間≈minGap）の第三者が
        0.4m 差で live 検査だけを永久に落とし、壁位置で整列した対向ペアが交換できず両レーン先頭が
        凍結する全域グリッドロックになる（weave2 Q3000 f0.6 seed2 で実測した残存変種）。壁ペアは
        前方余地が無く他の解消手段が幾何学的に存在しない。停止域では minGap 未満の車間も無害
        （SUMO の衝突は重なりのみ）。一方、緩和を走行中や中間帯の停止ペアへ広げると流全体が
        組み変わり別条件へ失敗が移動する（衝突増・新たな未完了。47run 比較で実証）ため、
        壁ペア×停止域に限定する。
        """
        lat = 1 if going_right else 0
        for nid, dist in get_veh_neighbors(veh_id, lat):  # 後続
            if nid == partner_id:
                continue
            v_n = get_veh_speed(nid)
            required = Safety.net_required(v_n, ego_speed)
            if at_wall and max(ego_speed, v_n) < SWAP_STOPPED_EPS:
                required -= MIN_GAP
            if dist < required:
                return False
        for nid, dist in Layer2._front_neighbors(veh_id, going_right, ego_speed):  # 先行（目標車線の先の車も含む）
            if nid == partner_id:
                continue
            v_n = get_veh_speed(nid)
            required = Safety.net_required(ego_speed, v_n)
            if at_wall and max(ego_speed, v_n) < SWAP_STOPPED_EPS:
                required -= MIN_GAP
            if dist < required:
                return False
        return True

    @staticmethod
    def _swap_gap_ok(snap: Snapshot, lane: int, pos: float, speed: float, ignore_id: str) -> bool:
        """スワップ相手を除いた目標車線の最近傍 前走/後続 に対し、相対制動モデルの安全ギャップを満たすか。

        交換後もパートナーとは別レーンになるため、パートナー（ignore_id）だけを除外して判定する。
        スナップショットの縦位置は前端基準（getLanePosition）なので車長を差し引いて実ギャップにする。
        静的要求は minGap でなく制動項＋余裕（``_net_required``）のみとする: スワップは互いの足跡を
        入れ替えるだけで各車線の車間パターンをほぼ保存するため（新ギャップ＝旧ギャップ∓Δpos）、
        現に成立している車列に minGap 未満の車間ができても停止・微速域では無害（SUMO の衝突は重なり
        のみ）。minGap を要求すると、詰まった車列で第三者の 20cm 差がスワップを永久拒否し、その第三者
        はスワップが動かないと動けない循環待ち（残存デッドロック）になる。
        """
        leader: VehObs | None = None
        follower: VehObs | None = None
        for vid in snap.lane_members.get(f"{snap.mainlane_edge}_{lane}", []):  # 縦位置降順
            o = snap.obs[vid]
            if vid == ignore_id or o.lane_pos is None:
                continue
            if o.lane_pos >= pos:
                leader = o  # 降順走査なので最後に残る leader が最近傍前走
            else:
                follower = o
                break
        if leader is not None and leader.lane_pos is not None:
            gap = (leader.lane_pos - VEH_LENGTH) - pos
            if gap < Safety.net_required(speed, leader.speed):
                return False
        if follower is not None and follower.lane_pos is not None:
            gap = (pos - VEH_LENGTH) - follower.lane_pos
            if gap < Safety.net_required(follower.speed, speed):
                return False
        return True

    # --- 提供車の協調減速 ---

    @staticmethod
    def _provider_yield(provider: V2CAV, requester: V2CAV, req: LCRequest, now: float) -> None:
        """提供車が要求車の後方に挿入可能な隙間を作る（必要なときだけ・有界に譲る）。

        呼ばれるのは、要求車の自力挿入が「後続側の隙間不足」で落ちたときだけ（先行側の不足や整列で解ける場合は
        譲らない）。必要量は挿入判定と同じ相対制動 ``Safety.net_required(v_提供, v_要求)``（提供車が後続）で測り、
        自分との隙間が既に足りていれば何もしない（足りないのは別の後続＝譲っても解けない）。

        - 要求車が走行中: 「要求車速度 − YIELD_DELTA」（ただし要求車速度の YIELD_MIN_RATIO 以上）を目標に、
          減速度 YIELD_DECEL 以下で下がる。車線内で停止はしない。
        - 要求車が停止気味（< YIELD_MIN_SPEED）: 減速では解けない（相対位置が凍結する）。前方に通過余地があれば
          要求車を追い越して後方を空ける（通過後は次の後続が提供車になる）。余地が無いときは、要求車が締切の
          HOLD_MARGIN 以内で止まっている場合に限り、その場で止まってスロットを守る。それ以外は譲らない。
        - 同じ要求車に YIELD_MAX_DURATION 以上譲り続けても入れなければ譲りをやめ、YIELD_COOLDOWN の間は
          提供車に選ばれない（RSU が次の後続を選ぶ）。

        旧実装（不足率に比例して要求車速度の最大 7 割まで減速、前方が詰まれば常にその場で停止、毎 step 無条件に
        呼ぶ）は、譲っている車が動くボトルネックになっていた。指令は候補（``propose``）として出す。
        """
        p = provider
        r = requester
        if r.lane_pos is None or p.lane_pos is None:
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
        if current_net >= Safety.net_required(p.speed, r.speed):
            return  # 自分との隙間は足りている
        p.yield_time += TIME_STEP
        if r.speed >= YIELD_MIN_SPEED:
            target = max(r.speed - YIELD_DELTA, YIELD_MIN_RATIO * r.speed, 0.0)
            p.propose("yield", target, YIELD_DECEL)
            return
        # 要求車の前へ完全に抜けて minGap＋余裕を残すのに要する走行余地
        pass_room = (r.lane_pos - p.lane_pos) + VEH_LENGTH + MIN_GAP + 2.0
        if p.leader_distance is None or p.leader_distance >= pass_room:
            p.propose("pass", r.speed + ALIGN_DELTA, abs(MAX_DECEL))  # 通過: 加速側は追従の候補が上限
            return
        if req.deadline_pos - r.lane_pos <= HOLD_MARGIN:
            p.propose("yield", 0.0, abs(MAX_DECEL))  # 締切直前で止まっている要求車のスロットを守る

    @staticmethod
    def _hold_before_deadline(requester: V2CAV, req: LCRequest) -> None:
        """挿入できていない要求車に、締切位置 D で HOLD_DECEL の減速で止まれる速度 √(2·HOLD_DECEL·残距離) を上限として与える。

        分岐直前まで巡航して SUMO トポロジー（teleport 無効の lane-drop）で急停止するのを避ける挙動品質（F5）。
        旧実装の「D で停止する減速で目標 0 を指令」は、100 m 手前から全車が止まる計画で減速して流れを落とし、
        減速した要求車と後続の速度差が開いて後続側の必要車間が広がる（かえって入れなくなる）副作用があった。
        上限なので、整列・挿入はそのまま続く。候補として出すので前車が近ければ追従の最大減速が勝つ。
        """
        remaining = req.deadline_pos - req.current_pos  # D までの残距離（要求は road==mainlane の同一フレームで生成）
        if remaining <= 0:
            return  # 既に D を越えていれば SUMO トポロジーに任せる
        v_hold = math.sqrt(2 * HOLD_DECEL * remaining)
        requester.propose("hold", v_hold, abs(MAX_DECEL))

    @staticmethod
    def _requester_align_to_slot(requester: V2CAV, req: LCRequest, check: InsertionCheck) -> None:
        """要求車自身を目標レーンのスロットに縦方向で整列させる（合流のための自己速度調整）。

        自力挿入の判定でどちら側が足りなかったかで挙動を分ける::

            前方が足りない … 足りない前方隣接車（対向要求車か通常の流れかを問わない）のうち最も遅いものより
                ALIGN_DELTA 遅く走って相対的に後ろへずれる。同速で並走すると横並びが固定され、進入時に横並びに
                なった車は最後まで入れない（lane-drop 終端に並んで到達→相互ブロック）。
            後方だけ足りない … 前方余地と制限速度の範囲で ALIGN_DELTA 速く走って前へずれる（提供車の譲りと対称。
                後続より速くなれば相対制動の必要量も縮む）。

        減速度は ALIGN_DECEL 以下（快適減速）。候補として出すので own leader が近ければ追従の最大減速が勝つ。
        """
        if requester.speed <= 0:
            return
        going_right = Safety.next_lane(req) < req.current_lane
        fronts = [
            (nid, dist, get_veh_speed(nid))
            for nid, dist in Layer2._front_neighbors(requester.id, going_right, requester.speed)
        ]
        if not check.front_ok:
            blocking = [v for _, dist, v in fronts if dist < Safety.net_required(requester.speed, v)]
            if blocking:
                requester.propose("align", max(min(blocking) - ALIGN_DELTA, 0.0), ALIGN_DECEL)
            return
        if not check.rear_ok and requester.lane_id is not None:
            target = min(requester.speed + ALIGN_DELTA, get_lane_max_speed(requester.lane_id))
            if fronts:
                target = min(target, min(v for _, _, v in fronts) + ALIGN_DELTA)
            if target > requester.speed:
                requester.propose("align", target, ALIGN_DECEL)
