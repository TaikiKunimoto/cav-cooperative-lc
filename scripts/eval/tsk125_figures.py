#!/usr/bin/env python3
"""TSK-125 探索グリッド（run_tsk125.py）の集計と作図。

入力: scripts/eval/out/tsk125/raw/*.csv（メイン CSV・__requests.csv・__obstacle_summary.csv）
出力: scripts/eval/out/tsk125/summary_tsk125.csv と figures/ 以下の PNG。

探索用（論文体裁ではない）。ラベルは英語。数値の解釈はしない（図と表を出すまで）。
"""

from __future__ import annotations

from pathlib import Path
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
OUT_DIR = SCRIPT_DIR / "out" / "tsk125"
RAW_DIR = OUT_DIR / "raw"
FIG_DIR = OUT_DIR / "figures"

# 手法ラベル（run 名の method 部）。sumo / rel は TSK-124 の縦制御修正版（別 worktree が同じ raw に出力する）
POLICY_ORDER = ["edf", "none", "off-late", "sumo", "rel"]
POLICY_LABEL = {
    "edf": "Proposed (EDF)",
    "none": "No priority (FCFS)",
    "off-late": "Non-coop. (LC2013, late notice)",
    "sumo": "Proposed + SUMO car-following (fix A)",
    "rel": "Proposed + relative-braking following (fix B)",
}
# Okabe-Ito
POLICY_COLOR = {"edf": "#D55E00", "none": "#0072B2", "off-late": "#009E73", "sumo": "#CC79A7", "rel": "#E69F00"}
POLICY_MARKER = {"edf": "o", "none": "s", "off-late": "^", "sumo": "D", "rel": "v"}
ENV_LABEL = {"weave": "Weave MD-1f\n(D≈197 m)", "weave2": "Weave MD-2\n(D≈392 m)", "merge": "Merge M\n(D≈194 m)"}
ENV_ORDER = ["merge", "weave", "weave2"]

NAME_RE = re.compile(
    r"^(?P<method>v2(?:-[a-z-]+)?)__(?P<scenario>[a-z0-9_]+)__Q(?P<q>\d+)__f(?P<f>[\d.]+)__s(?P<seed>\d+)"
    r"(?:__obs(?P<obs>[\d.-]+))?(?:__am(?P<am>\d+))?$"
)

COLS = {
    "deadline_achievement_rate": "deadline_rate",
    "mandatory_lc_total": "mlc_total",
    "mandatory_lc_completed": "mlc_completed",
    "mandatory_lc_collided": "mlc_collided",
    "mandatory_lc_incomplete": "mlc_incomplete",
    "total_collisions": "collisions",
    "average_speed": "avg_speed",
    "average_travel_time": "avg_travel_time",
    "total_departed_vehicles": "departed",
    "exited_vehicles": "exited",
    "running_vehicles": "running_end",
    "canceled_vehicles": "canceled",
    "total_generated_vehicles": "generated",
    "simulation_time": "sim_time",
    "min_TTC": "min_ttc",
    "TET": "tet",
}


def parse_name(stem: str) -> dict[str, object] | None:
    m = NAME_RE.match(stem)
    if m is None:
        return None
    d = m.groupdict()
    method = str(d["method"])
    policy = "edf" if method == "v2" else method[len("v2-") :]
    return {
        "name": stem,
        "policy": policy,
        "scenario": d["scenario"],
        "env": str(d["scenario"]).replace("_obs", ""),
        "q": int(d["q"]),
        "f": float(d["f"]),
        "seed": int(d["seed"]),
        "obstacle": d["obs"],
        "am": int(d["am"]) if d["am"] else 400,
    }


def load_runs() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for p in sorted(RAW_DIR.glob("*.csv")):
        if any(tag in p.stem for tag in ("__requests", "__failures", "__obstacle_")):  # sidecar は除外
            continue
        meta = parse_name(p.stem)
        if meta is None:
            print(f"[skip] 名前を解釈できません: {p.name}")
            continue
        raw = pd.read_csv(p)
        if raw.empty:
            continue
        r = raw.iloc[0]
        row = dict(meta)
        for src, dst in COLS.items():
            row[dst] = pd.to_numeric(r.get(src), errors="coerce")
        side = RAW_DIR / f"{p.stem}__obstacle_summary.csv"
        if side.exists():
            s = pd.read_csv(side).iloc[0]
            for k in (
                "max_queue_m",
                "hard_brake_events",
                "throughput_post_vph",
                "avg_speed_post_ms",
                "avoid_requests",
                "avoid_stuck_at_end",
            ):
                row[k] = pd.to_numeric(s.get(k), errors="coerce")
        rows.append(row)
    df = pd.DataFrame(rows)
    if df.empty:
        raise SystemExit(f"{RAW_DIR} に run がありません")
    df["exit_throughput"] = df["exited"] / 600.0 * 3600.0  # 流入 600 s を基準にした退出台数 [veh/h]
    df["deadline_pct"] = df["deadline_rate"] * 100
    return df


