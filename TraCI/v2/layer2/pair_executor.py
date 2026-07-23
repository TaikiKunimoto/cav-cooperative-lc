"""Layer2: 実行。要求車は目標車線に十分なギャップがあれば（協調不要でも）瞬時LCし、無ければ提供車が協調減速で gap を開ける。

挿入が安全（前後二方向 OK）なら ``changeLane`` で瞬時に1段車線変更する。これは**提供車の有無に依らない**：
目標車線が空（合流先の専用車線・疎な車線など）でも、ギャップが安全なら自力で移れる（provider が居ないと一切動けない
旧挙動の修正）。安全でない要求は3段構えで解消する::

    1. 対向スワップ … 隣接レーンで互いのレーンを目指し重なった2要求車（織込みの構造的ペア）は、
       どちらも通常挿入が幾何学的に不可能。同一stepの相互 changeLane で原子的に交換する。
    2. 提供車の協調減速 … Phase B の割当提供車が要求車の後方に gap を開ける。
    3. 要求車のスロット整列 … 前方隣接車にブロックされていれば「前方 − ALIGN_DELTA」まで下げて
       相対的に後ろへずれ、後方だけ未充足なら前方余地の範囲でわずかに前へ出る。旧実装の
       「前方と同速に合わせる」は重なりを固定し、lane-drop 終端に並んで到達→相互ブロックの
       永久デッドロック（織込み環境のグリッドロック起点）を生んでいた。

本処理は control_speed の後に呼び、協調減速の slowDown と changeLane が最後の指令になるようにする。
"""

import math
import os
import sys

