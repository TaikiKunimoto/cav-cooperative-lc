"""シミュレーション環境（シナリオ）の定義。

環境＝形状（何が起きるか・net が決める固定構造）。負荷・規模（どれだけ）はパラメータ（総流入 Q・必須LC比率 f）。
評価環境_パターン網羅.md §4「環境＝形状／パラメータ＝負荷・規模」。コア調停（EDF/2フェーズ/Layer2）は
環境非依存で、各車は ``(target_lane, deadline_pos)`` だけを持つ（route 名 r_exit/r_pass には依存しない）。

道路網（net）は ``config/v2/build_networks.py`` が長さ・速度・車線幅をまとめて作る。区間長（``mainlane_length``）と
締切は、その道路網の区間の長さとちょうど一致させる（実行開始時に ``check_measured_length`` で照合する）。
新しい環境は build_networks.py に形を足し、rou を用意して Environment を 1 つ追加し ENVIRONMENTS に登録する。
"""

from typing import NamedTuple

from pydantic import BaseModel, ConfigDict

from v2.constants import ACTIVATION_MARGIN


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
    # 本線 edge のうち終端で本線へ続くレーン（through 車が走り続けてよいレーン）。突発障害物の回避先の選択と、
    # 回避後に本線へ戻る操作の目標に使う（出口車線・加速車線は含めない）
    through_lanes: tuple[int, ...]
    # 区間に入った時点で要求を出すか（合流・織り込み。区間に入った車は自分の行き先と車線が分かっている）。
    # False なら締切の ACTIVATION_MARGIN 手前から要求を出す（分流・封鎖）
    request_at_entry: bool = False
    # 本線 edge のうち合流ランプから続く車線（merge の加速車線、weave・weave2 の補助車線）。ここから本線へ移る要求車は、
    # 合流の速度 MERGE_SPEED まで加速してから移る（LCRequest.accelerating_to_merge）
    ramp_lanes: tuple[int, ...] = ()

    def default_activation_margin(self) -> float:
        """要求を出し始める位置（締切の何 m 手前か）の既定値。区間に入った時点なら区間長（＝締切）を返す。"""
        return self.mainlane_length if self.request_at_entry else ACTIVATION_MARGIN

    def check_measured_length(self, actual_length: float) -> None:
        """道路網の区間（本線 edge）の実長が ``mainlane_length`` と一致するかを確かめる（実行開始時）。

        道路網は build_networks.py が区間長ちょうどで作るので、ずれていれば道路網と環境の定義の片方だけを
        直したことになる。そのまま走らせると締切の位置が道路網と食い違うので止める。
        """
        if abs(actual_length - self.mainlane_length) > 0.01:
            raise ValueError(
                f"本線 {self.mainlane_edge} の実長 {actual_length:.2f} m が環境の定義 {self.mainlane_length:.2f} m と"
                " 違います。config/v2/build_networks.py と TraCI/v2/environment.py の区間長をそろえてください"
            )

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


# 5 環境の道路網は config/v2/build_networks.py が作る。共通: 車線幅 3.5 m、本線 100 km/h、合流ランプ 40 km/h（合流車は加速車線で
# 合流の速度 65 km/h まで加速してから本線へ移る）、出口ランプは本線と同じ速度（評価区間の外）、区間の手前の本線 500 m・
# 合流ランプ 300 m、区間の後ろ 200 m。
# 区間長（mainlane_length）と締切は道路網の区間の長さとちょうど同じ値にする。

