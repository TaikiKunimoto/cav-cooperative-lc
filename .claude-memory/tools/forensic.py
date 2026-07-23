# ruff: noqa
"""失敗要因の forensic 再現ランナー（リポジトリ非改変・monkeypatch 計装）。

usage: cd TraCI && uv run python <this> <seed> <inflow> <mlc_ratio> --env NAME [--obstacle L,P,T] [--outdir DIR]

分類（活性化済み・非回避の必須LC操作ごと）:
  OK                 … completed_in_time
  COLLIDED           … 車両が衝突イベントに関与（SUMO により teleport/除去され得る）
  ARRIVED_UNRECORDED … 完了記録なしだが車両は到着（=物理的には成功。計測アーティファクト疑い）
  STUCK_AT_WALL      … 終了時 running、締切位置の直前(<20m)で長時間停止(>=20s)
  STUCK_QUEUE        … 終了時 running、それ以外の場所で長時間停止
  CENSORED           … 終了時 running、停止履歴なし＝進行中に打ち切り（測定プロトコル起因）
  OTHER              … 上記以外
"""

import json
import optparse
import os
import random
import sys

sys.path.insert(0, os.getcwd())  # TraCI ディレクトリ

OUTDIR_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "forensic_out")

options = None  # set in main


def main() -> None:
    global options
    parser = optparse.OptionParser()
    parser.add_option("--env", dest="env", default="diverge")
    parser.add_option("--obstacle", dest="obstacle", default=None)
    parser.add_option("--outdir", dest="outdir", default=OUTDIR_DEFAULT)
    options, positional = parser.parse_args()
    seed, inflow, mlc = positional[0], float(positional[1]), float(positional[2])

    tag = f"{options.env}_Q{int(inflow)}_f{mlc}_s{seed}"
    if options.obstacle:
        tag += f"_obs{options.obstacle.replace(',', '-')}"
    outdir = os.path.join(options.outdir, tag)
    os.makedirs(outdir, exist_ok=True)
    os.environ["EVAL_OUTPUT_DIR"] = outdir
    os.environ["EVAL_OUTPUT_NAME"] = "result"

    random.seed(seed)  # __main__ と同一（文字列 seed）

    from sumolib import checkBinary
    import traci

    from simulationStatistics.simulation_statistics import SimulationStatistics
    from utils import traci_wrapper
    from v2.environment import ENVIRONMENTS
    from v2.layer1.rsu import RSU
    from v2.layer2.pair_executor import Layer2
    from v2.obstacle import Obstacle
    from v2.simulation import V2Simulation
    from v2.constants import MIN_GAP, MAX_DECEL

    env = ENVIRONMENTS[options.env]
    D_BY_NONE = 1e18

    # ---------- 計装 ----------
    class Probe:
        def __init__(self) -> None:
            self.reg: dict[str, dict] = {}  # vid -> record
            self.vehs: dict[str, object] = {}  # vid -> V2CAV 参照（pop 後も保持）
            self.timeline = open(os.path.join(outdir, "timeline.jsonl"), "w")
            self.insertion_last: dict[str, dict] = {}
            self.insertion_samples: list[dict] = []
            self.last_dump = -1.0
            self.last_ins_sample = -1.0

        def rec(self, vid: str) -> dict:
            if vid not in self.reg:
                self.reg[vid] = {
                    "stuck_start": None,
                    "stuck_pos": None,
                    "stuck_max": 0.0,
                    "stuck_max_pos": None,
                    "stuck_now": 0.0,
                    "wall_stuck": 0.0,
                }
            return self.reg[vid]

        def step(self, sim: V2Simulation) -> None:
            t = traci.simulation.getTime()
            for veh in sim.vehicles:
                self.vehs.setdefault(veh.id, veh)
                if veh.departure_time is None or veh.road is None:
                    continue
                r = self.rec(veh.id)
                if veh.speed < 0.3:
                    if r["stuck_start"] is None:
                        r["stuck_start"] = t
                        r["stuck_pos"] = (veh.road, veh.lane, round(veh.lane_pos or -1, 1))
                    streak = t - r["stuck_start"]
                    r["stuck_now"] = streak
                    if streak > r["stuck_max"]:
                        r["stuck_max"] = streak
                        r["stuck_max_pos"] = r["stuck_pos"]
                else:
                    r["stuck_start"] = None
                    r["stuck_now"] = 0.0
            # 10s ごとに本線スナップショットを dump
            if t - self.last_dump >= 10.0 - 1e-9:
                self.last_dump = t
                rows = []
                for veh in sim.vehicles:
                    if veh.departure_time is None or veh.road is None:
                        continue
                    if veh.road != env.mainlane_edge:
                        continue
                    op = veh.active_operation()
                    rows.append(
                        [
                            veh.id,
                            veh.lane,
                            round(veh.lane_pos or -1, 1),
                            round(veh.speed, 2),
                            veh.status.name if hasattr(veh.status, "name") else str(veh.status),
                            (op.target_lane if op else None),
                            veh.receiving_from_id,
                            veh.providing_to_id,
                            round(self.rec(veh.id)["stuck_now"], 1),
                        ]
                    )
                self.timeline.write(json.dumps({"t": t, "veh": rows}) + "\n")
            # 停止中の要求車の挿入チェック詳細を 5s ごとに採取
            if t - self.last_ins_sample >= 5.0 - 1e-9:
                self.last_ins_sample = t
                for veh in sim.vehicles:
                    vid = veh.id
                    r = self.reg.get(vid)
                    if not r or r["stuck_now"] < 5.0:
                        continue
                    if vid in self.insertion_last:
                        d = dict(self.insertion_last[vid])
                        d["t"] = t
                        d["stuck"] = round(r["stuck_now"], 1)
                        self.insertion_samples.append(d)

    probe = Probe()

    # _add_vehicle は毎step末に呼ばれる → probe の駆動点
    orig_add = V2Simulation._add_vehicle

    def patched_add(self: V2Simulation) -> None:
        orig_add(self)
        probe.step(self)

    V2Simulation._add_vehicle = patched_add  # type: ignore[method-assign]

    # 挿入安全チェックの詳細記録
    orig_ins = Layer2.__dict__["_insertion_safe_live"].__func__

    def patched_ins(veh_id: str, ego_speed: float, going_right: bool) -> bool:
        res = orig_ins(veh_id, ego_speed, going_right)
        a = abs(MAX_DECEL)
        lat = 1 if going_right else 0
        detail = {
            "vid": veh_id,
            "ego_v": round(ego_speed, 2),
            "right": going_right,
            "ok": res,
            "back": [],
            "front": [],
        }
        for nid, dist in traci_wrapper.get_veh_neighbors(veh_id, lat):
            fv = traci_wrapper.get_veh_speed(nid)
            req = MIN_GAP + max(0.0, (fv**2 - ego_speed**2) / (2 * a))
            detail["back"].append([nid, round(dist, 2), round(fv, 2), round(req, 2)])
        for nid, dist in traci_wrapper.get_veh_neighbors(veh_id, lat | 2):
            lv = traci_wrapper.get_veh_speed(nid)
            req = MIN_GAP + max(0.0, (ego_speed**2 - lv**2) / (2 * a))
            detail["front"].append([nid, round(dist, 2), round(lv, 2), round(req, 2)])
        probe.insertion_last[veh_id] = detail
        return res

    Layer2._insertion_safe_live = staticmethod(patched_ins)  # type: ignore[method-assign]

    # ---------- 実行 ----------
    stats = SimulationStatistics(filename="forensic", output_dir=outdir, track_deadline_achievement=True)
    obstacle = Obstacle.from_spec(options.obstacle) if options.obstacle else None
    traci.start([checkBinary("sumo"), "-c", env.sumocfg, "--time-to-teleport", "-1"])
    sim = V2Simulation(
        simulation_time=600.0, env=env, total_inflow=inflow, mlc_ratio=mlc, seed=seed, obstacle=obstacle
    )
    sim.run(stats)
    probe.timeline.close()

    # ---------- 分類 ----------
    collided: set[str] = set()
    for _, ids in sim.collision_history:
        collided.update(ids)
    arrived = set(sim.exit_vehicles)
    canceled = set(sim.canceled_vehicles)

    rows = []
    counts: dict[str, int] = {}
    for vid, veh in probe.vehs.items():
        ops = [op for op in veh.operations if not op.is_avoidance and op.activated]
        for op in ops:
            if op.completed_in_time:
                cls = "OK"
            elif vid in collided:
                cls = "COLLIDED"
            elif vid in arrived:
                cls = "ARRIVED_UNRECORDED"
            elif vid in canceled:
                cls = "CANCELED?!"
            else:
                r = probe.reg.get(vid, {})
                d = op.deadline_pos
                sp = r.get("stuck_max_pos")
                if r.get("stuck_max", 0) >= 20 and sp and sp[0] == env.mainlane_edge and sp[2] >= d - 20:
                    cls = "STUCK_AT_WALL"
                elif r.get("stuck_max", 0) >= 20:
                    cls = "STUCK_QUEUE"
                else:
                    cls = "CENSORED"
            counts[cls] = counts.get(cls, 0) + 1
            if cls != "OK":
                r = probe.reg.get(vid, {})
                rows.append(
                    {
                        "vid": vid,
                        "cls": cls,
                        "route": veh.route,
                        "target": op.target_lane,
                        "D": op.deadline_pos,
                        "act_t": op.activation_time,
                        "final": [
                            veh.road,
                            veh.lane,
                            round(veh.lane_pos, 1) if veh.lane_pos is not None else None,
                            round(veh.speed, 2),
                        ],
                        "depart_t": veh.departure_time,
                        "stuck_max": round(r.get("stuck_max", 0), 1),
                        "stuck_max_pos": r.get("stuck_max_pos"),
                    }
                )

    with open(os.path.join(outdir, "classification.json"), "w") as f:
        json.dump({"tag": tag, "counts": counts, "failures": rows}, f, ensure_ascii=False, indent=1)
    with open(os.path.join(outdir, "insertion_samples.jsonl"), "w") as f:
        for s in probe.insertion_samples:
            f.write(json.dumps(s) + "\n")

    print("\n=== FORENSIC SUMMARY ===")
    print("tag:", tag)
    print("counts:", counts)
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))


if __name__ == "__main__":
    main()
