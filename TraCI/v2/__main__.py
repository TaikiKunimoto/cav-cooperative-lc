"""v2 エントリポイント（EDF統一調停・自己完結パッケージ）。

実行: ``cd TraCI && uv run python -m v2 <seed> <inflow> <mlc_ratio> [--env NAME] [--nogui]``
- ``inflow``    : 総流入量 Q [veh/h]
- ``mlc_ratio`` : 必須LC車の比率 f（0..1）
- ``--env``     : 評価環境名（既定 diverge＝分流D）。環境を変えると net・必須LC仕様が切り替わる。

環境（形状）と負荷（Q・f）を分離しており、env を変えるだけで同じ Q,f を別シナリオに適用できる。
結果は simulationStatistics/statistics/v2/ に出力。
"""

import optparse
import os
import random
import sys

from simulationStatistics.simulation_statistics import SimulationStatistics

if "SUMO_HOME" in os.environ:
    tools = os.path.join(os.environ["SUMO_HOME"], "tools")
    sys.path.append(tools)
else:
    sys.exit("please declare environment variable 'SUMO_HOME'")

from sumolib import checkBinary
import traci

import v2.constants as _constants


def _apply_constant_overrides(argv: list[str]) -> dict[str, float]:
    """``--set NAME=VALUE[,NAME=VALUE...]`` を v2.constants に適用する（要素の除去・感度分析用）。

    他モジュールは ``from v2.constants import X`` で import 時に値を束縛するため、それらを import する前に呼ぶ。
    constants.py 内で他の定数から導出される値（SWAP_WINDOW など）は再計算しないので、必要なら直接指定する。
    """
    overrides: dict[str, float] = {}
    i = 0
    while i < len(argv):
        arg = argv[i]
        spec: str | None = None
        if arg == "--set" and i + 1 < len(argv):
            spec = argv[i + 1]
            i += 1
        elif arg.startswith("--set="):
            spec = arg[len("--set=") :]
        if spec:
            for item in spec.split(","):
                name, sep, value = item.partition("=")
                name = name.strip()
                if not sep or name.startswith("_") or not name.isupper() or not hasattr(_constants, name):
                    sys.exit(f"--set: 不明な定数 '{name}'（v2/constants.py の大文字名を NAME=VALUE で指定）")
                overrides[name] = float(value)
                setattr(_constants, name, float(value))
        i += 1
    return overrides


CONSTANT_OVERRIDES = _apply_constant_overrides(sys.argv[1:])

from v2.constants import ACTIVATION_MARGIN  # noqa: E402  上書き後に束縛する
from v2.environment import ENVIRONMENTS  # noqa: E402
from v2.obstacle import Obstacle  # noqa: E402
from v2.policy import Policy  # noqa: E402
from v2.simulation import OUTPUT_DIR, V2Simulation  # noqa: E402

SIMULATION_TIME: float = 600.0  # シミュレーション時間[s]


def _start_sim(sumo_binary: str, sumocfg: str) -> None:
    # --time-to-teleport -1: 全制御を traci で行うため SUMO の jam-teleport を無効化
    # （障害物=停止車両が除去されない／stuck 車は running として残り失敗信号が明確になる）
    traci.start([sumo_binary, "-c", sumocfg, "--time-to-teleport", "-1"])
    print("Simulation started")


