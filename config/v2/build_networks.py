"""評価環境（config/v2/<env>/）の道路網を作る。

長さ・速度・車線幅を ``Geometry`` に集め、環境ごとに nod/edg/con を書いて netconvert で ``<env>.net.xml`` を作る。
区間（評価の対象の edge）の長さは edge の ``length`` で指定し、道路網の長さと ``TraCI/v2/environment.py`` の締切が
ちょうど一致するようにする（ジャンクションの形で区間が数 m 縮むことを防ぐ）。ランプの曲線などの形は見た目だけで、
走行には長さ・速度・接続だけが効く。

使い方::

    python3 config/v2/build_networks.py                 # 5 環境すべて
    python3 config/v2/build_networks.py weave           # 1 環境だけ
    python3 config/v2/build_networks.py weave --weave-length 500

長さを変えたら ``TraCI/v2/environment.py`` の区間長と締切も同じ値にする（実行時に道路網の実長と照合する）。
"""

import argparse
from dataclasses import dataclass, field
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Geometry:
    """5 環境に共通の値と、環境ごとの区間長 [m]・速度 [m/s]・車線幅 [m]。"""

    lane_width: float = 3.5  # 道路構造令 第 5 条第 4 項（第 1 種第 1〜3 級）
    main_speed: float = 27.78  # 100 km/h（道路構造令 第 13 条、第 1 種第 2 級）
    on_ramp_speed: float = 11.11  # 40 km/h（合流ランプ。加速車線で本線の速度まで加速する）
    exit_ramp_speed: float = 27.78  # 出口ランプは評価区間の外。減速車線を作らないので本線と同じにする
    approach: float = 500.0  # 区間の手前の本線（投入直後の並びを区間に持ち込まない）
    on_ramp: float = 300.0  # 合流ランプ
    downstream: float = 200.0  # 区間の後ろ（本線・出口とも）
    merge_accel: float = 240.0  # 加速車線長（本線 100 km/h の標準値、テーパを含む）
    weave: float = 457.0  # 片側織り込み区間（仮: 1,500 ft。日本の基準値を確かめて差し替える）
    weave2: float = 610.0  # 両側織り込み区間（2,000 ft）
    diverge: float = 1000.0  # 分流の区間（締切＝分岐点）
    straight: float = 1000.0  # 封鎖付き直線

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if value <= 0:
                raise ValueError(f"Geometry.{name} は正の値が必要です（受け取り: {value}）")


class RampShape:
    """ランプの見た目の形（走行には効かない）。接続する車線の位置まで本線と平行に寄せてから接続し、ジャンクションを小さく保つ。

    spreadType が right（既定）の edge は形の線が左端で、車線は右へ並ぶ。merge・weave・diverge の区間（3 車線）の
    lane0 の左端は形の線から 2 車線分（2·車線幅）右にあるので、1 車線のランプの形の線をそこへ合わせる。
    spreadType が center の weave2 は形の線が車線の中心なので、lane0・lane3 の中心（±1.5·車線幅）へ合わせる。
    """

    # 本線と平行な位置から接続点へ寄る割合（x = −100, −87.5, …, −25 m。三次の smoothstep）
    SMOOTH = (0.0, 0.074, 0.259, 0.5, 0.741, 0.926, 1.0)

    @classmethod
    def inward(cls, start_x: float, y_far: float, y_near: float) -> list[tuple[float, float]]:
        """合流ランプ: y_far を本線と平行に走り、x = −100〜−25 m で y_near へ寄ってから平行に接続点（x = 0）へ。"""
        xs = (-100.0, -87.5, -75.0, -62.5, -50.0, -37.5, -25.0)
        pts = [(x, y_far + (y_near - y_far) * k) for x, k in zip(xs, cls.SMOOTH, strict=True)]
        return [(start_x, y_far), *pts, (0.0, y_near)]

    @classmethod
    def outward(cls, x0: float, end_x: float, y_near: float, y_far: float) -> list[tuple[float, float]]:
        """出口ランプ: 分岐点 x0 から 20 m で y_near から y_far へ離れ、その後は本線と平行に end_x まで。"""
        pts = [(x0 + 20.0 * i / 6, y_near + (y_far - y_near) * k) for i, k in enumerate(cls.SMOOTH)]
        return [*pts, (end_x, y_far)]