# --- 環境① S-D 単一分流: 本線 3 車線の区間（1,000 m）の終わり DivergeStart で lane0 が出口（ExitRamp）と本線に分かれる。
# 出口へ行く車は締切（分岐点）の ACTIVATION_MARGIN 手前から右端 lane0 へ必須LC＝調停の本体。through と lane1,2 は MainLane へ ---
DIVERGE = Environment(
    name="diverge",
    sumocfg="../config/v2/diverge/diverge.sumocfg",
    mainlane_edge="DivergeZone",  # 本線3車線の調停区間（lane0=右端=出口側。DivergeStart で lane0→ExitRamp 分岐）
    mainlane_length=1000.0,  # DivergeStart（lane0 がオフランプ車線へ分岐する位置）＝ 締切
    through_lanes=(0, 1, 2),  # lane0 も MainLane へ続く（ExitRamp への分岐と並列）
    groups=(
        # 直進（必須LCなし）。本線3車線（DivergeZone → MainLane）
        Group(name="through", route="r_pass", weight=1.0),
        # 分流（本線右端 lane0 へ必須LC＝調停、締切=DivergeStart）→ lane0 から ExitRamp(オフランプ車線)へ抜け出口
        Group(name="exiting", route="r_exit", weight=1.0, target_lane=0, deadline_pos=1000.0),
    ),
)

# --- 環境② S-M 単一合流: 本線 2 車線と合流ランプ（40 km/h）が MergeNode で連結し、ランプの車線が加速車線（MergeZone lane0、
# 240 m＝本線 100 km/h の標準値、テーパを含む）になり、AccelEnd で無くなる。合流車は加速車線から本線 lane1 へ必須LC ---
MERGE = Environment(
    name="merge",
    sumocfg="../config/v2/merge/merge.sumocfg",
    mainlane_edge="MergeZone",  # 加速車線を含む3車線の連結区間（調停対象）
    mainlane_length=240.0,  # 加速車線(MergeZone lane0)が無くなる位置 ＝ 締切
    through_lanes=(1, 2),
    request_at_entry=True,
    ramp_lanes=(0,),
    groups=(
        # 直進（必須LCなし）。本線2車線（MainApproach lane0/1 → MergeZone lane1/2）
        Group(name="through", route="r_main", weight=1.0, depart_edge="MainApproach"),
        # 合流（合流ランプ OnRamp → 加速車線 MergeZone lane0 → 目標 lane1 へ必須LC、締切=加速車線端）
        Group(name="merging", route="r_ramp", weight=1.0, target_lane=1, deadline_pos=240.0, depart_edge="OnRamp"),
    ),
)

# --- 環境③ 素地 = 直進3車線（straight, 1000m）。障害物Bは --obstacle で動的に発生させる（突発タイミング＝パラメータ）---
# 例: env③ S-B1 = `--env straight --obstacle 1,600,80`（Lane1・pos600・t80 で停止車両を発生）
STRAIGHT = Environment(
    name="straight",
    sumocfg="../config/v2/straight/straight.sumocfg",
    mainlane_edge="Road",
    mainlane_length=1000.0,
    through_lanes=(0, 1, 2),
    groups=(Group(name="through", route="r_main", weight=1.0),),  # 全車 through（必須LCは障害物で動的付与）
)

