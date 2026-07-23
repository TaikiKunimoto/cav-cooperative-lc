"""シミュレーション環境（シナリオ）の定義。

環境＝形状（何が起きるか・net が決める固定構造）。負荷・規模（どれだけ）はパラメータ（総流入 Q・必須LC比率 f）。
評価環境_パターン網羅.md §4「環境＝形状／パラメータ＝負荷・規模」。コア調停（EDF/2フェーズ/Layer2）は
環境非依存で、各車は ``(target_lane, deadline_pos)`` だけを持つ（route 名 r_exit/r_pass には依存しない）。

新しい環境（合流M・封鎖B・織込み 等）は net/rou を用意し Environment を1つ追加して ENVIRONMENTS に登録する。
現状 net が存在するのは環境①（分流D）のみ。
"""

from typing import NamedTuple

from pydantic import BaseModel, ConfigDict


class Group(BaseModel):
    """車両グループ。必須LC車は target_lane/deadline_pos を持ち、through 車（必須LCなし）は None。"""

    model_config = ConfigDict(frozen=True)

    name: str
    route: str  # SUMO ルート id（net を通すための経路。機構は参照しない）
    weight: float  # グループ内分の重み（同種グループ間で流入を内分するのに使う）
    target_lane: int | None = None  # 必須LC の目標レーン（None=必須LCなし）
    deadline_pos: float | None = None  # 締切位置（target_lane != None のとき必須）
    depart_edge: str | None = None  # 投入 edge（None=本線 mainlane_edge）
    depart_lanes: tuple[int, ...] | None = None  # 投入レーン候補（None=投入edgeの全レーン）


class GroupRate(NamedTuple):
    """グループと、その展開後の流入量のペア（group_rates の結果）。``for group, rate in ...`` のタプル展開も可。"""

    group: Group
    rate: float  # 流入量 [veh/h]


class Environment(BaseModel):
    """1シナリオの固定構造。負荷（総流入 Q・必須LC比率 f）は実行時パラメータで与える。"""

    model_config = ConfigDict(frozen=True)

    name: str
    sumocfg: str  # config パス（cwd=TraCI からの相対）
    mainlane_edge: str  # 本線 edge（pos・締切の基準・lane_members のキー）
    mainlane_length: float  # 本線長
    groups: tuple[Group, ...]

    def with_measured_length(self, actual_length: float) -> "Environment":
        """本線の実測エッジ長を反映した Environment を返す（シミュレーション開始後に net から取得して適用）。

        環境定義の ``mainlane_length`` は公称値で、netconvert が生成する実エッジ長（ジャンクション形状で
        数m 伸びる）と一致しない。締切 D を公称値のままにすると「公称長〜実エッジ長」の区間で完了した
        必須LC（物理的には lane-drop 手前で成功し出口へ到達）が失敗として誤計上される。
        締切が公称長と一致するグループ（＝lane-drop 端を締切とする定義）の deadline_pos を実測長へ置き換える。
        """
        if actual_length <= 0:
            raise ValueError(f"本線 {self.mainlane_edge} の実測長が不正です: {actual_length}")
        groups = tuple(
            g.model_copy(update={"deadline_pos": actual_length}) if g.deadline_pos == self.mainlane_length else g
            for g in self.groups
        )
        return self.model_copy(update={"mainlane_length": actual_length, "groups": groups})

    def group_rates(self, total_inflow: float, mlc_ratio: float) -> list[GroupRate]:
        """総流入 Q と必須LC比率 f を、グループ別の流入量[veh/h]に展開する（グループ定義順を保持）。

        必須LC車（target_lane!=None）を全体の f、through 車を (1−f) とし、同種グループ間は weight で内分する。
        単一要素環境（through+必須LC1種）なら f がそのまま必須LC比率になる。必須LCグループが無い環境
        （straight 等。障害物は --obstacle で動的付与）では f を無視し全車を through にする。
        """
        has_mlc = any(g.target_lane is not None for g in self.groups)
        f = mlc_ratio if has_mlc else 0.0
        mlc_weight = sum(g.weight for g in self.groups if g.target_lane is not None) or 1.0
        through_weight = sum(g.weight for g in self.groups if g.target_lane is None) or 1.0
        rates: list[GroupRate] = []
        for g in self.groups:
            if g.target_lane is not None:
                rates.append(GroupRate(g, total_inflow * f * (g.weight / mlc_weight)))
            else:
                rates.append(GroupRate(g, total_inflow * (1.0 - f) * (g.weight / through_weight)))
        return rates


