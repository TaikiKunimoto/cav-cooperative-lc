"""現実の車にできない動きの検査（観測のみ。挙動は変えない）。

SUMO の車線変更は 1 step で終わり、TraCI の changeLane は lane change mode 0 だと SUMO の安全確認を経ずに実行される。
そのため、車体が重なる位置への車線変更や、横に重なった 2 台が互いの車線へ移る「すり抜け」も通ってしまう
（SUMO はこれを衝突と数えないことがある）。毎 step の車線と縦位置を前 step と比べて、その回数を数える。
"""

from v2.constants import VEH_LENGTH

# 本線 edge 上の車の (車線インデックス, 縦位置〔前端〕)
LanePositions = dict[str, tuple[int, float]]


class Feasibility:
    """車線変更の現実性の検査（状態を持たない静的ロジック）。"""

    @staticmethod
    def count_violations(prev: LanePositions, cur: LanePositions) -> tuple[int, int]:
        """前 step と今 step の (車線, 縦位置) から、(車体が重なる位置への車線変更の数, すり抜けの数) を返す。

        - 重なり: 車線を移った車の車体（前端から VEH_LENGTH）が、移った先の車線の別の車の車体と縦に重なる。
        - すり抜け: 2 車線の間で逆向きに移った 2 台が、移る前に縦に重なっていた（車体が互いを通り抜ける）。
        どちらも本線 edge 上にいる車どうしだけを見る（車線インデックスは edge ごとの番号のため）。
        """
        moved = {vid: (prev[vid][0], lane) for vid, (lane, _) in cur.items() if vid in prev and prev[vid][0] != lane}
        by_lane: dict[int, list[tuple[str, float]]] = {}
        for vid, (lane, pos) in cur.items():
            by_lane.setdefault(lane, []).append((vid, pos))
        overlap = 0
        for vid, (_, to_lane) in moved.items():
            pos = cur[vid][1]
            if any(other != vid and abs(p - pos) < VEH_LENGTH for other, p in by_lane.get(to_lane, [])):
                overlap += 1
        crossing = 0
        items = list(moved.items())
        for i, (a, (from_a, to_a)) in enumerate(items):
            for b, (from_b, to_b) in items[i + 1 :]:
                if from_a == to_b and to_a == from_b and abs(prev[a][1] - prev[b][1]) < VEH_LENGTH:
                    crossing += 1
        return overlap, crossing