# --- 環境⑤ MD-2 両側織込み（上下対称, 4車線コア 1:2:1、610 m）。WeaveStart で上下2本の合流ランプが連結し
# （OnRampBottom→補助車線 lane0(下) / OnRampTop→補助車線 lane3(上)）、WeaveEnd で上下2本の出口ランプへ分かれる
# （lane0(下)→OffRampBottom / lane3(上)→OffRampTop）。本線2車線は中央 lane1,2。合流車は外側 lane0/lane3 から
# 本線 lane1/lane2 へ、出口へ行く車は本線から外側 lane0/lane3 へ、対角の車は lane0↔lane3 へ必須LC。
# 要求を出す 6 グループは同じ重み ---
WEAVE2 = Environment(
    name="weave2",
    sumocfg="../config/v2/weave2/weave2.sumocfg",
    mainlane_edge="WeaveZone",  # 補助車線を含む4車線の織込みゾーン（調停対象, 1:2:1）
    mainlane_length=610.0,  # 補助車線が出口へ抜ける位置＝締切
    through_lanes=(1, 2),
    request_at_entry=True,
    ramp_lanes=(0, 3),
    groups=(
        # 直進（必須LCなし）。本線2車線（MainApproach → WeaveZone lane1/2 → MainLane）
        Group(name="through", route="r_main", weight=1.0, depart_edge="MainApproach"),
        # 合流(下): OnRampBottom → 補助車線 lane0 → 目標 lane1 へ必須LC（下から上）
        Group(
            name="merging_bottom",
            route="r_ramp_b",
            weight=1.0,
            target_lane=1,
            deadline_pos=610.0,
            depart_edge="OnRampBottom",
        ),
        # 合流(上): OnRampTop → 補助車線 lane3 → 目標 lane2 へ必須LC（上から下, 合流下と上下対称）
        Group(
            name="merging_top",
            route="r_ramp_t",
            weight=1.0,
            target_lane=2,
            deadline_pos=610.0,
            depart_edge="OnRampTop",
        ),
        # 分流(下): 本線 → 補助車線 lane0 へ必須LC → OffRampBottom（合流と逆向き＝織込み）
        Group(
            name="diverging_bottom",
            route="r_exit_b",
            weight=1.0,
            target_lane=0,
            deadline_pos=610.0,
            depart_edge="MainApproach",
        ),
        # 分流(上): 本線 → 補助車線 lane3 へ必須LC → OffRampTop（分流下と上下対称）
        Group(
            name="diverging_top",
            route="r_exit_t",
            weight=1.0,
            target_lane=3,
            deadline_pos=610.0,
            depart_edge="MainApproach",
        ),
        # 対角(下→上): OnRampBottom(lane0) → 対角 lane3 へ3車線必須LC → OffRampTop
        Group(
            name="crossing_bt",
            route="r_cross_bt",
            weight=1.0,
            target_lane=3,
            deadline_pos=610.0,
            depart_edge="OnRampBottom",
        ),
        # 対角(上→下): OnRampTop(lane3) → 対角 lane0 へ3車線必須LC → OffRampBottom（対角下→上と上下対称）
        Group(
            name="crossing_tb",
            route="r_cross_tb",
            weight=1.0,
            target_lane=0,
            deadline_pos=610.0,
            depart_edge="OnRampTop",
        ),
    ),
)

# --- 環境④ MD-1f 一側織込み: 本線 2 車線と合流ランプ（40 km/h）が WeaveStart で連結し、ランプの車線が補助車線
# （WeaveZone lane0）になる。織込み区間（WeaveZone 3 車線、457 m）の補助車線で、合流車（OnRamp→lane0→本線 lane1、上へ）と
# 出口へ行く車（本線→lane0→出口、下へ）が逆向きに交差する。補助車線は WeaveEnd で出口ランプ（ExitRamp）へ抜ける。
# 区間長 457 m は仮（1,500 ft）。日本の基準値を確かめたら build_networks.py の Geometry.weave と一緒に差し替える ---
WEAVE = Environment(
    name="weave",
    sumocfg="../config/v2/weave/weave.sumocfg",
    mainlane_edge="WeaveZone",  # 補助車線を含む3車線の織込み区間（調停対象）
    mainlane_length=457.0,  # 補助車線(WeaveZone lane0)が出口へ抜ける位置 ＝ 締切
    through_lanes=(1, 2),
    request_at_entry=True,
    ramp_lanes=(0,),
    groups=(
        # 直進（必須LCなし）。本線2車線（MainApproach → WeaveZone lane1/2 → MainLane）
        Group(name="through", route="r_main", weight=1.0, depart_edge="MainApproach"),
        # 合流（オンランプ → 補助車線 WeaveZone lane0 → 本線 lane1 へ上がる）
        Group(name="merging", route="r_ramp", weight=1.0, target_lane=1, deadline_pos=457.0, depart_edge="OnRamp"),
        # 分流（本線 → 補助車線 WeaveZone lane0 へ降りる → 出口）。合流と逆向きに補助車線で交差＝織込み
        Group(
            name="diverging", route="r_exit", weight=1.0, target_lane=0, deadline_pos=457.0, depart_edge="MainApproach"
        ),
    ),
)

ENVIRONMENTS: dict[str, Environment] = {e.name: e for e in (DIVERGE, MERGE, STRAIGHT, WEAVE2, WEAVE)}