# --- 環境① S-D 単一分流（実分流形状, 本線3車線・1000m）。投入は DivergeZone 直接。分流車は本線3車線を ~1000m 走る間に
# 右端 lane0（占有された本線）へ必須LC＝調停の本体（deadline=DivergeStart=1000m地点）。DivergeStart で lane0 が
# オフランプ車線(ExitRamp ~100m)へ分岐し出口へ。through と lane1,2 は MainLane へ継続。net は手動con（config/v2/diverge/build.sh）---
DIVERGE = Environment(
    name="diverge",
    sumocfg="../config/v2/diverge/diverge.sumocfg",
    mainlane_edge="DivergeZone",  # 本線3車線の調停区間（lane0=右端=出口側。DivergeStart で lane0→ExitRamp 分岐）
    mainlane_length=1000.0,  # DivergeStart（lane0 がオフランプ車線へ分岐する位置）＝ 締切
    groups=(
        # 直進（必須LCなし）。本線3車線（DivergeZone → MainLane）
        Group(name="through", route="r_pass", weight=1.0),
        # 分流（本線右端 lane0 へ必須LC＝調停、締切=DivergeStart）→ lane0 から ExitRamp(オフランプ車線)へ抜け出口
        Group(name="exiting", route="r_exit", weight=1.0, target_lane=0, deadline_pos=1000.0),
    ),
)

# --- 環境② S-M 単一合流（実合流形状）。手前に「本線2車線 / 合流ランプ1車線」が完全独立の区間があり、
# MergeNode で合流ランプが本線下側へ加速車線(MergeZone lane0)として連結、加速車線端 AccelEnd で drop。
# 合流車は OnRamp→加速車線(lane0)→本線 lane1 へ必須LC（上向き）。net は ramps.guess 生成（config/v2/merge/build.sh）---
MERGE = Environment(
    name="merge",
    sumocfg="../config/v2/merge/merge.sumocfg",
    mainlane_edge="MergeZone",  # 加速車線を含む3車線の連結区間（調停対象）
    mainlane_length=194.0,  # 加速車線(MergeZone lane0)が drop する位置 ＝ 締切
    groups=(
        # 直進（必須LCなし）。本線2車線（MainApproach lane0/1 → MergeZone lane1/2）
        Group(name="through", route="r_main", weight=1.0, depart_edge="MainApproach"),
        # 合流（合流ランプ OnRamp → 加速車線 MergeZone lane0 → 目標 lane1 へ必須LC、締切=加速車線端）
        Group(name="merging", route="r_ramp", weight=1.0, target_lane=1, deadline_pos=194.0, depart_edge="OnRamp"),
    ),
)

# --- 環境③ 素地 = 直進3車線（straight, 1000m）。障害物Bは --obstacle で動的に発生させる（突発タイミング＝パラメータ）---
# 例: env③ S-B1 = `--env straight --obstacle 1,600,80`（Lane1・pos600・t80 で停止車両を発生）
STRAIGHT = Environment(
    name="straight",
    sumocfg="../config/v2/straight/straight.sumocfg",
    mainlane_edge="Road",
    mainlane_length=1000.0,
    groups=(Group(name="through", route="r_main", weight=1.0),),  # 全車 through（必須LCは障害物で動的付与）
)