def load_requests(names: list[str]) -> pd.DataFrame:
    parts = []
    for n in names:
        p = RAW_DIR / f"{n}__requests.csv"
        if not p.exists():
            continue
        d = pd.read_csv(p)
        meta = parse_name(n)
        assert meta is not None
        for k, v in meta.items():
            d[k] = v
        parts.append(d)
    if not parts:
        return pd.DataFrame()
    d = pd.concat(parts, ignore_index=True)
    d["margin_m"] = d["deadline_pos"] - d["completion_pos"]
    return d


def _save(fig: plt.Figure, name: str) -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    p = FIG_DIR / f"{name}.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    print(f"[fig] {p}")


def _lines(ax: plt.Axes, d: pd.DataFrame, x: str, y: str, agg: str = "mean") -> None:
    for pol in POLICY_ORDER:
        s = d[d["policy"] == pol].groupby(x)[y].agg([agg, "std", "size"]).reset_index()
        if s.empty:
            continue
        ax.errorbar(
            s[x],
            s[agg],
            yerr=s["std"].fillna(0),
            color=POLICY_COLOR[pol],
            marker=POLICY_MARKER[pol],
            lw=1.6,
            ms=6,
            capsize=3,
            label=POLICY_LABEL[pol],
        )


def _bars(ax: plt.Axes, d: pd.DataFrame, cat: str, cats: list[str], y: str) -> None:
    present = [p for p in POLICY_ORDER if p in set(d["policy"])]
    w = 0.8 / max(len(present), 1)
    for i, pol in enumerate(present):
        s = d[d["policy"] == pol].groupby(cat)[y].agg(["mean", "std"]).reindex(cats)
        xs = [k + (i - (len(present) - 1) / 2) * w for k in range(len(cats))]
        ax.bar(
            xs,
            s["mean"],
            width=w,
            yerr=s["std"].fillna(0),
            capsize=3,
            color=POLICY_COLOR[pol],
            edgecolor="black",
            lw=0.5,
            label=POLICY_LABEL[pol],
        )
    ax.set_xticks(range(len(cats)))
    ax.set_xticklabels([ENV_LABEL.get(c, c) for c in cats], fontsize=9)


METRICS = [
    ("deadline_pct", "Deadline completion [%]"),
    ("mlc_incomplete", "Incomplete mandatory LC [per run]"),
    ("collisions", "Collisions [per run]"),
    ("avg_speed", "Average speed [m/s]"),
    ("exit_throughput", "Exit throughput [veh/h]"),
    ("canceled", "Vehicles not inserted (canceled) [per run]"),
]


