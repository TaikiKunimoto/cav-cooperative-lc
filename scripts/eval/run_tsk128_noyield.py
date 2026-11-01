#!/usr/bin/env python3
"""TSK-128: 提供車の割当と協調減速を外し、要求の処理順を縦位置で決める変種を、本番スイートと同じ条件で回す。

v2 に ``--set COOP_YIELD=0,POSITION_ORDER=1`` を付け、run_sweep.py の proposed スイートと同じ 385 条件（4 環境 ×
Q1500〜4000 × f0.2・0.4・0.6 × seed1〜5 ＋ 封鎖付き straight × Q1500〜3500 × seed1〜5）で回す。方式名（run 名の先頭）は
``v2-noyield-pos``。run_sweep.py の Job・run_job・write_manifest をそのまま使うので、出力先は ``scripts/eval/out/raw``・
``manifest.json`` になる（提案の run と分けるため、別の worktree で回す）。

使い方（リポジトリ直下から）::

    uv run python scripts/eval/run_tsk128_noyield.py --quick --workers 5   # 高負荷の 5 条件（seed 1）だけ
    uv run python scripts/eval/run_tsk128_noyield.py --workers 12          # 385 条件（済んだ run は飛ばす）
    uv run python scripts/eval/run_tsk128_noyield.py --dry-run
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import os
from pathlib import Path
import shlex
import sys
import time

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import run_sweep as rs  # noqa: E402

METHOD = "v2-noyield-pos"
SET_SPEC = "COOP_YIELD=0,POSITION_ORDER=1"
SUITE = "tsk128-noyield-pos"
QUICK_Q_MLC = 4000  # --quick: 4 環境は Q4000・f0.6、封鎖付き straight は Q3500（いずれも seed 1）
QUICK_F_MLC = 0.6
QUICK_Q_OBSTACLE = 3500


@dataclass
class SetJob(rs.Job):
    """run_sweep.Job に ``--set`` を足したもの。run 名は run_sweep.Job と同じ形（方式名だけが違う）。"""

    set_spec: str | None = None

    def command(self) -> list[str]:
        cmd = super().command()
        if self.set_spec:
            cmd += ["--set", self.set_spec]
        return cmd


def build_jobs(quick: bool) -> list[SetJob]:
    """proposed スイートの 385 条件をそのまま写し、方式名と ``--set`` だけを替える（--quick は高負荷の 5 条件）。"""
    jobs: list[SetJob] = []
    for j in rs.build_jobs("proposed", quick=False):
        if quick:
            mlc_corner = j.scenario != "straight_obs" and j.q == QUICK_Q_MLC and j.f == QUICK_F_MLC
            obstacle_corner = j.scenario == "straight_obs" and j.q == QUICK_Q_OBSTACLE
            if j.seed != 1 or not (mlc_corner or obstacle_corner):
                continue
        jobs.append(
            SetJob(
                method=METHOD,
                scenario=j.scenario,
                env=j.env,
                q=j.q,
                f=j.f,
                seed=j.seed,
                obstacle=j.obstacle,
                policy=j.policy,
                set_spec=SET_SPEC,
            )
        )
    return jobs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    ap.add_argument("--quick", action="store_true", help="高負荷の 5 条件（seed 1）だけ")
    ap.add_argument("--force", action="store_true", help="既存 CSV を無視して再実行")
    ap.add_argument("--dry-run", action="store_true", help="ジョブ一覧だけ表示して終了")
    args = ap.parse_args()

    if "SUMO_HOME" not in os.environ:
        raise SystemExit("SUMO_HOME が未設定です。SUMO を有効化してから実行してください。")
    jobs = build_jobs(args.quick)
    print(f"[{SUITE}] jobs={len(jobs)} workers={args.workers} quick={args.quick} raw={rs.RAW_DIR}", flush=True)
    print(f"[{SUITE}] example: {shlex.join(jobs[0].command())}", flush=True)
    if args.dry_run:
        for j in jobs:
            print(f"  {j.name}")
        return

    rs.RAW_DIR.mkdir(parents=True, exist_ok=True)
    rs.LOG_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    results: list[rs.Job] = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(rs.run_job, j, args.force) for j in jobs]
        for i, fut in enumerate(as_completed(futs), 1):
            j = fut.result()
            results.append(j)
            dur = f"{j.duration_s:.1f}s" if j.duration_s is not None else "-"
            print(f"[{i}/{len(jobs)}] {j.status:7s} {j.name} ({dur})", flush=True)

    elapsed = time.time() - t0
    rs.write_manifest(results, suite=SUITE, quick=args.quick, elapsed=elapsed)
    bad = [j for j in results if j.status not in ("ok", "skipped")]
    print(f"[{SUITE}] DONE ok={len(results) - len(bad)} bad={len(bad)} elapsed={elapsed:.0f}s", flush=True)
    for j in bad:
        print(f"  {j.status}: {j.name} log={j.log}", flush=True)


if __name__ == "__main__":
    main()