# --- 環境⑤ MD-2 両側織込み（上下左右対称, 4車線コア 1:2:1）。WeaveStart で上下2本のオンランプが合流し
# （OnRampBottom→加速車線 lane0(下) / OnRampTop→加速車線 lane3(上)）、WeaveEnd で上下2本のオフランプへ分流する
# （lane0(下)→OffRampBottom / lane3(上)→OffRampTop）。本線2車線は中央 lane1,2。合流車は外側 lane0/lane3 から
# 本線 lane1/lane2 へ、分流車は本線から外側 lane0/lane3 へ必須LC＝上下左右対称の両側織込み。MLC4種は weight 均等。
# net は手動 connection 生成（config/v2/weave2/build.sh）---
WEAVE2 = Environment(
    name="weave2",
    sumocfg="../config/v2/weave2/weave2.sumocfg",
    mainlane_edge="WeaveZone",  # 加速/出口車線を含む4車線の織込みゾーン（調停対象, 1:2:1）
    mainlane_length=392.0,  # 加速車線drop＝出口分岐位置＝締切（WeaveZone 実net長）
    groups=(
        # 直進（必須LCなし）。本線2車線（MainApproach → WeaveZone lane1/2 → MainLane）
        Group(name="through", route="r_main", weight=1.0, depart_edge="MainApproach"),
        # 合流(下): OnRampBottom → 加速車線 lane0 → 目標 lane1 へ必須LC（下から上）
        Group(
            name="merging_bottom",
            route="r_ramp_b",
            weight=1.0,
            target_lane=1,
            deadline_pos=392.0,
            depart_edge="OnRampBottom",
        ),
        # 合流(上): OnRampTop → 加速車線 lane3 → 目標 lane2 へ必須LC（上から下, 合流下と上下対称）
        Group(
            name="merging_top",
            route="r_ramp_t",
            weight=1.0,
            target_lane=2,
            deadline_pos=392.0,
            depart_edge="OnRampTop",
        ),
        # 分流(下): 本線 → 出口車線 lane0 へ必須LC → OffRampBottom（合流と逆向き＝織込み）
        Group(
            name="diverging_bottom",
            route="r_exit_b",
            weight=1.0,
            target_lane=0,
            deadline_pos=392.0,
            depart_edge="MainApproach",
        ),
        # 分流(上): 本線 → 出口車線 lane3 へ必須LC → OffRampTop（分流下と上下対称）
        Group(
            name="diverging_top",
            route="r_exit_t",
            weight=1.0,
            target_lane=3,
            deadline_pos=392.0,
            depart_edge="MainApproach",
        ),
        # 対角(下→上): OnRampBottom(lane0) → 対角 lane3 へ3車線必須LC → OffRampTop（織込みの交差移動。weight=2で強調）
        Group(
            name="crossing_bt",
            route="r_cross_bt",
            weight=2.0,
            target_lane=3,
            deadline_pos=392.0,
            depart_edge="OnRampBottom",
        ),
        # 対角(上→下): OnRampTop(lane3) → 対角 lane0 へ3車線必須LC → OffRampBottom（対角下→上と上下対称, weight=2）
        Group(
            name="crossing_tb",
            route="r_cross_tb",
            weight=2.0,
            target_lane=0,
            deadline_pos=392.0,
            depart_edge="OnRampTop",
        ),
    ),
)

# --- 環境④ MD-1f 一側織込み（実形状）。手前に「本線2車線 / オンランプ1車線」の独立区間があり、WeaveStart で
# オンランプが本線下側の補助車線(WeaveZone lane0)として連結。織込み区間(WeaveZone 3車線)の補助車線で、合流車
# (OnRamp→lane0→本線lane1, 上へ)と分流車(本線→lane0→出口, 下へ)が逆向きに交差。補助車線端 WeaveEnd で
# lane0 はオフランプ(ExitRamp)へ抜ける。net は ramps.guess 生成（config/v2/weave/build.sh）---
WEAVE = Environment(
    name="weave",
    sumocfg="../config/v2/weave/weave.sumocfg",
    mainlane_edge="WeaveZone",  # 補助車線を含む3車線の織込み区間（調停対象）
    mainlane_length=196.0,  # 補助車線(WeaveZone lane0)が出口へ抜ける位置 ＝ 締切
    groups=(
        # 直進（必須LCなし）。本線2車線（MainApproach → WeaveZone lane1/2 → MainLane）
        Group(name="through", route="r_main", weight=1.0, depart_edge="MainApproach"),
        # 合流（オンランプ → 補助車線 WeaveZone lane0 → 本線 lane1 へ上がる）
        Group(name="merging", route="r_ramp", weight=1.0, target_lane=1, deadline_pos=196.0, depart_edge="OnRamp"),
        # 分流（本線 → 補助車線 WeaveZone lane0 へ降りる → 出口）。合流と逆向きに補助車線で交差＝織込み
        Group(
            name="diverging", route="r_exit", weight=1.0, target_lane=0, deadline_pos=196.0, depart_edge="MainApproach"
        ),
    ),
)

ENVIRONMENTS: dict[str, Environment] = {e.name: e for e in (DIVERGE, MERGE, STRAIGHT, WEAVE2, WEAVE)}
