#!/usr/bin/env python3
"""柱B-2 実験2: 活性化位置（猶予距離）の限界比較ランナー。

edf / off-late を weave, weave2 × Q3000 × f{0.4,0.6} × seed1-3 × margin{400,300,200,100} で回す。
メインの manifest.json / raw/ を汚さないよう、出力は out/ablation_b2/raw_margin/ に分離し、
run 名にマージンを含める（``v2__weave__Q3000__f0.4__s1__am300``）。レジューム可（CSV存在でスキップ）。

使い方（リポジトリ直下）::

    uv run python scripts/eval/run_b2_margin.py --workers 7
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
TRACI_DIR = REPO_ROOT / "TraCI"
OUT_DIR = SCRIPT_DIR / "out" / "ablation_b2"
RAW_DIR = OUT_DIR / "raw_margin"
LOG_DIR = OUT_DIR / "logs_margin"
MANIFEST_TXT = OUT_DIR / "run_manifest_b2.txt"
PER_RUN_TIMEOUT_S = 1800


def job_name(policy: str, env: str, q: int, f: float, seed: int, margin: int) -> str:
    method = "v2" if policy == "edf" else f"v2-{policy}"
    return f"{method}__{env}__Q{q}__f{f}__s{seed}__am{margin}"


def build_command(policy: str, env: str, q: int, f: float, seed: int, margin: int) -> list[str]:
    cmd = ["uv", "run", "python", "-m", "v2", str(seed), str(q), str(f), "--env", env, "--nogui"]
    if policy != "edf":
        cmd += ["--policy", policy]
    cmd += ["--activation-margin", str(margin)]
    return cmd


def run_job(policy: str, env: str, q: int, f: float, seed: int, margin: int, force: bool) -> tuple[str, str, float]:
    name = job_name(policy, env, q, f, seed, margin)
    csv_path = RAW_DIR / f"{name}.csv"
    if not force and csv_path.exists() and len(csv_path.read_text().splitlines()) >= 2:
        return name, "skipped", 0.0
    envv = dict(os.environ)
    envv["EVAL_OUTPUT_DIR"] = str(RAW_DIR)
    envv["EVAL_OUTPUT_NAME"] = name
    cmd = build_command(policy, env, q, f, seed, margin)
    t0 = time.time()
    with open(LOG_DIR / f"{name}.log", "w") as logf:
        try:
            proc = subprocess.run(
                cmd, cwd=str(TRACI_DIR), env=envv, stdout=logf, stderr=subprocess.STDOUT, timeout=PER_RUN_TIMEOUT_S
            )
            status = "ok" if proc.returncode == 0 and csv_path.exists() else "failed"
        except subprocess.TimeoutExpired:
            status = "timeout"
    return name, status, time.time() - t0


def main() -> None:
    ap = argparse.ArgumentParser(description="柱B-2 実験2: 活性化位置の限界比較")
    ap.add_argument("--policies", nargs="+", default=["edf", "off-late"], choices=["edf", "none", "off", "off-late"])
    ap.add_argument("--envs", nargs="+", default=["weave", "weave2"])
    ap.add_argument("--q", nargs="+", type=int, default=[3000])
    ap.add_argument("--f", nargs="+", type=float, default=[0.4, 0.6])
    ap.add_argument("--seeds", nargs="+", type=int, default=[1, 2, 3])
    ap.add_argument("--margins", nargs="+", type=int, default=[400, 300, 200, 100])
    ap.add_argument("--workers", type=int, default=7)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if "SUMO_HOME" not in os.environ:
        raise SystemExit("SUMO_HOME が未設定です。SUMO を有効化してから実行してください。")
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    jobs = [
        (p, e, q, f, s, m)
        for p in args.policies
        for e in args.envs
        for q in args.q
        for f in args.f
        for s in args.seeds
        for m in args.margins
    ]
    print(f"[run_b2_margin] jobs={len(jobs)} workers={args.workers}")
    if args.dry_run:
        for j in jobs:
            print(f"  {job_name(*j)}: {' '.join(build_command(*j))}")
        return

    with open(MANIFEST_TXT, "a") as fh:
        fh.write(f"# {datetime.now().isoformat(timespec='seconds')} 起動: {shlex.join(sys.argv)}\n")

    done = 0
    bad = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(run_job, *j, args.force): j for j in jobs}
        for fut in as_completed(futures):
            name, status, dur = fut.result()
            done += 1
            bad += status not in ("ok", "skipped")
            print(f"[{done}/{len(jobs)}] {status:7s} {name} ({dur:.0f}s)", flush=True)
            with open(MANIFEST_TXT, "a") as fh:
                fh.write(f"{datetime.now().isoformat(timespec='seconds')}\t{status}\t{name}\n")

    print(f"[run_b2_margin] 完了: ok/skip={done - bad} bad={bad}")
    if bad:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