from utils.traci_wrapper import get_lane_max_speed, get_veh_neighbors, get_veh_speed, slow_down
from v2.constants import (
    ALIGN_DELTA,
    HOLD_MARGIN,
    LC_SAFETY_MARGIN,
    MAX_ACCEL,
    MAX_DECEL,
    MAX_SPEED,
    MIN_GAP,
    SWAP_WINDOW,
    VEH_LENGTH,
)
from v2.layer1.priority import EDF
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
        for req in req_by_id.values():  # EDF 順（鍵昇順）
            requester = by_id.get(req.veh_id)
            if requester is None:
                continue
            target_lane = Safety.next_lane(req)
            if Layer2._insertion_safe_live(
                requester.id, requester.speed, target_lane < req.current_lane
            ) and Layer2._slot_free(committed, target_lane, req.current_pos):
                traci.vehicle.changeLane(requester.id, target_lane, 0)
                committed.setdefault(target_lane, []).append(req.current_pos)
                lc_count += 1
            else:
                unresolved.append(req)

        swapped = Layer2._execute_swaps(unresolved, snap, committed)
        lc_count += len(swapped)

        for req in unresolved:
            if req.veh_id in swapped:
                continue
            requester = by_id[req.veh_id]
            provider_id = provider_of.get(req.veh_id)
            provider = by_id.get(provider_id) if provider_id is not None else None
            if provider is not None:
                Layer2._provider_yield(provider, requester)
            if EDF.effective_distance(req) <= HOLD_MARGIN:
                # 締切間近でなお挿入不可: D手前で滑らかに停止し行き止まり(lane-drop)の急停止を防ぐ（F5・最終手段）
                Layer2._hold_before_deadline(requester, req)
            else:
                # それ以前: 目標レーンのスロットに自分を縦方向で整列させ、挿入成功率を上げる
                Layer2._requester_align_to_slot(requester, req, req_by_id)
        return lc_count

    @staticmethod
    def _slot_free(committed: dict[int, list[float]], target_lane: int, pos: float) -> bool:
        """今step、同じ目標車線で重なる位置への挿入が既に確定していないか（同時LC衝突を防ぐ）。"""
        check_range = VEH_LENGTH + MIN_GAP
        return all(abs(p - pos) >= check_range for p in committed.get(target_lane, []))

    @staticmethod
    def _insertion_safe_live(veh_id: str, ego_speed: float, going_right: bool) -> bool:
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
        これを満たさない隙間は False を返し、提供車が gap を開け切るまで待つ（本来の協調挙動へ回す）。
        """
        lat = 1 if going_right else 0  # bit1: 左=0 / 右=1
        for nid, dist in get_veh_neighbors(veh_id, lat):  # 後続（bit2=0）: ego が止まっても後続が止まれる車間
            if dist < Layer2._net_required(get_veh_speed(nid), ego_speed):
                return False
        for nid, dist in get_veh_neighbors(veh_id, lat | 2):  # 先行（bit2=1）: ego が先行へ追突しない車間
            if dist < Layer2._net_required(ego_speed, get_veh_speed(nid)):
                return False
        return True

    @staticmethod
    def _net_required(v_back: float, v_front: float) -> float:
        """minGap 控除済みギャップに対する必要量 ＝ 相対制動項 ＋ 1step進化バッファ。

        後続 v_back が前方 v_front へ最大減速で追突しない車間の minGap 超過分。判定と changeLane
        反映の1stepズレで相対位置が動くため ``LC_SAFETY_MARGIN`` を足す（境界挿入の側面衝突防止）。
        """
        return max(0.0, (v_back**2 - v_front**2) / (2 * abs(MAX_DECEL))) + LC_SAFETY_MARGIN

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
                if not (
                    Layer2._swap_gap_ok(snap, a_next, a.current_pos, a_obs.speed, ignore_id=b.veh_id)
                    and Layer2._swap_gap_ok(snap, a.current_lane, b.current_pos, b_obs.speed, ignore_id=a.veh_id)
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
    def _swap_gap_ok(snap: Snapshot, lane: int, pos: float, speed: float, ignore_id: str) -> bool:
        """スワップ相手を除いた目標車線の最近傍 前走/後続 に対し、相対制動モデルの安全ギャップを満たすか。

        交換後もパートナーとは別レーンになるため、パートナー（ignore_id）だけを除外して判定する。
        スナップショットの縦位置は前端基準（getLanePosition）なので車長を差し引いて実ギャップにする。
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
            if gap < MIN_GAP + Layer2._net_required(speed, leader.speed):
                return False
        if follower is not None and follower.lane_pos is not None:
            gap = (pos - VEH_LENGTH) - follower.lane_pos
            if gap < MIN_GAP + Layer2._net_required(follower.speed, speed):
                return False
        return True

    # --- 提供車の協調減速 ---

    @staticmethod
    def _provider_yield(provider: V2CAV, requester: V2CAV) -> None:
        """提供車が要求車の後方スロットを整える（不足なら強く下がり、横並びなら前へ抜け、遠すぎれば余裕を残して詰める）。

        旧実装（``_supporting_speed``）は「ギャップ充足 → 目標速度 0」と逆転しており、提供車が
        ゾーン中央で完全停止して自レーンを塞ぎ、渋滞を全レーンへ伝播させていた（グリッドロックの増幅器）。

        - 通過（要求車がほぼ停止、または横並びで gap 不足）: 減速では解けない。停止した要求車との
          相対位置は提供車も減速した時点で凍結し、以後どちらも動けない（28cm 不足のまま永久待機する
          事例を観測）。前方が空いていれば要求車を追い越して後方を明け渡す（現実の合流でも待つ車の
          真横には停まらず、流れが通過して後ろが空く）。通過後は次の後続が新提供車に再割当される。
        - 不足（gap < required、要求車は走行中）: 不足率に比例して強く下がる（最大で要求車の3割まで
          減速＝旧実装の譲歩強度。高速流へ挿入する要求車のギャップは弱い相対減速では開き切らない）。
        - 大余剰（gap > 2×required）: スロット手前まで前進して詰める（停止したまま遠方で待つと自レーンの
          後続 through 車を長時間塞ぐ）。接近速度は半分の減速度で吸収できる範囲＋制御遅延バッファに
          抑えて、スロットへの食い込み（横並び固着）を防ぐ。
        - それ以外（required〜2×required）: 要求車と同速で維持。
        加速方向の指令は自レーン前方に安全車間＋車長の余裕があるときのみ出す（control_speed の緊急減速を
        上書きせず、密な creep 中に前車へ突っ込まない）。
        """
        p = provider
        r = requester
        if r.lane_pos is None or p.lane_pos is None:
            return
        current_gap = r.lane_pos - p.lane_pos  # 提供車は要求車より後方（前端間距離）
        speed_diff = p.speed - r.speed
        required = VEH_LENGTH + MIN_GAP * 1.5
        if speed_diff > 0:
            required += Safety.g_req(p.speed) * (speed_diff / MAX_SPEED)
        if current_gap < required and (r.speed < 1.0 or current_gap < VEH_LENGTH):
            target = max(r.speed, 0.0) + ALIGN_DELTA  # 通過: 前へ抜けて要求車の後方を明け渡す
        elif current_gap < required:
            # 不足: 不足率に比例して強く下がる
            deficit = required - current_gap
            target = r.speed * (1.0 - 0.7 * min(deficit / required, 1.0))
        elif current_gap > 2 * required:
            # 大余剰: スロット手前（required + 1余裕）まで、半減速吸収可能な速度で詰める
            margin = current_gap - 2 * required
            approach = min(margin / 5.0, math.sqrt(abs(MAX_DECEL) * max(margin - 3.0, 0.0)))
            target = r.speed + approach
        else:
            target = r.speed  # 充足: 同速で維持（それ以上減速して車線を塞がない）
        if p.lane_id is not None:
            target = min(target, get_lane_max_speed(p.lane_id))
        if p.speed - target > 0.1:
            slow_down(p.id, target, (p.speed - target) / abs(MAX_DECEL))
        elif target - p.speed > 0.1 and (p.leader_distance is None or p.leader_distance >= p.safety_gap + VEH_LENGTH):
            slow_down(p.id, target, (target - p.speed) / MAX_ACCEL)

    @staticmethod
    def _hold_before_deadline(requester: V2CAV, req: LCRequest) -> None:
        """挿入できず提供車も無い要求車を、締切位置 D の手前で滑らかに減速・保持する（要求車自身の committed-wait, F5）。

        分岐直前まで巡航して SUMO トポロジー（teleport無効の lane-drop）で急停止するのを避ける挙動品質。EDF の鍵
        には載せない。D で停止する減速を MAX_DECEL 上限で slowDown する（dist ≤ HOLD_MARGIN の判定は呼び出し側）。
        leader が安全車間内に居る場合は control_speed の追従・緊急減速に委ねてスキップする：hold の弱い減速が
        緊急ブレーキを上書きすると hold 車列で後続が前車へ追突しうるため、先頭車（leader 遠い/無し）だけが hold する。
        """
        if requester.speed <= 0:
            return
        if requester.leader_distance is not None and requester.leader_distance < requester.safety_gap:
            return  # leader 近接: control_speed（追従・緊急ブレーキ）に委ねる
        remaining = req.deadline_pos - req.current_pos  # D までの残距離（要求は road==mainlane の同一フレームで生成）
        if remaining <= 0:
            return  # 既に D を越えていれば SUMO トポロジーに任せる
        needed_decel = (requester.speed**2) / (2 * remaining)  # D で停止するのに要する減速
        decel = min(needed_decel, abs(MAX_DECEL))  # 物理上限内で滑らかに（超過時は最大減速で best-effort）
        slow_down(requester.id, 0.0, requester.speed / decel)

    @staticmethod
    def _is_opposing(req: LCRequest, other: LCRequest | None) -> bool:
        """相手が「自分の現在レーンを次の1段の目標とする対向要求車」か（ロックステップ・デッドロックの相手方）。"""
        return other is not None and Safety.next_lane(other) == req.current_lane

    @staticmethod
    def _requester_align_to_slot(requester: V2CAV, req: LCRequest, req_by_id: dict[str, LCRequest]) -> None:
        """要求車自身を目標レーンのスロットに縦方向で整列させる（合流のための自己速度調整）。

        隣接ブロッカーの種別で挙動を分ける::

            前方ブロッカーが対向要求車 … 「前方 − ALIGN_DELTA」まで減速して相対的に後ろへずれる。
                同速追従では対向要求車と重なったまま速度同調し、lane-drop 終端に並んで到達して
                相互ブロックする（織込みデッドロックの起点）。積極的に縦位置をずらして解消する。
            後方ブロッカーが対向要求車 … 前方余地・制限速度の範囲でわずかに増速して前へずれる
                （前方へずれる側と減速側で対称に離れ、SWAP_WINDOW 外へ出れば通常挿入が可能になる）。
            通常の交通流にブロックされている … 目標レーン先行の流速に自分を合わせ、速度差を縮めて
                挿入成功率を上げる（従来挙動）。流れ相手に相対後退しても次の車が隙間を埋めるだけで
                後退が止まらなくなるため、フォールバックは対向要求車限定とする。

        own leader が安全車間内なら control_speed の追従・緊急減速に委ねてスキップする（緊急ブレーキ上書き防止）。
        """
        if requester.speed <= 0:
            return
        if requester.leader_distance is not None and requester.leader_distance < requester.safety_gap:
            return
        a = abs(MAX_DECEL)
        going_right = Safety.next_lane(req) < req.current_lane
        lat = 1 if going_right else 0
        front_speed: float | None = None
        front_opposing_speed: float | None = None
        for nid, dist in get_veh_neighbors(requester.id, lat | 2):  # 目標レーンの先行（bit2=1）
            speed = get_veh_speed(nid)
            front_speed = speed if front_speed is None else min(front_speed, speed)
            # dist は minGap 控除済み（_insertion_safe_live と同じ相対制動モデルの閾値）
            if dist < Layer2._net_required(requester.speed, speed) and Layer2._is_opposing(req, req_by_id.get(nid)):
                front_opposing_speed = speed if front_opposing_speed is None else min(front_opposing_speed, speed)
        if front_opposing_speed is not None:
            # 前方の対向要求車から相対的に下がって重なりを解消する
            target = max(front_opposing_speed - ALIGN_DELTA, 0.0)
            if requester.speed > target:
                slow_down(requester.id, target, (requester.speed - target) / a)
            return
        rear_opposing = False
        for nid, dist in get_veh_neighbors(requester.id, lat):  # 目標レーンの後続（bit2=0）
            speed = get_veh_speed(nid)
            if dist < Layer2._net_required(speed, requester.speed) and Layer2._is_opposing(req, req_by_id.get(nid)):
                rear_opposing = True
        if rear_opposing and requester.lane_id is not None:
            # 後方の対向要求車から前へずれて重なりを解消する（減速側と対称）。前方車には近づきすぎない
            target = min(requester.speed + ALIGN_DELTA, get_lane_max_speed(requester.lane_id))
            if front_speed is not None:
                target = min(target, front_speed + ALIGN_DELTA)
            if target - requester.speed > 0.1:
                slow_down(requester.id, target, (target - requester.speed) / MAX_ACCEL)
            return
        # 通常の交通流にブロックされている: 目標レーン先行の流速へ合わせる（従来の自己減速）
        if front_speed is None or requester.speed <= front_speed:
            return
        slow_down(requester.id, max(front_speed, 0.0), (requester.speed - front_speed) / a)
