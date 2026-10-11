#!/usr/bin/env python3
"""TSK-125 E4: 提案から要素を 1 つずつ抜いた変種を，提案・既存の比較対象と Q3000 で横に並べる（棒グラフ・完了余裕 CDF・表）。

入力は `out/tsk125/raw/`（`run_tsk125.py --exp E0 E4` と TSK-124 の `v2-sumo`/`v2-rel`）。
出力は `out/tsk125/figures/fig_ablation_f{f}.png`・`fig_ablation_margin_f{f}.png` と `out/tsk125/table_ablation.csv`，
標準出力に Notion 貼り付け用の Markdown 表。

使い方（リポジトリ直下から）::

    uv run python scripts/eval/tsk125_ablation.py            # f=0.4, 0.6
    uv run python scripts/eval/tsk125_ablation.py --f 0.6
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tsk125_figures as tf

# 並び順: 提案 → 要素を 1 つ抜いた変種 → パラメータ変更 → 既存の比較対象（抜く量が大きい順）
VARIANT_ORDER = ["edf", "noyield", "nohold", "noalign", "noswap", "r0", "tc1", "none", "sumo", "rel", "off-late"]
VARIANT_LABEL = {
    "edf": "Proposed",
    "noyield": "− provider\nyielding",
    "nohold": "− hold before\ndeadline",
    "noalign": "− slot\nalignment",
    "noswap": "− opposing\nswap",
    "r0": "− multi-stage\ncorrection (R=0)",
    "tc1": "Tc 0.1→1.0 s",
    "none": "− EDF priority\n(FCFS)",
    "sumo": "− own car-\nfollowing (SUMO)",
    "rel": "relative-braking\nfollowing",
    "off-late": "− everything\n(non-coop.)",
}
VARIANT_LABEL_JA = {
    "edf": "提案",
    "noyield": "④ 協調減速を抜く",
    "nohold": "⑦ 締切前の保持を抜く",
    "noalign": "⑥ スロット整列を抜く",
    "noswap": "⑤ 対向スワップを抜く",
    "r0": "② 多段 LC 補正を抜く（R=0）",
    "tc1": "③ 調停周期 0.1→1.0 s",
    "none": "① EDF 優先度を抜く（到着順）",
    "sumo": "⑧ 自前の追従制御を抜く（SUMO に委ねる）",
    "rel": "⑧' 追従則を相対制動に置き換え",
    "off-late": "⑨ 全部抜く（非協調 LC2013）",
}
# 棒の色: 提案＝橙，要素除去＝青，パラメータ変更＝紫，置き換え＝黄，既存の比較対象（全部抜く）＝緑（Okabe-Ito）
GROUP_OF = {
    "edf": "proposed",
    "noyield": "removed",
    "nohold": "removed",
    "noalign": "removed",
    "noswap": "removed",
    "r0": "removed",
    "tc1": "param",
    "none": "removed",
    "sumo": "removed",
    "rel": "replaced",
    "off-late": "baseline",
}
GROUP_COLOR = {
    "proposed": "#D55E00",
    "removed": "#0072B2",
    "param": "#CC79A7",
    "replaced": "#E69F00",
    "baseline": "#009E73",
}
# CDF の線色（変種ごとに区別する）
VARIANT_COLOR = {
    "edf": "#D55E00",
    "noyield": "#0072B2",
    "nohold": "#56B4E9",
    "noalign": "#1b4f72",
    "noswap": "#7fb3d5",
    "r0": "#8e44ad",
    "tc1": "#CC79A7",
    "none": "#000000",
    "sumo": "#999999",
    "rel": "#E69F00",
    "off-late": "#009E73",
}
METRICS = [
    ("deadline_pct", "Deadline achievement [%]"),
    ("avg_speed", "Average speed [m/s]"),
    ("collisions", "Collisions [events]"),
    ("canceled", "Vehicles unable to enter"),
]
ENV_ORDER = ["merge", "weave", "weave2"]
AM = 400  # 活性化位置 [m]（--am で変更。E4b は 100）
ENV_TITLE = {"merge": "Merge (D≈194 m)", "weave": "Weave MD-1f (D≈197 m)", "weave2": "Weave MD-2 (D≈392 m)"}


def select(df: pd.DataFrame, f: float, q: int, seed: int) -> pd.DataFrame:
    d = df[(df["obstacle"].isna()) & (df["am"] == AM) & (df["q"] == q) & (df["f"] == f) & (df["seed"] == seed)]
    d = d[d["policy"].isin(VARIANT_ORDER)].copy()
    d["status"] = "ok"
    # 実時間上限で打ち切られた run（結果 CSV なし＝グリッドロック）も行として持つ（図では × 印，表では「打ち切り」）
    to = tf.load_timeouts()
    if not to.empty:
        to = to[
            (to["obstacle"].isna())
            & (to["am"] == AM)
            & (to["q"] == q)
            & (to["f"] == f)
            & (to["seed"] == seed)
            & (to["policy"].isin(VARIANT_ORDER))
            & (~to["name"].isin(set(d["name"])))
        ].copy()
        for c in ("deadline_pct", "avg_speed", "collisions", "canceled", "mlc_incomplete", "exit_throughput"):
            to[c] = float("nan")
        d = pd.concat([d, to], ignore_index=True)
    d["order"] = d["policy"].map({p: i for i, p in enumerate(VARIANT_ORDER)})
    return d.sort_values(["env", "order"])


def plot(df: pd.DataFrame, f: float, q: int, seed: int, out: Path) -> None:
    d = select(df, f, q, seed)
    present = [p for p in VARIANT_ORDER if p in set(d["policy"])]
    if not present:
        print(f"[skip] f={f}: 該当 run なし")
        return
    fig, axes = plt.subplots(
        len(ENV_ORDER), len(METRICS), figsize=(4.0 * len(METRICS), 3.2 * len(ENV_ORDER)), squeeze=False
    )
    xs = list(range(len(present)))
    for r, env in enumerate(ENV_ORDER):
        de = d[d["env"] == env].set_index("policy")
        for c, (col, title) in enumerate(METRICS):
            ax = axes[r][c]
            vals = [float(de[col].get(p, float("nan"))) if p in de.index else float("nan") for p in present]
            colors = [GROUP_COLOR[GROUP_OF[p]] for p in present]
            ax.bar(xs, vals, color=colors, width=0.7)
            if "edf" in de.index:
                ax.axhline(float(de[col].get("edf")), color=GROUP_COLOR["proposed"], ls="--", lw=0.8, alpha=0.7)
            for x, v in zip(xs, vals, strict=True):
                if v == v:  # not NaN
                    txt = f"{v:.1f}" if col in ("avg_speed", "deadline_pct") else f"{v:.0f}"
                    ax.text(x, v, txt, ha="center", va="bottom", fontsize=7)
            for x, p in zip(xs, present, strict=True):
                if p in de.index and str(de["status"].get(p)) in ("timeout", "failed"):
                    ax.text(
                        x,
                        0.04,
                        "× gridlock" if str(de["status"].get(p)) == "timeout" else "× error",
                        rotation=90,
                        transform=ax.get_xaxis_transform(),
                        ha="center",
                        va="bottom",
                        fontsize=7,
                        color="#B00020",
                    )
            if col == "deadline_pct":
                lo = min([v for v in vals if v == v] + [100.0])
                ax.set_ylim(max(0.0, lo - 5.0), 101.5)
            ax.set_xticks(xs)
            ax.set_xticklabels([VARIANT_LABEL[p] for p in present], rotation=60, ha="right", fontsize=7)
            if r == 0:
                ax.set_title(title, fontsize=10)
            if c == 0:
                ax.set_ylabel(ENV_TITLE[env], fontsize=9)
            ax.grid(axis="y", alpha=0.3)
    fig.suptitle(
        f"Removing one element at a time — Q={q} veh/h, f={f}, activation {AM} m, seed {seed} (dashed: proposed)",
        fontsize=11,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"[fig] {out}")


def completed_requests(df: pd.DataFrame, f: float, q: int, seed: int) -> pd.DataFrame:
    d = select(df, f, q, seed)
    req = tf.load_requests(d["name"].tolist())
    if req.empty:
        return req
    return req[req["completed_in_time"].astype(str) == "True"]


def margin_cdf(df: pd.DataFrame, f: float, q: int, seed: int, out: Path) -> None:
    done = completed_requests(df, f, q, seed)
    if done.empty:
        print(f"[skip] f={f}: requests.csv なし")
        return
    fig, axes = plt.subplots(1, len(ENV_ORDER), figsize=(5.2 * len(ENV_ORDER), 4.4), squeeze=False)
    for ax, env in zip(axes[0], ENV_ORDER, strict=True):
        for pol in VARIANT_ORDER:
            s = done[(done["env"] == env) & (done["policy"] == pol)]["margin_m"].sort_values()
            if s.empty:
                continue
            ax.step(
                s.values,
                [(i + 1) / len(s) for i in range(len(s))],
                where="post",
                color=VARIANT_COLOR[pol],
                lw=2.2 if pol == "edf" else 1.3,
                ls="-" if GROUP_OF[pol] in ("proposed", "removed") else "--",
                label=f"{VARIANT_LABEL[pol].replace(chr(10), ' ')} (n={len(s)})",
            )
        ax.set_title(ENV_TITLE[env], fontsize=10)
        ax.set_xlabel("Remaining distance to deadline at completion [m]")
        ax.set_ylabel("CDF")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=6.5, loc="lower right")
    fig.suptitle(
        f"Completion margin CDF — Q={q} veh/h, f={f}, activation {AM} m, seed {seed} (completed requests)", fontsize=11
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    print(f"[fig] {out}")


def table(df: pd.DataFrame, fs: list[float], q: int, seed: int, out_csv: Path) -> None:
    rows = []
    for f in fs:
        d = select(df, f, q, seed)
        done = completed_requests(df, f, q, seed)
        med = done.groupby("name")["margin_m"].median() if not done.empty else pd.Series(dtype=float)
        for _, r in d.iterrows():
            rows.append(
                {
                    "f": f,
                    "env": r["env"],
                    "variant": r["policy"],
                    "status": r["status"],
                    "variant_ja": VARIANT_LABEL_JA.get(str(r["policy"]), r["policy"]),
                    "deadline_pct": round(float(r["deadline_pct"]), 2),
                    "mlc_incomplete": r["mlc_incomplete"],
                    "collisions": r["collisions"],
                    "avg_speed": round(float(r["avg_speed"]), 1),
                    "canceled": r["canceled"],
                    "exit_throughput": (
                        round(float(r["exit_throughput"]))
                        if r["exit_throughput"] == r["exit_throughput"]
                        else float("nan")
                    ),
                    "margin_median_m": round(float(med.get(r["name"], float("nan"))), 1),
                }
            )
    t = pd.DataFrame(rows)
    t.to_csv(out_csv, index=False)
    print(f"[table] {out_csv}")
    # Notion 貼り付け用の Markdown（値は 達成率 / 衝突 / 平均速度 / 流入不能 / 完了余裕の中央値）
    for f in fs:
        tf_ = t[t["f"] == f]
        if tf_.empty:
            continue
        print(
            f"\n### f={f}（Q={q}, 活性化 {AM} m, seed {seed}）  値: 達成率[%] / 衝突 / 平均速度[m/s] / 流入不能[台] / 完了余裕の中央値[m]"
        )
        print("| 変種 | " + " | ".join(ENV_ORDER) + " |")
        print("|---|" + "---|" * len(ENV_ORDER))
        for v in VARIANT_ORDER:
            tv = tf_[tf_["variant"] == v]
            if tv.empty:
                continue
            cells = []
            for env in ENV_ORDER:
                te = tv[tv["env"] == env]
                if te.empty:
                    cells.append("—")
                else:
                    e = te.iloc[0]
                    if e["deadline_pct"] != e["deadline_pct"]:  # NaN ＝ 打ち切り
                        cells.append("打ち切り（実時間上限）" if e["status"] == "timeout" else "失敗（例外）")
                        continue
                    cells.append(
                        f"{e['deadline_pct']:.1f} / {int(e['collisions'])} / {e['avg_speed']:.1f} / "
                        f"{int(e['canceled'])} / {e['margin_median_m']:.0f}"
                    )
            print(f"| {VARIANT_LABEL_JA[v]} | " + " | ".join(cells) + " |")


def main() -> None:
    ap = argparse.ArgumentParser(description="TSK-125 E4 要素の除去の図表")
    ap.add_argument("--f", nargs="+", type=float, default=[0.4, 0.6])
    ap.add_argument("--q", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--am", type=int, default=400, help="活性化位置 [m]（既定 400。E4b は 100）")
    ap.add_argument("--envs", nargs="+", default=None, help="描く環境（既定 merge weave weave2。E4b は weave だけ）")
    args = ap.parse_args()
    global AM
    AM = args.am
    if args.envs:
        ENV_ORDER[:] = args.envs
    sfx = "" if AM == 400 else f"_am{AM}"
    df = tf.load_runs()
    for f in args.f:
        plot(df, f, args.q, args.seed, tf.FIG_DIR / f"fig_ablation_f{f}{sfx}.png")
        margin_cdf(df, f, args.q, args.seed, tf.FIG_DIR / f"fig_ablation_margin_f{f}{sfx}.png")
    table(df, args.f, args.q, args.seed, tf.OUT_DIR / f"table_ablation{sfx}.csv")


if __name__ == "__main__":
    main()