def fig_e0(df: pd.DataFrame) -> None:
    d = df[(df["obstacle"].isna()) & (df["am"] == 400) & (df["q"] == 3000)]
    if d.empty:
        return
    envs = [e for e in ENV_ORDER if e in d["env"].unique()]
    fig, axes = plt.subplots(2, 3, figsize=(15, 8))
    for ax, (y, lab) in zip(axes.flat, METRICS, strict=True):
        _bars(ax, d, "env", envs, y)
        ax.set_ylabel(lab)
        ax.grid(axis="y", alpha=0.3)
    axes[0, 0].set_ylim(80, 102)
    axes[0, 0].axhline(100, color="gray", lw=0.8, ls="--")
    axes[0, 0].legend(fontsize=8, loc="lower left")
    fig.suptitle(
        f"E0: default activation (400 m), Q=3000 veh/h, f∈{{{', '.join(str(x) for x in sorted(d['f'].unique()))}}}, seeds n={d['seed'].nunique()} (mean ± SD over f×seed)",
        fontsize=11,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    _save(fig, "fig_e0_overview")


def fig_margin_cdf(df: pd.DataFrame) -> None:
    d = df[(df["obstacle"].isna()) & (df["am"] == 400) & (df["q"] == 3000)]
    if d.empty:
        return
    req = load_requests(d["name"].tolist())
    if req.empty:
        return
    done = req[req["completed_in_time"].astype(str) == "True"]
    envs = [e for e in ENV_ORDER if e in done["env"].unique()]
    fig, axes = plt.subplots(1, len(envs), figsize=(5 * len(envs), 4.2), squeeze=False)
    for ax, env in zip(axes[0], envs, strict=True):
        for pol in POLICY_ORDER:
            s = done[(done["env"] == env) & (done["policy"] == pol)]["margin_m"].sort_values()
            if s.empty:
                continue
            ax.step(
                s.values,
                [(i + 1) / len(s) for i in range(len(s))],
                where="post",
                color=POLICY_COLOR[pol],
                lw=1.6,
                label=f"{POLICY_LABEL[pol]} (n={len(s)})",
            )
        ax.set_title(ENV_LABEL.get(env, env), fontsize=10)
        ax.set_xlabel("Remaining distance to deadline at completion [m]")
        ax.set_ylabel("CDF")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7, loc="lower right")
    fig.suptitle("E0: completion margin CDF by policy (completed requests, Q=3000, am=400)", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    _save(fig, "fig_e0_margin_cdf")


def fig_e1(df: pd.DataFrame) -> None:
    for f in (0.4, 0.8):
        d = df[
            (df["obstacle"].isna())
            & (df["am"] == 400)
            & (df["f"] == f)
            & (df["q"] >= 3000)
            & (df["policy"].isin(POLICY_ORDER[:3]))
        ]
        if d.empty or d["q"].nunique() < 2:
            continue
        envs = [e for e in ENV_ORDER if e in d["env"].unique()]
        fig, axes = plt.subplots(len(METRICS), len(envs), figsize=(4.6 * len(envs), 2.8 * len(METRICS)), squeeze=False)
        for j, env in enumerate(envs):
            for i, (y, lab) in enumerate(METRICS):
                ax = axes[i, j]
                _lines(ax, d[d["env"] == env], "q", y)
                if i == 0:
                    ax.set_title(ENV_LABEL.get(env, env), fontsize=10)
                    ax.set_ylim(0, 105)
                    ax.axhline(100, color="gray", lw=0.8, ls="--")
                if j == 0:
                    ax.set_ylabel(lab, fontsize=9)
                if i == len(METRICS) - 1:
                    ax.set_xlabel("Total inflow Q [veh/h]")
                ax.grid(alpha=0.3)
        axes[0, 0].legend(fontsize=7, loc="lower left")
        fig.suptitle(f"E1: load beyond the evaluated grid (f={f}, am=400; mean ± SD over seeds)", fontsize=11)
        fig.tight_layout(rect=(0, 0, 1, 0.97))
        _save(fig, f"fig_e1_load_f{f}")


def fig_e2(df: pd.DataFrame) -> None:
    d = df[(df["obstacle"].isna()) & (df["q"] == 3000) & (df["policy"].isin(POLICY_ORDER[:3]))]
    if d.empty or d["am"].nunique() < 2:
        return
    envs = [e for e in ENV_ORDER if e in d["env"].unique()]
    metrics = [m for m in METRICS if m[0] in ("deadline_pct", "mlc_incomplete", "collisions", "avg_speed")]
    for f in sorted(d["f"].unique()):
        dd = d[d["f"] == f]
        fig, axes = plt.subplots(len(metrics), len(envs), figsize=(4.6 * len(envs), 2.9 * len(metrics)), squeeze=False)
        for j, env in enumerate(envs):
            for i, (y, lab) in enumerate(metrics):
                ax = axes[i, j]
                _lines(ax, dd[dd["env"] == env], "am", y)
                ax.invert_xaxis()
                if i == 0:
                    ax.set_title(ENV_LABEL.get(env, env), fontsize=10)
                    ax.set_ylim(0, 105)
                    ax.axhline(100, color="gray", lw=0.8, ls="--")
                if j == 0:
                    ax.set_ylabel(lab, fontsize=9)
                if i == len(metrics) - 1:
                    ax.set_xlabel("Activation distance before deadline [m] (= notice timing)")
                ax.grid(alpha=0.3)
        axes[0, 0].legend(fontsize=7, loc="lower left")
        fig.suptitle(f"E2: shorter notice (activation margin), Q=3000, f={f} (mean ± SD over seeds)", fontsize=11)
        fig.tight_layout(rect=(0, 0, 1, 0.97))
        _save(fig, f"fig_e2_margin_f{f}")


def fig_e3(df: pd.DataFrame) -> None:
    d = df[df["obstacle"].notna() & df["policy"].isin(POLICY_ORDER[:3])]
    if d.empty:
        return
    envs = [e for e in ENV_ORDER if e in d["env"].unique()]
    metrics = [
        ("deadline_pct", "Mandatory-LC deadline completion [%]"),
        ("collisions", "Collisions [per run]"),
        ("max_queue_m", "Max queue upstream of blockage [m]"),
        ("avg_speed", "Average speed [m/s]"),
        ("exit_throughput", "Exit throughput [veh/h]"),
        ("hard_brake_events", "Hard-brake events (≤ -3 m/s²) [per run]"),
    ]
    fig, axes = plt.subplots(len(metrics), len(envs), figsize=(5 * len(envs), 2.8 * len(metrics)), squeeze=False)
    for j, env in enumerate(envs):
        for i, (y, lab) in enumerate(metrics):
            ax = axes[i, j]
            if y not in d.columns:
                ax.set_visible(False)
                continue
            _lines(ax, d[d["env"] == env], "q", y)
            if i == 0:
                ax.set_title(ENV_LABEL.get(env, env), fontsize=10)
                ax.set_ylim(0, 105)
                ax.axhline(100, color="gray", lw=0.8, ls="--")
            if j == 0:
                ax.set_ylabel(lab, fontsize=9)
            if i == len(metrics) - 1:
                ax.set_xlabel("Total inflow Q [veh/h]")
            ax.grid(alpha=0.3)
    axes[0, 0].legend(fontsize=7, loc="lower left")
    obs = d["obstacle"].iloc[0]
    fig.suptitle(
        f"E3: mandatory LC + sudden lane blockage (obstacle {obs}: lane,pos[m],t[s]); f=0.4, seeds n={d['seed'].nunique()}",
        fontsize=11,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    _save(fig, "fig_e3_obstacle")


def load_timeouts() -> pd.DataFrame:
    """run_manifest_tsk125.txt から timeout / failed の run を拾う（打ち切り＝結果 CSV なし。図の欠損の説明用）。"""
    mf = OUT_DIR / "run_manifest_tsk125.txt"
    rows: list[dict[str, object]] = []
    if not mf.exists():
        return pd.DataFrame()
    seen: set[str] = set()
    for ln in mf.read_text().splitlines():
        parts = ln.split("\t")
        if len(parts) < 3 or parts[1] not in ("timeout", "failed"):
            continue
        name = parts[2]
        if name in seen:
            continue
        seen.add(name)
        meta = parse_name(name)
        if meta is None:
            continue
        meta["status"] = parts[1]
        rows.append(meta)
    return pd.DataFrame(rows)


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="TSK-125 の集計・作図")
    ap.add_argument("--seeds", default="1", help="使う seed（カンマ区切り。既定 1）")
    args = ap.parse_args()
    seeds = [int(x) for x in args.seeds.split(",")]
    df = load_runs()
    df = df[df["seed"].isin(seeds)]
    if df.empty:
        raise SystemExit(f"seed {seeds} の run がありません")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_DIR / "summary_tsk125.csv", index=False)
    print(f"[summary] runs={len(df)} → {OUT_DIR / 'summary_tsk125.csv'}")
    # 条件別の集計表（人が読む用）
    key = ["env", "obstacle", "am", "q", "f", "policy"]
    tab = (
        df.groupby(key, dropna=False)[
            ["deadline_pct", "mlc_total", "mlc_incomplete", "collisions", "avg_speed", "exit_throughput", "canceled"]
        ]
        .mean()
        .round(2)
        .reset_index()
    )
    tab["n"] = df.groupby(key, dropna=False).size().values
    tab.to_csv(OUT_DIR / "table_tsk125.csv", index=False)
    print(tab.to_string(index=False))
    to = load_timeouts()
    if not to.empty:
        to = to[to["seed"].isin(seeds)]
        # 後から成功した run は除く（レジューム再実行で CSV ができているもの）
        to = to[~to["name"].isin(set(df["name"]))]
        to.to_csv(OUT_DIR / "timeouts_tsk125.csv", index=False)
        print("\n[timeouts/failed] 結果 CSV の無い run（図では欠損）:")
        print(to[["status", "policy", "env", "q", "f", "am", "obstacle"]].to_string(index=False))
    fig_e0(df)
    fig_margin_cdf(df)
    fig_e1(df)
    fig_e2(df)
    fig_e3(df)


if __name__ == "__main__":
    main()
