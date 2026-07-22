#!/usr/bin/env python3
"""集計結果（summary_long.csv）から、スライド用の図を生成する。

出力（out/figures/）:
  - fig1_scenario_deadline.png … 複数シナリオの締切達成率（メッセージA: 単一手法で複数シナリオに対応）
  - fig2_robustness_deadline.png … Q×f を変えた締切達成率（メッセージB: 流入量・LC率に頑健）
  - fig_safety.png … シナリオ別の安全性（衝突件数/run・衝突0%率）
  - fig3_baseline_diverge.png … 分流での交通効率比較 v2 vs v1（baseline データがある場合のみ）

ラベルは文字化け回避のため英語表記（数表側は日本語）。依存: pandas / matplotlib（導入済み）。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
OUT_DIR = SCRIPT_DIR / "out"
FIG_DIR = OUT_DIR / "figures"
LONG_CSV = OUT_DIR / "summary_long.csv"

MLC_ENVS = ["diverge", "merge", "weave", "weave2"]
ENV_LABEL = {"diverge": "Diverge (D)", "merge": "Merge (M)", "weave": "Weave MD-1f", "weave2": "Weave MD-2"}
COLORS = plt.rcParams["axes.prop_cycle"].by_key()["color"]


def _load() -> pd.DataFrame:
    if not LONG_CSV.exists():
        raise SystemExit(f"{LONG_CSV} がありません（先に aggregate.py を実行）。")
    return pd.read_csv(LONG_CSV)


def fig1_scenario_deadline(df: pd.DataFrame) -> None:
    """メッセージA: 4つの必須LCシナリオで締切達成率が一様に高い（単一手法で対応）。"""
    d = df[df["scenario"].isin(MLC_ENVS)].dropna(subset=["deadline_rate"])
    if d.empty:
        print("[fig1] 必須LCデータなし。スキップ")
        return
    order = [e for e in MLC_ENVS if e in d["scenario"].unique()]
    means = d.groupby("scenario")["deadline_rate"].mean().reindex(order)
    stds = d.groupby("scenario")["deadline_rate"].std().reindex(order)
    ns = d.groupby("scenario")["deadline_rate"].size().reindex(order)

    fig, ax = plt.subplots(figsize=(7, 4.2))
    x = range(len(order))
    ax.bar(x, means.values * 100, yerr=stds.values * 100, capsize=5,
           color=COLORS[: len(order)], edgecolor="black", linewidth=0.6)
    ax.set_xticks(list(x))
    ax.set_xticklabels([ENV_LABEL.get(e, e) for e in order])
    ax.set_ylabel("Mandatory-LC deadline completion rate [%]")
    ax.set_ylim(0, 105)
    ax.axhline(100, color="gray", lw=0.8, ls="--")
    ax.set_title("Single controller across multiple mandatory-LC scenarios")
    for xi, m, n in zip(x, means.values, ns.values, strict=True):
        ax.text(xi, m * 100 + 1.5, f"{m * 100:.1f}%\n(n={int(n)})", ha="center", va="bottom", fontsize=9)
    fig.tight_layout()
    p = FIG_DIR / "fig1_scenario_deadline.png"
    fig.savefig(p, dpi=160)
    plt.close(fig)
    print(f"[fig1] {p}")


def fig2_robustness_deadline(df: pd.DataFrame) -> None:
    """メッセージB: Q と f を変えても締切達成率が高位で安定（小倍数：環境ごと1枚）。"""
    d = df[df["scenario"].isin(MLC_ENVS)].dropna(subset=["deadline_rate"])
    if d.empty:
        print("[fig2] 必須LCデータなし。スキップ")
        return
    envs = [e for e in MLC_ENVS if e in d["scenario"].unique()]
    fig, axes = plt.subplots(2, 2, figsize=(10, 7), sharex=True, sharey=True)
    axes = axes.ravel()
    for i, env in enumerate(envs):
        ax = axes[i]
        de = d[d["scenario"] == env]
        for j, fval in enumerate(sorted(de["f"].unique())):
            sub = de[de["f"] == fval]
            g = sub.groupby("q")["deadline_rate"].agg(["mean", "std"]).reset_index().sort_values("q")
            ax.errorbar(g["q"], g["mean"] * 100, yerr=(g["std"] * 100).fillna(0), marker="o",
                        capsize=3, color=COLORS[j % len(COLORS)], label=f"f={fval}")
        ax.set_title(ENV_LABEL.get(env, env))
        ax.set_ylim(0, 105)
        ax.axhline(100, color="gray", lw=0.7, ls="--")
        ax.grid(True, alpha=0.3)
        if i % 2 == 0:
            ax.set_ylabel("Deadline completion [%]")
        if i >= 2:
            ax.set_xlabel("Inflow Q [veh/h]")
    for k in range(len(envs), len(axes)):
        axes[k].set_visible(False)
    axes[0].legend(title="MLC ratio", fontsize=9, loc="lower left")
    fig.suptitle("Robustness to inflow Q and mandatory-LC ratio f", y=0.99)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    p = FIG_DIR / "fig2_robustness_deadline.png"
    fig.savefig(p, dpi=160)
    plt.close(fig)
    print(f"[fig2] {p}")


def fig_safety(df: pd.DataFrame) -> None:
    """安全性の作動包絡: 流入量 Q を上げると衝突が増える（高密度で協調挿入の安全余裕が縮む）。

    左: 衝突件数/run vs Q（環境別の線）。右: 衝突0 run 比率 vs Q。
    低負荷では衝突0に近く、Q とともに劣化する＝物理的に筋の通った安全包絡を示す。
    """
    envs = [e for e in MLC_ENVS if e in df["scenario"].unique()]
    d = df[df["scenario"].isin(envs)]
    if d.empty or "collisions" not in d.columns:
        print("[safety] データなし。スキップ")
        return
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.2))
    for j, env in enumerate(envs):
        de = d[d["scenario"] == env]
        cpr = de.groupby("q")["collisions"].mean().reset_index().sort_values("q")
        a1.plot(cpr["q"], cpr["collisions"], marker="o", color=COLORS[j % len(COLORS)], label=ENV_LABEL.get(env, env))
        zf = de.assign(z=(de["collisions"] == 0)).groupby("q")["z"].mean().mul(100).reset_index().sort_values("q")
        a2.plot(zf["q"], zf["z"], marker="s", color=COLORS[j % len(COLORS)], label=ENV_LABEL.get(env, env))
    a1.set_xlabel("Inflow Q [veh/h]")
    a1.set_ylabel("Collisions per run (mean)")
    a1.set_title("Collisions grow with congestion")
    a1.grid(True, alpha=0.3)
    a1.legend(fontsize=8)
    a2.set_xlabel("Inflow Q [veh/h]")
    a2.set_ylabel("Collision-free runs [%]")
    a2.set_ylim(0, 105)
    a2.set_title("Collision-free run share vs load")
    a2.grid(True, alpha=0.3)
    a2.legend(fontsize=8)
    fig.suptitle("Safety envelope: high deadline completion holds, but safety margin shrinks under heavy load", y=0.99, fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    p = FIG_DIR / "fig_safety.png"
    fig.savefig(p, dpi=160)
    plt.close(fig)
    print(f"[safety] {p}")


def fig_straight_obstacle(df: pd.DataFrame) -> None:
    """直進路の突発障害物（中央車線封鎖）への動的回避: 負荷別の安全性と交通流の維持。

    straight の回避LCは is_avoidance で締切達成率の母数外のため達成率図には出ない。代わりに
    「衝突0率（安全に回避できたか）」と「スループット（封鎖後も流れを捌けたか）」を Q 別に示す。
    """
    d = df[df["scenario"] == "straight_obs"]
    if d.empty:
        print("[straight] データなし。スキップ")
        return
    cfree = d.assign(z=(d["collisions"] == 0)).groupby("q")["z"].mean().mul(100).reset_index().sort_values("q")
    thr = d.groupby("q")["throughput"].mean().reset_index().sort_values("q")
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.2))
    a1.plot(cfree["q"], cfree["z"], marker="s", color="#5cb85c")
    a1.set_xlabel("Inflow Q [veh/h]")
    a1.set_ylabel("Collision-free runs [%]")
    a1.set_ylim(0, 105)
    a1.set_title("Safe avoidance of mid-lane blockage")
    a1.grid(True, alpha=0.3)
    a2.plot(thr["q"], thr["throughput"], marker="o", color=COLORS[0], label="served")
    a2.plot([thr["q"].min(), thr["q"].max()], [thr["q"].min(), thr["q"].max()], "k--", lw=0.7, label="offered")
    a2.set_xlabel("Inflow Q [veh/h]")
    a2.set_ylabel("Throughput [veh/h]")
    a2.set_title("Flow maintained past the blockage")
    a2.grid(True, alpha=0.3)
    a2.legend(fontsize=9)
    fig.suptitle("Straight + sudden mid-lane obstacle (dynamic escalation avoidance)", y=0.99, fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    p = FIG_DIR / "fig_straight_obstacle.png"
    fig.savefig(p, dpi=160)
    plt.close(fig)
    print(f"[straight] {p}")


def fig3_baseline_diverge(df: pd.DataFrame) -> None:
    """分流での交通効率比較 v2 vs v1（baseline scenario のみ）。"""
    d = df[df["scenario"].astype(str).str.contains("baseline")]
    if d.empty:
        print("[fig3] baseline データなし。スキップ（baseline スイート未実行）")
        return
    methods = sorted(d["method"].unique())
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.2))
    for j, m in enumerate(methods):
        dm = d[d["method"] == m]
        g = dm.groupby("q").agg(spd=("avg_speed", "mean"), spd_s=("avg_speed", "std"),
                                thr=("throughput", "mean")).reset_index().sort_values("q")
        a1.errorbar(g["q"], g["spd"], yerr=g["spd_s"].fillna(0), marker="o", capsize=3,
                    color=COLORS[j % len(COLORS)], label=m)
        a2.plot(g["q"], g["thr"], marker="s", color=COLORS[j % len(COLORS)], label=m)
    a1.set_xlabel("Inflow Q [veh/h]")
    a1.set_ylabel("Average speed [m/s]")
    a1.set_title("Traffic efficiency: average speed")
    a1.grid(True, alpha=0.3)
    a1.legend()
    a2.set_xlabel("Inflow Q [veh/h]")
    a2.set_ylabel("Throughput [veh/h]")
    a2.set_title("Throughput (served)")
    a2.grid(True, alpha=0.3)
    a2.plot([d["q"].min(), d["q"].max()], [d["q"].min(), d["q"].max()], "k--", lw=0.7, label="offered")
    a2.legend()
    fig.suptitle("Diverge efficiency: proposed (v2) vs baseline (v1)", y=0.995)
    fig.text(
        0.5, 0.005,
        "Caveat: geometry differs (v1 net 2496 m free-flow, no tight deadline; v2 diverge 1000 m with hard LC deadline). "
        "Throughput is comparable up to ~3500 veh/h; absolute speed is NOT a clean head-to-head.",
        ha="center", va="bottom", fontsize=8, color="#555555", wrap=True,
    )
    fig.tight_layout(rect=(0, 0.06, 1, 0.95))
    p = FIG_DIR / "fig3_baseline_diverge.png"
    fig.savefig(p, dpi=160)
    plt.close(fig)
    print(f"[fig3] {p}")


def main() -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    df = _load()
    fig1_scenario_deadline(df)
    fig2_robustness_deadline(df)
    fig_safety(df)
    fig_straight_obstacle(df)
    fig3_baseline_diverge(df)
    print(f"\n[make_figures] 図は {FIG_DIR} に出力しました。")


if __name__ == "__main__":
    main()
