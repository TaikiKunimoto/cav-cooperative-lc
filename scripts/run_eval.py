#!/usr/bin/env python3
"""一括評価ランナー: 手法(policy)×env×Q×f×seed範囲×並列度 を CLI 指定でループ実行する。

`scripts/eval/run_sweep.py`（固定スイート）と同じ実行基盤（EVAL_OUTPUT_DIR/NAME フック・
.prev 退避・manifest.json 統合）を共有しつつ、任意の直積グリッドを CLI から組める一括ランナー。

  - レジューム: 出力 CSV（データ行あり）が既にある条件はスキップ。--force で無視して再実行
    （旧 CSV は .prev へ退避）
  - 記録: 全 run のコマンドを `scripts/eval/out/run_manifest.txt` へ追記（1行=1run、スキップ含む）。
    実行来歴（git commit / SUMO / ホスト）は manifest.json にも統合され aggregate.py が読む
  - policy: edf（既定。現行実装＝フラグ省略）/ none / off。edf 以外は `--policy` を v2 CLI へ
    渡し（柱B のアブレーション実装が受け取る）、手法ラベルが v2-<policy> になるため
    既存の v2__ 結果と衝突しない

使い方（リポジトリ直下から）::

    # 提案手法の必須LC 4環境 × 全グリッド（既存結果はスキップ＝レジューム）
    uv run python scripts/run_eval.py --env diverge merge weave weave2 \
        --q 1500 2000 2500 3000 3500 4000 --f 0.2 0.4 0.6 --seeds 1-5 --workers 8

    # 障害物封鎖（B シナリオ）
    uv run python scripts/run_eval.py --env straight --q 1500 2000 2500 3000 3500 \
        --f 0.0 --seeds 1-5 --obstacle 1,500,60

    # アブレーション（優先度なし）を weave2 の一部条件で
    uv run python scripts/run_eval.py --policy none --env weave2 --q 3000 4000 --f 0.6 --seeds 1-5
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
sys.path.insert(0, str(SCRIPT_DIR / "eval"))

import run_sweep as rs  # noqa: E402  （scripts/eval/run_sweep.py の実行基盤を共有）

RUN_MANIFEST = rs.OUT_DIR / "run_manifest.txt"

# v2 CLI が受け付ける環境名（想定外は即エラー）
KNOWN_ENVS = ["diverge", "merge", "weave", "weave2", "straight"]


def parse_seeds(spec: str) -> list[int]:
    """seed 範囲指定をパースする（例 "1-5" / "1,3,5" / "1-3,5"）。想定外の書式は即エラー。"""
    seeds: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, hi_s = part.split("-", 1)
            lo, hi = int(lo_s), int(hi_s)
            if hi < lo:
                raise ValueError(f"seed 範囲が逆順です: {part!r}（{spec!r}）")
            seeds.extend(range(lo, hi + 1))
        else:
            seeds.append(int(part))
    if not seeds:
        raise ValueError(f"seed 指定が空です: {spec!r}")
    return sorted(set(seeds))


def build_jobs(args: argparse.Namespace) -> list[rs.Job]:
    method = "v2" if args.policy == "edf" else f"v2-{args.policy}"
    jobs: list[rs.Job] = []
    for env in args.env:
        scenario = env if args.obstacle is None else f"{env}_obs"
        for q in args.q:
            for f in args.f:
                for s in parse_seeds(args.seeds):
                    jobs.append(
                        rs.Job(
                            method=method,
                            scenario=scenario,
                            env=env,
                            q=q,
                            f=f,
                            seed=s,
                            obstacle=args.obstacle,
                            policy=args.policy,
                        )
                    )
    return jobs


def append_run_manifest(lines: list[str]) -> None:
    with open(RUN_MANIFEST, "a") as fh:
        for ln in lines:
            fh.write(ln + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="一括評価ランナー（policy×env×Q×f×seed×並列度）")
    ap.add_argument("--policy", choices=["edf", "none", "off"], default="edf", help="調停ポリシー（既定 edf）")
    ap.add_argument("--env", nargs="+", choices=KNOWN_ENVS, default=["diverge", "merge", "weave", "weave2"])
    ap.add_argument("--q", nargs="+", type=int, default=rs.Q_FULL, help="総流入 Q [veh/h] の水準")
    ap.add_argument("--f", nargs="+", type=float, default=rs.F_FULL, help="必須LC比率 f の水準")
    ap.add_argument("--seeds", default="1-5", help='seed 範囲（例 "1-5" / "1,3,5"）')
    ap.add_argument("--obstacle", default=None, help='障害物 "lane,pos,time"（straight の B シナリオ用）')
    ap.add_argument(
        "--workers",
        type=int,
        default=max(1, int((os.cpu_count() or 4) * 0.35)),
        help="並列度（既定=論理コアの35%%≒物理コアの7割）",
    )
    ap.add_argument("--force", action="store_true", help="既存 CSV を無視して再実行（旧CSVは .prev 退避）")
    ap.add_argument("--dry-run", action="store_true", help="ジョブ一覧だけ表示して終了")
    args = ap.parse_args()

    if "SUMO_HOME" not in os.environ:
        raise SystemExit("SUMO_HOME が未設定です。SUMO を有効化してから実行してください。")

    rs.RAW_DIR.mkdir(parents=True, exist_ok=True)
    rs.LOG_DIR.mkdir(parents=True, exist_ok=True)

    jobs = build_jobs(args)
    print(f"[run_eval] policy={args.policy} envs={args.env} jobs={len(jobs)} workers={args.workers}")

    if args.dry_run:
        for j in jobs:
            print(f"  {j.name}: {' '.join(j.command())}")
        return

    append_run_manifest([f"# {datetime.now().isoformat(timespec='seconds')} run_eval 起動: {shlex.join(sys.argv)}"])

    t_start = time.time()
    done = 0
    results: list[rs.Job] = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(rs.run_job, j, args.force): j for j in jobs}
        for fut in as_completed(futures):
            j = fut.result()
            results.append(j)
            done += 1
            dur = f"{j.duration_s:.1f}s" if j.duration_s is not None else "-"
            print(f"[{done}/{len(jobs)}] {j.status:7s} {j.name} ({dur})", flush=True)
            append_run_manifest(
                [
                    f"{datetime.now().isoformat(timespec='seconds')}\t{j.status}\t{j.name}\t"
                    f"EVAL_OUTPUT_NAME={j.name} {shlex.join(j.command())}"
                ]
            )

    elapsed = time.time() - t_start
    rs.write_manifest(results, suite=f"run_eval:{args.policy}", quick=False, elapsed=elapsed)

    n_ok = sum(1 for j in results if j.status in ("ok", "skipped"))
    n_bad = len(results) - n_ok
    print(f"\n[run_eval] 完了: ok/skip={n_ok} bad={n_bad} elapsed={elapsed:.0f}s")
    print(f"[run_eval] コマンド記録: {RUN_MANIFEST}  来歴: {rs.MANIFEST}")
    if n_bad:
        print("[run_eval] 失敗/timeout の run:")
        for j in results:
            if j.status not in ("ok", "skipped"):
                print(f"  - {j.status}: {j.name}  (log: {j.log})")


if __name__ == "__main__":
    main()