def _get_options() -> tuple[optparse.Values, list[str]]:
    parser = optparse.OptionParser(
        usage="python -m v2 <seed> <inflow> <mlc_ratio> [--env NAME] [--obstacle L,P,T] [--policy P] [--nogui]"
    )
    parser.add_option("--env", dest="env", default="diverge", help="evaluation environment name (default: diverge)")
    parser.add_option(
        "--obstacle", dest="obstacle", default=None, help="dynamic obstacle as 'lane,pos,time' (突発障害物)"
    )
    parser.add_option(
        "--policy",
        dest="policy",
        type="choice",
        choices=[p.value for p in Policy],
        default=Policy.EDF.value,
        help="arbitration policy: edf=提案 / none=優先度なし / off=非協調(LC2013) (default: edf)",
    )
    parser.add_option(
        "--activation-margin",
        dest="activation_margin",
        type="float",
        default=ACTIVATION_MARGIN,
        help=f"要求の活性化位置 [m]（締切Dの何m手前で活性化するか。既定 {ACTIVATION_MARGIN:.0f}。柱B-2の猶予距離比較用）",
    )
    parser.add_option(
        "--set",
        dest="set_spec",
        default=None,
        help="定数の上書き NAME=VALUE[,NAME=VALUE]（起動時に v2.constants へ適用済み。例 HOLD_MARGIN=0,COOP_YIELD=0）",
    )
    parser.add_option("--nogui", action="store_true", default=False, help="run the commandline version of sumo")
    return parser.parse_args()


def _create_file_name(env_name: str, total_inflow: float, mlc_ratio: float, seed: str, policy: Policy) -> str:
    """単体実行時の出力名（一括ラン時は EVAL_OUTPUT_NAME が優先）。edf 以外は policy を含めて区別する。"""
    method = "v2" if policy is Policy.EDF else f"v2-{policy.value}"
    return f"{method}_{env_name}_inflow{int(total_inflow)}_mlc{mlc_ratio}_seed{seed}"


if __name__ == "__main__":
    options, positional = _get_options()
    usage = "usage: python -m v2 <seed> <inflow> <mlc_ratio> [--env NAME] [--obstacle L,P,T] [--policy P] [--nogui]"
    if len(positional) < 3:
        sys.exit(f"位置引数が不足しています（必要3: seed inflow mlc_ratio／受け取り {len(positional)} 個）\n{usage}")
    seed = positional[0]  # 乱数シード
    random.seed(seed)
    try:
        total_inflow = float(positional[1])  # 総流入量 Q [veh/h]
        mlc_ratio = float(positional[2])  # 必須LC車の比率 f（0..1）
    except ValueError:
        sys.exit(
            f"inflow と mlc_ratio は数値で指定してください（受け取り: {positional[1]!r}, {positional[2]!r}）\n{usage}"
        )

    env = ENVIRONMENTS.get(options.env)
    if env is None:
        sys.exit(f"不明な --env '{options.env}'（利用可能: {', '.join(ENVIRONMENTS)}）")

    policy = Policy(options.policy)
    if options.activation_margin <= 0:
        sys.exit(f"--activation-margin は正の値で指定してください（受け取り: {options.activation_margin}）")

    filename = _create_file_name(env.name, total_inflow, mlc_ratio, seed, policy)
    if options.activation_margin != ACTIVATION_MARGIN:
        filename += f"_am{int(options.activation_margin)}"
    if CONSTANT_OVERRIDES:
        filename += "_set-" + "-".join(f"{k}{v:g}" for k, v in CONSTANT_OVERRIDES.items())
        print(f"[v2] constant overrides: {CONSTANT_OVERRIDES}")
    # track_deadline_achievement=True: 提案手法は締切達成率（必須LC完了率）を中核指標としてCSV出力する
    stats = SimulationStatistics(filename=filename, output_dir=OUTPUT_DIR, track_deadline_achievement=True)

    obstacle = Obstacle.from_spec(options.obstacle) if options.obstacle is not None else None

    sumo_binary = checkBinary("sumo" if options.nogui else "sumo-gui")
    _start_sim(sumo_binary, env.sumocfg)
    sim = V2Simulation(
        simulation_time=SIMULATION_TIME,
        env=env,
        total_inflow=total_inflow,
        mlc_ratio=mlc_ratio,
        seed=seed,
        obstacle=obstacle,
        policy=policy,
        activation_margin=options.activation_margin,
    )
    sim.run(stats)
