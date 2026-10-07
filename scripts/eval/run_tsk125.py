#!/usr/bin/env python3
"""TSK-125: ベースライン（none / off-late）と提案（edf）の差が出る条件を探す探索グリッドのランナー。

`run_eval.py` と同じ実行基盤（run_sweep.Job / run_job）を使うが、メインの `out/raw/`・`manifest.json` を
汚さないよう出力を `out/tsk125/` に分離し、run 名に活性化位置（`__am<m>`）と障害物（`__obs<spec>`）を含める。
探索用なので aggregate.py は通さず、集計・作図は `tsk125_figures.py` が raw を直接読む。

使い方（リポジトリ直下から）::

    uv run python scripts/eval/run_tsk125.py --exp E0 E3 --workers 6
    uv run python scripts/eval/run_tsk125.py --exp E2 --seeds 1-2
    uv run python scripts/eval/run_tsk125.py --list        # グリッド一覧だけ表示

実験の定義（env × Q × f × policy × am × obstacle）は EXPERIMENTS にまとめる。
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
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

OUT_DIR = SCRIPT_DIR / "out" / "tsk125"
RAW_DIR = OUT_DIR / "raw"
LOG_DIR = OUT_DIR / "logs"
RUN_MANIFEST = OUT_DIR / "run_manifest_tsk125.txt"

POLICIES = ["edf", "none", "off-late"]
AM_DEFAULT = 400.0


@dataclass
class Grid:
    """1 実験分の直積グリッド。"""

    name: str
    envs: list[str]
    qs: list[int]
    fs: list[float]
    policies: list[str]
    margins: list[float]
    obstacle: str | None
    seeds: str
    note: str
    # 提案から要素を抜いた変種 [(tag, --set の指定)]。指定時は policies は edf のみを想定し、method は v2-<tag>
    variants: list[tuple[str, str]] | None = None


# E4: 提案（edf）から 1 要素ずつ抜いた／1 パラメータだけ変えた変種。値は v2/constants.py の定数を --set で上書きする
VARIANTS_E4: list[tuple[str, str]] = [
    ("noyield", "COOP_YIELD=0"),  # 提供車の協調減速（Phase B の割当）を抜く
    ("nohold", "HOLD_MARGIN=0"),  # 締切前の保持を抜く
    ("noalign", "ALIGN_DELTA=0"),  # スロット整列を抜く（同速追従に戻す）
    ("noswap", "SWAP_WINDOW=-1"),  # 対向スワップを抜く
    ("r0", "R=0"),  # 多段 LC の実効距離補正を抜く
    ("tc1", "TC=1.0"),  # 調停周期 0.1 → 1.0 s
]

EXPERIMENTS: dict[str, Grid] = {
    # 既定条件の 3 手法対比（完了余裕 CDF・速度・衝突の基準点。E2 の am=400 の参照点を兼ねる）
    "E0": Grid(
        name="E0",
        envs=["weave", "weave2", "merge"],
        qs=[3000],
        fs=[0.4, 0.6],
        policies=POLICIES,
        margins=[AM_DEFAULT],
        obstacle=None,
        seeds="1-2",
        note="既定（am400）での 3 手法対比",
    ),
    # 負荷限界: 既存グリッド（Q≤4000）の外側で非協調が破綻するか
    "E1": Grid(
        name="E1",
        envs=["weave", "weave2", "merge"],
        qs=[4000, 5000, 6000],
        fs=[0.4, 0.8],
        policies=POLICIES,
        margins=[AM_DEFAULT],
        obstacle=None,
        seeds="1-2",
        note="負荷限界（Q>4000・f=0.8）",
    ),
    # 猶予距離: 活性化位置を縮めたとき（通知が遅いとき）に差が出るか
    "E2": Grid(
        name="E2",
        envs=["weave", "weave2", "merge"],
        qs=[3000],
        fs=[0.4, 0.6],
        policies=POLICIES,
        margins=[200.0, 100.0, 50.0],
        obstacle=None,
        seeds="1-2",
        note="猶予距離（am 200/100/50）",
    ),
    # 複合: 必須LC の需要がある区間に突発封鎖が重なったとき
    "E3": Grid(
        name="E3",
        envs=["weave", "weave2"],
        qs=[2000, 3000],
        fs=[0.4],
        policies=POLICIES,
        margins=[AM_DEFAULT],
        obstacle="1,120,60",
        seeds="1-3",
        note="複合（必須LC＋突発封鎖 lane1・120m・t=60s）",
    ),
    # 要素の除去: 提案から 1 要素ずつ抜いた変種を既定条件（E0 と同じ Q3000・am400）で比較する
    "E4": Grid(
        name="E4",
        envs=["weave", "weave2", "merge"],
        qs=[3000],
        fs=[0.4, 0.6],
        policies=["edf"],
        margins=[AM_DEFAULT],
        obstacle=None,
        seeds="1",
        note="要素の除去（提案から 1 要素ずつ抜く）",
        variants=VARIANTS_E4,
    ),
    # 要素の除去を，差が出た条件（片側織り込み・通知が遅い＝活性化 100 m）でも回す。edf/none/off-late の am100 は E2 にある
    "E4b": Grid(
        name="E4b",
        envs=["weave"],
        qs=[3000],
        fs=[0.4, 0.6],
        policies=["edf"],
        margins=[100.0],
        obstacle=None,
        seeds="1",
        note="要素の除去 × 通知が遅い条件（weave・am100）",
        variants=VARIANTS_E4,
    ),
}


@dataclass
class TJob(rs.Job):
    """run_sweep.Job に活性化位置を足した探索用ジョブ。"""

    activation_margin: float = AM_DEFAULT
    set_spec: str | None = None  # --set NAME=VALUE[,...]（要素の除去）

    @property
    def name(self) -> str:  # type: ignore[override]
        base = super().name
        if self.activation_margin != AM_DEFAULT:
            base += f"__am{int(self.activation_margin)}"
        return base

    def command(self) -> list[str]:
        cmd = super().command()
        if self.activation_margin != AM_DEFAULT:
            cmd += ["--activation-margin", str(self.activation_margin)]
        if self.set_spec:
            cmd += ["--set", self.set_spec]
        return cmd


def build_jobs(grid: Grid, seeds_override: str | None) -> list[TJob]:
    jobs: list[TJob] = []
    seeds = parse_seeds(seeds_override or grid.seeds)
    for env in grid.envs:
        scenario = env if grid.obstacle is None else f"{env}_obs"
        for q in grid.qs:
            for f in grid.fs:
                for policy in grid.policies:
                    for am in grid.margins:
                        for variant in grid.variants or [None]:
                            for s in seeds:
                                if variant is not None:
                                    method, set_spec = f"v2-{variant[0]}", variant[1]
                                else:
                                    method, set_spec = ("v2" if policy == "edf" else f"v2-{policy}"), None
                                jobs.append(
                                    TJob(
                                        method=method,
                                        scenario=scenario,
                                        env=env,
                                        q=q,
                                        f=f,
                                        seed=s,
                                        obstacle=grid.obstacle,
                                        policy=policy,
                                        activation_margin=am,
                                        set_spec=set_spec,
                                    )
                                )
    return jobs


def append_manifest(lines: list[str]) -> None:
    with open(RUN_MANIFEST, "a") as fh:
        for ln in lines:
            fh.write(ln + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="TSK-125 探索グリッドのランナー")
    ap.add_argument("--exp", nargs="+", choices=sorted(EXPERIMENTS), default=["E0"], help="実行する実験")
    ap.add_argument("--seeds", default=None, help="seed 範囲の上書き（例 1-2）")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--list", action="store_true", help="ジョブ一覧だけ表示")
    args = ap.parse_args()

    if "SUMO_HOME" not in os.environ:
        raise SystemExit("SUMO_HOME が未設定です。")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # run_sweep.run_job はモジュール変数の RAW_DIR / LOG_DIR を参照するので差し替える
    rs.RAW_DIR = RAW_DIR
    rs.LOG_DIR = LOG_DIR

    jobs: list[TJob] = []
    for name in args.exp:
        jobs += build_jobs(EXPERIMENTS[name], args.seeds)
    print(f"[tsk125] exp={args.exp} jobs={len(jobs)} workers={args.workers} out={OUT_DIR}")
    if args.list:
        for j in jobs:
            print(f"  {j.name}: {' '.join(j.command())}")
        return

    meta = rs.collect_metadata()
    append_manifest(
        [
            f"# {datetime.now().isoformat(timespec='seconds')} 起動: {shlex.join(sys.argv)} "
            f"git={meta['git_commit']} dirty={meta['git_dirty']} sumo={meta['sumo_version']}"
        ]
    )

    t0 = time.time()
    done = 0
    results: list[TJob] = []
    # 重い edf（高負荷）を先に投げて尻尾を短くする
    jobs.sort(key=lambda j: (j.policy != "edf", -j.q))
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(rs.run_job, j, args.force): j for j in jobs}
        for fut in as_completed(futures):
            j = fut.result()
            results.append(j)  # type: ignore[arg-type]
            done += 1
            dur = f"{j.duration_s:.0f}s" if j.duration_s is not None else "-"
            print(f"[{done}/{len(jobs)}] {j.status:7s} {j.name} ({dur})", flush=True)
            append_manifest(
                [f"{datetime.now().isoformat(timespec='seconds')}\t{j.status}\t{j.name}\t{shlex.join(j.command())}"]
            )
    elapsed = time.time() - t0
    bad = [j for j in results if j.status not in ("ok", "skipped")]
    print(f"\n[tsk125] 完了: ok/skip={len(results) - len(bad)} bad={len(bad)} elapsed={elapsed:.0f}s")
    for j in bad:
        print(f"  - {j.status}: {j.name} (log: {j.log})")


if __name__ == "__main__":
    main()
