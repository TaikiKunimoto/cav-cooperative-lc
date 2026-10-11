#!/usr/bin/env python3
"""TSK-143: 修正の前後を比べる 6 run スモーク（merge / weave / weave2 × Q3000 × f0.4 / 0.6 × seed1）のランナー。

出力は ``scripts/eval/out/tsk143/<label>/{raw,logs}`` に分け、ラベルで修正の段階（f1, f2-4, ...）を区別する。
run 名は run_tsk125 と同じ規則（``v2__<env>__Q<q>__f<f>__s<seed>``）なので、``audit_runs.py --baseline`` で
``out/tsk125``（修正前の既定条件）と直接比べられる。

使い方（リポジトリ直下から）::

    uv run python scripts/eval/run_tsk143.py --label f1              # 6 run（6 並列）
    uv run python scripts/eval/run_tsk143.py --label f1 --envs weave --fs 0.4
    uv run python scripts/eval/run_tsk143.py --label f1 --list       # ジョブ一覧だけ
    uv run python scripts/eval/audit_runs.py scripts/eval/out/tsk143/f1 --baseline scripts/eval/out/tsk125
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import os
from pathlib import Path
import shlex
import sys
import time

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent))

import run_sweep as rs  # noqa: E402
from run_eval import parse_seeds  # noqa: E402
from run_tsk125 import AM_DEFAULT, TJob  # noqa: E402

OUT_DIR = SCRIPT_DIR / "out" / "tsk143"
SMOKE_ENVS = ["merge", "weave", "weave2"]
SMOKE_QS = [3000]
SMOKE_FS = [0.4, 0.6]


def build_jobs(
    envs: list[str],
    qs: list[int],
    fs: list[float],
    seeds: list[int],
    am: float,
    set_spec: str | None,
    obstacle: str | None,
) -> list[TJob]:
    jobs: list[TJob] = []
    for env in envs:
        scenario = env if obstacle is None else f"{env}_obs"
        for q in qs:
            for f in fs:
                for s in seeds:
                    jobs.append(
                        TJob(
                            method="v2",
                            scenario=scenario,
                            env=env,
                            q=q,
                            f=f,
                            seed=s,
                            obstacle=obstacle,
                            policy="edf",
                            activation_margin=am,
                            set_spec=set_spec,
                        )
                    )
    return jobs


def main() -> None:
    ap = argparse.ArgumentParser(description="TSK-143 スモーク（修正の前後比較）のランナー")
    ap.add_argument("--label", required=True, help="出力ラベル（out/tsk143/<label>/）。修正の段階名を付ける")
    ap.add_argument("--envs", nargs="+", default=SMOKE_ENVS)
    ap.add_argument("--qs", nargs="+", type=int, default=SMOKE_QS)
    ap.add_argument("--fs", nargs="+", type=float, default=SMOKE_FS)
    ap.add_argument("--seeds", default="1", help="seed 範囲（例 1-2）")
    ap.add_argument("--am", type=float, default=AM_DEFAULT, help="活性化位置 [m]")
    ap.add_argument("--set", dest="set_spec", default=None, help="--set NAME=VALUE[,...] をそのまま渡す")
    ap.add_argument("--obstacle", default=None, help="--obstacle 'lane,pos,time' をそのまま渡す")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--force", action="store_true", help="既存の CSV があっても再実行する")
    ap.add_argument("--list", action="store_true", help="ジョブ一覧だけ表示")
    args = ap.parse_args()

    if "SUMO_HOME" not in os.environ:
        raise SystemExit("SUMO_HOME が未設定です。")

    out_dir = OUT_DIR / args.label
    raw_dir = out_dir / "raw"
    log_dir = out_dir / "logs"
    raw_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    # run_sweep.run_job はモジュール変数の RAW_DIR / LOG_DIR を参照するので差し替える
    rs.RAW_DIR = raw_dir
    rs.LOG_DIR = log_dir

    jobs = build_jobs(args.envs, args.qs, args.fs, parse_seeds(args.seeds), args.am, args.set_spec, args.obstacle)
    print(f"[tsk143] label={args.label} jobs={len(jobs)} workers={args.workers} out={out_dir}")
    if args.list:
        for j in jobs:
            print(f"  {j.name}: {' '.join(j.command())}")
        return

    meta = rs.collect_metadata()
    manifest = out_dir / "run_manifest.txt"
    with open(manifest, "a") as fh:
        fh.write(
            f"# {datetime.now().isoformat(timespec='seconds')} 起動: {shlex.join(sys.argv)} "
            f"git={meta['git_commit']} dirty={meta['git_dirty']} sumo={meta['sumo_version']}\n"
        )

    t0 = time.time()
    done = 0
    results: list[TJob] = []
    jobs.sort(key=lambda j: (j.env != "weave", -j.f))  # 重い weave・高 f を先に投げる
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(rs.run_job, j, args.force): j for j in jobs}
        for fut in as_completed(futures):
            j = fut.result()
            results.append(j)  # type: ignore[arg-type]
            done += 1
            dur = f"{j.duration_s:.0f}s" if j.duration_s is not None else "-"
            print(f"[{done}/{len(jobs)}] {j.status:7s} {j.name} ({dur})", flush=True)
            with open(manifest, "a") as fh:
                fh.write(
                    f"{datetime.now().isoformat(timespec='seconds')}\t{j.status}\t{j.name}\t{shlex.join(j.command())}\n"
                )
    elapsed = time.time() - t0
    bad = [j for j in results if j.status not in ("ok", "skipped")]
    print(f"\n[tsk143] 完了: ok/skip={len(results) - len(bad)} bad={len(bad)} elapsed={elapsed:.0f}s")
    for j in bad:
        print(f"  - {j.status}: {j.name} (log: {j.log})")


if __name__ == "__main__":
    main()
