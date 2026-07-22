#!/usr/bin/env python3
"""挿入安全判定の修正前/後を、同一グリッドの CSV から比較する（PR 用の前後表を出力）。

使い方:
    uv run python scripts/eval/compare_fix.py [before_dir] [after_dir]
既定: before=out/raw_before_fix, after=out/raw

各 run CSV のファイル名 ``v2__<scenario>__Q<q>__f<f>__s<seed>[...].csv`` から (scenario,q,f,seed) を取り、
衝突件数/run・衝突0率・締切達成率を before/after で集計して表示する（提案シナリオのみ）。
"""

from __future__ import annotations

import csv
from pathlib import Path
import re
import sys

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
OUT = SCRIPT_DIR / "out"
SCEN = ["diverge", "merge", "weave", "weave2", "straight_obs"]
NAME_RE = re.compile(r"^v2__(?P<scen>[a-z0-9_]+?)__Q(?P<q>\d+)__f(?P<f>[\d.]+)__s(?P<seed>\d+)")


def load(d: Path) -> pd.DataFrame:
    rows = []
    for p in sorted(d.glob("v2__*.csv")):
        m = NAME_RE.match(p.name)
        if not m or m.group("scen") not in SCEN:
            continue
        try:
            with open(p) as fh:
                data = list(csv.DictReader(fh))
            if len(data) < 1:
                continue
            r = data[-1]
        except OSError:
            continue
        coll = r.get("total_collisions")
        dl = r.get("deadline_achievement_rate")
        rows.append(
            {
                "scenario": m.group("scen"),
                "q": int(m.group("q")),
                "f": float(m.group("f")),
                "seed": int(m.group("seed")),
                "collisions": float(coll) if coll not in (None, "") else None,
                "deadline": float(dl) if dl not in (None, "") else None,
            }
        )
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["cfree"] = (df["collisions"] == 0).astype(float)
    g = (
        df.groupby("scenario")
        .agg(
            n=("seed", "size"),
            coll_per_run=("collisions", "mean"),
            collisions_total=("collisions", "sum"),
            cfree_pct=("cfree", "mean"),
            deadline_mean=("deadline", "mean"),
        )
        .reindex(SCEN)
    )
    g["cfree_pct"] *= 100
    return g


def main() -> None:
    before_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT / "raw_before_fix"
    after_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else OUT / "raw"
    b = summarize(load(before_dir))
    a = summarize(load(after_dir))

    def fmt(x, nd=2):
        return "-" if x is None or pd.isna(x) else f"{x:.{nd}f}"

    print(f"before = {before_dir}")
    print(f"after  = {after_dir}\n")
    print("| シナリオ | n | 衝突/run 前→後 | 衝突計 前→後 | 衝突0率% 前→後 | 締切達成率 前→後 |")
    print("|---|---:|---:|---:|---:|---:|")
    for s in SCEN:
        if s not in b.index or s not in a.index:
            continue
        rb, ra = b.loc[s], a.loc[s]
        if pd.isna(ra["n"]) or pd.isna(rb["n"]):
            print(f"| {s} | - | (データ不足) | | | |")
            continue
        print(
            f"| {s} | {int(ra['n'])} | {fmt(rb['coll_per_run'])} → **{fmt(ra['coll_per_run'])}** | "
            f"{int(rb['collisions_total'])} → **{int(ra['collisions_total'])}** | "
            f"{fmt(rb['cfree_pct'],0)} → **{fmt(ra['cfree_pct'],0)}** | "
            f"{fmt(rb['deadline_mean'],3)} → **{fmt(ra['deadline_mean'],3)}** |"
        )
    # 全体（MLC4環境）
    bm = load(before_dir)
    am = load(after_dir)
    bm = bm[bm.scenario != "straight_obs"]
    am = am[am.scenario != "straight_obs"]
    print(
        f"\nMLC4環境合計 衝突件数: {int(bm['collisions'].sum())} → {int(am['collisions'].sum())}"
        f"  / 衝突0率: {(bm['collisions']==0).mean()*100:.0f}% → {(am['collisions']==0).mean()*100:.0f}%"
        f"  / 締切達成率(平均): {bm['deadline'].mean():.3f} → {am['deadline'].mean():.3f}"
    )


if __name__ == "__main__":
    main()