@dataclass
class Network:
    """1 環境の nod/edg/con の中身（XML の行）を集め、netconvert で net を作る。環境ごとの形は classmethod で作る。"""

    name: str
    nodes: list[str] = field(default_factory=list)
    edges: list[str] = field(default_factory=list)
    connections: list[str] = field(default_factory=list)

    def node(self, node_id: str, x: float, y: float, kind: str = "priority") -> None:
        self.nodes.append(f'    <node id="{node_id}" x="{x:.2f}" y="{y:.2f}" type="{kind}"/>')

    def edge(
        self,
        edge_id: str,
        ends: tuple[str, str],
        lanes: int,
        speed: float,
        length: float,
        priority: int,
        shape: list[tuple[float, float]] | None = None,
        spread: str | None = None,
    ) -> None:
        attrs = (
            f'id="{edge_id}" from="{ends[0]}" to="{ends[1]}" numLanes="{lanes}" speed="{speed:.2f}" '
            f'length="{length:.2f}" priority="{priority}"'
        )
        if spread is not None:
            attrs += f' spreadType="{spread}"'
        if shape is not None:
            attrs += ' shape="' + " ".join(f"{x:.2f},{y:.2f}" for x, y in shape) + '"'
        self.edges.append(f"    <edge {attrs}/>")

    def connect(self, frm: str, from_lane: int, to: str, to_lane: int) -> None:
        self.connections.append(f'    <connection from="{frm}" to="{to}" fromLane="{from_lane}" toLane="{to_lane}"/>')

    def build(self, geo: Geometry) -> Path:
        """nod/edg/con を書き、netconvert で ``<name>.net.xml`` を作ってそのパスを返す。"""
        out = HERE / self.name
        header = f"<!-- build_networks.py が生成する。手で直さず build_networks.py を直す（{self.name}） -->\n"
        (out / f"{self.name}.nod.xml").write_text(header + "<nodes>\n" + "\n".join(self.nodes) + "\n</nodes>\n")
        (out / f"{self.name}.edg.xml").write_text(header + "<edges>\n" + "\n".join(self.edges) + "\n</edges>\n")
        cmd = [Network.netconvert(), "--node-files", f"{self.name}.nod.xml", "--edge-files", f"{self.name}.edg.xml"]
        con = out / f"{self.name}.con.xml"
        if self.connections:
            con.write_text(header + "<connections>\n" + "\n".join(self.connections) + "\n</connections>\n")
            cmd += ["--connection-files", con.name]
        elif con.exists():
            con.unlink()
        cmd += ["--default.lanewidth", f"{geo.lane_width:.2f}", "--output-file", f"{self.name}.net.xml"]
        subprocess.run(cmd, cwd=out, check=True)
        return out / f"{self.name}.net.xml"

    @staticmethod
    def netconvert() -> str:
        path = shutil.which("netconvert")
        if path is None:
            raise FileNotFoundError("netconvert が見つかりません。SUMO の bin を PATH に入れてください")
        return path

    @classmethod
    def diverge(cls, geo: Geometry) -> "Network":
        """単一分流: 本線 3 車線の区間の終わりで lane0 が出口と本線に分かれる（締切＝分岐点）。"""
        n = cls("diverge")
        n.node("MainStart", -geo.diverge, 0.0, "dead_end")
        n.node("DivergeStart", 0.0, 0.0)
        n.node("End", geo.downstream, 0.0, "dead_end")
        lane0 = -2 * geo.lane_width  # 区間の lane0 の左端（形の線から 2 車線分右）
        n.node("ExitEnd", geo.downstream, lane0 - 5.0, "dead_end")
        n.edge("DivergeZone", ("MainStart", "DivergeStart"), 3, geo.main_speed, geo.diverge, 10)
        n.edge("MainLane", ("DivergeStart", "End"), 3, geo.main_speed, geo.downstream, 10)
        exit_shape = RampShape.outward(0.0, geo.downstream, lane0, lane0 - 5.0)
        n.edge("ExitRamp", ("DivergeStart", "ExitEnd"), 1, geo.exit_ramp_speed, geo.downstream, 1, shape=exit_shape)
        n.connect("DivergeZone", 0, "MainLane", 0)
        n.connect("DivergeZone", 0, "ExitRamp", 0)
        n.connect("DivergeZone", 1, "MainLane", 1)
        n.connect("DivergeZone", 2, "MainLane", 2)
        return n

    @classmethod
    def merge(cls, geo: Geometry) -> "Network":
        """単一合流: 合流ランプが加速車線（区間の lane0）になり、加速車線の終わり（締切）で無くなる。"""
        n = cls("merge")
        n.node("MainStart", -geo.approach, 0.0, "dead_end")
        lane0 = -2 * geo.lane_width  # 区間の lane0（加速車線）の左端
        n.node("RampStart", -geo.on_ramp, lane0 - 8.0, "dead_end")
        n.node("MergeNode", 0.0, 0.0)
        n.node("AccelEnd", geo.merge_accel, 0.0)
        n.node("End", geo.merge_accel + geo.downstream, 0.0, "dead_end")
        n.edge("MainApproach", ("MainStart", "MergeNode"), 2, geo.main_speed, geo.approach, 10)
        ramp_shape = RampShape.inward(-geo.on_ramp, lane0 - 8.0, lane0)
        n.edge("OnRamp", ("RampStart", "MergeNode"), 1, geo.on_ramp_speed, geo.on_ramp, 1, shape=ramp_shape)
        n.edge("MergeZone", ("MergeNode", "AccelEnd"), 3, geo.main_speed, geo.merge_accel, 10)
        n.edge("MainLane", ("AccelEnd", "End"), 2, geo.main_speed, geo.downstream, 10)
        n.connect("MainApproach", 0, "MergeZone", 1)
        n.connect("MainApproach", 1, "MergeZone", 2)
        n.connect("OnRamp", 0, "MergeZone", 0)
        n.connect("MergeZone", 1, "MainLane", 0)
        n.connect("MergeZone", 2, "MainLane", 1)  # 加速車線（lane0）は接続しない＝区間の終わりで無くなる
        return n

    @classmethod
    def weave(cls, geo: Geometry) -> "Network":
        """片側織り込み: 合流ランプの車線が補助車線（区間の lane0）になり、区間の終わり（締切）で出口へ抜ける。"""
        n = cls("weave")
        end = geo.weave + geo.downstream
        n.node("MainStart", -geo.approach, 0.0, "dead_end")
        lane0 = -2 * geo.lane_width  # 区間の lane0（補助車線）の左端
        n.node("OnRampStart", -geo.on_ramp, lane0 - 8.0, "dead_end")
        n.node("WeaveStart", 0.0, 0.0)
        n.node("WeaveEnd", geo.weave, 0.0)
        n.node("End", end, 0.0, "dead_end")
        n.node("ExitEnd", end, lane0 - 5.0, "dead_end")
        n.edge("MainApproach", ("MainStart", "WeaveStart"), 2, geo.main_speed, geo.approach, 10)
        ramp_shape = RampShape.inward(-geo.on_ramp, lane0 - 8.0, lane0)
        n.edge("OnRamp", ("OnRampStart", "WeaveStart"), 1, geo.on_ramp_speed, geo.on_ramp, 1, shape=ramp_shape)
        n.edge("WeaveZone", ("WeaveStart", "WeaveEnd"), 3, geo.main_speed, geo.weave, 10)
        n.edge("MainLane", ("WeaveEnd", "End"), 2, geo.main_speed, geo.downstream, 10)
        exit_shape = RampShape.outward(geo.weave, end, lane0, lane0 - 5.0)
        n.edge("ExitRamp", ("WeaveEnd", "ExitEnd"), 1, geo.exit_ramp_speed, geo.downstream, 1, shape=exit_shape)
        n.connect("MainApproach", 0, "WeaveZone", 1)
        n.connect("MainApproach", 1, "WeaveZone", 2)
        n.connect("OnRamp", 0, "WeaveZone", 0)
        n.connect("WeaveZone", 0, "ExitRamp", 0)
        n.connect("WeaveZone", 1, "MainLane", 0)
        n.connect("WeaveZone", 2, "MainLane", 1)
        return n

    @classmethod
    def weave2(cls, geo: Geometry) -> "Network":
        """両側織り込み: 上下の合流ランプが補助車線（lane0・lane3）になり、区間の終わり（締切）で上下の出口へ抜ける。"""
        n = cls("weave2")
        end = geo.weave2 + geo.downstream
        side = 1.5 * geo.lane_width  # 4 車線を中央そろえにしたときの lane0・lane3 の中心
        far = side + 5.0  # 出口ランプが本線と平行に走る位置
        n.node("MainStart", -geo.approach, 0.0, "dead_end")
        n.node("WeaveStart", 0.0, 0.0)
        n.node("WeaveEnd", geo.weave2, 0.0)
        n.node("End", end, 0.0, "dead_end")
        n.node("OnRampBottomStart", -geo.on_ramp, -20.0, "dead_end")
        n.node("OnRampTopStart", -geo.on_ramp, 20.0, "dead_end")
        n.node("OffRampBottomEnd", end, -far, "dead_end")
        n.node("OffRampTopEnd", end, far, "dead_end")
        c = "center"
        n.edge("MainApproach", ("MainStart", "WeaveStart"), 2, geo.main_speed, geo.approach, 10, spread=c)
        n.edge("WeaveZone", ("WeaveStart", "WeaveEnd"), 4, geo.main_speed, geo.weave2, 10, spread=c)
        n.edge("MainLane", ("WeaveEnd", "End"), 2, geo.main_speed, geo.downstream, 10, spread=c)
        for name, sign in (("Bottom", -1.0), ("Top", 1.0)):
            ramp_in = RampShape.inward(-geo.on_ramp, 20.0 * sign, side * sign)
            ramp_out = RampShape.outward(geo.weave2, end, side * sign, far * sign)
            n.edge(
                f"OnRamp{name}", (f"OnRamp{name}Start", "WeaveStart"), 1, geo.on_ramp_speed, geo.on_ramp, 1,
                shape=ramp_in, spread=c,
            )  # fmt: skip
            n.edge(
                f"OffRamp{name}", ("WeaveEnd", f"OffRamp{name}End"), 1, geo.exit_ramp_speed, geo.downstream, 1,
                shape=ramp_out, spread=c,
            )  # fmt: skip
        n.connect("MainApproach", 0, "WeaveZone", 1)
        n.connect("MainApproach", 1, "WeaveZone", 2)
        n.connect("OnRampBottom", 0, "WeaveZone", 0)
        n.connect("OnRampTop", 0, "WeaveZone", 3)
        n.connect("WeaveZone", 1, "MainLane", 0)
        n.connect("WeaveZone", 2, "MainLane", 1)
        n.connect("WeaveZone", 0, "OffRampBottom", 0)
        n.connect("WeaveZone", 3, "OffRampTop", 0)
        return n

    @classmethod
    def straight(cls, geo: Geometry) -> "Network":
        """封鎖付き直線: 3 車線の 1 本道（障害物は実行時に --obstacle で作る）。"""
        n = cls("straight")
        n.node("Start", 0.0, 0.0, "dead_end")
        n.node("End", geo.straight, 0.0, "dead_end")
        n.edge("Road", ("Start", "End"), 3, geo.main_speed, geo.straight, 10)
        return n

    @classmethod
    def of(cls, name: str, geo: Geometry) -> "Network":
        builders = {"diverge": cls.diverge, "merge": cls.merge, "straight": cls.straight, "weave": cls.weave}
        builders["weave2"] = cls.weave2
        if name not in builders:
            raise ValueError(f"不明な環境 '{name}'（利用可能: {', '.join(builders)}）")
        return builders[name](geo)


ENV_NAMES = ("diverge", "merge", "straight", "weave", "weave2")


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description="評価環境の道路網を作る")
    parser.add_argument("envs", nargs="*", help=f"作る環境（{' / '.join(ENV_NAMES)}。省略で 5 環境すべて）")
    parser.add_argument("--weave-length", type=float, default=Geometry.weave, help="片側織り込み区間の長さ [m]")
    args = parser.parse_args(argv)
    geo = Geometry(weave=args.weave_length)
    for name in args.envs or ENV_NAMES:
        net = Network.of(name, geo).build(geo)
        print(f"{name}: {net.relative_to(HERE)} generated")


if __name__ == "__main__":
    main(sys.argv[1:])
