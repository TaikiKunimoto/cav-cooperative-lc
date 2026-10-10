#!/usr/bin/env python3
"""柱B-2 の表・図を生成する（実験1=突発障害物比較／実験2=活性化位置の限界比較）。

入力:
  - 実験1: out/raw/ の straight_obs run（メインCSV＋ __obstacle_summary/__obstacle_avoidance sidecar）
  - 実験2: out/ablation_b2/raw_margin/ の run（メインCSV）
出力: out/ablation_b2/ に CSV・論文スタイル tex 断片・図（300dpi PNG＋PDF・日本語ラベル・無タイトル）。

結果を先取りしない: 図表は全条件をそのまま載せ、条件の取捨選択はしない。
"""

from __future__ import annotations

import csv
from pathlib import Path
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
OUT_DIR = SCRIPT_DIR / "out"
RAW_DIR = OUT_DIR / "raw"
B2_DIR = OUT_DIR / "ablation_b2"
MARGIN_RAW = B2_DIR / "raw_margin"

METHODS_EXP1 = ["v2", "v2-off-late", "v2-none"]
METHODS_EXP2 = ["v2", "v2-off-late"]
METHOD_LABEL = {"v2": "提案手法", "v2-none": "優先度なし", "v2-off-late": "非協調(遅通知)"}
METHOD_STYLE = {
    "v2": {"color": "#0072B2", "marker": "o", "linestyle": "-"},
    "v2-none": {"color": "#E69F00", "marker": "s", "linestyle": "--"},
    "v2-off-late": {"color": "#D55E00", "marker": "D", "linestyle": ":"},
}
ENV_LABEL = {"weave": "weave", "weave2": "weave2"}

EXP1_NAME = re.compile(r"^(?P<method>v2(?:-[a-z-]+)?)__straight_obs__Q(?P<q>\d+)__f0\.0__s(?P<seed>\d)__obs1-500-60$")
EXP2_NAME = re.compile(
    r"^(?P<method>v2(?:-[a-z-]+)?)__(?P<env>weave2?)__Q(?P<q>\d+)__f(?P<f>[\d.]+)__s(?P<seed>\d)__am(?P<am>\d+)$"
)


def _setup_japanese_font() -> None:
    from matplotlib import font_manager

    if "Noto Sans CJK JP" not in {f.name for f in font_manager.fontManager.ttflist}:
        raise SystemExit("日本語フォント 'Noto Sans CJK JP' が見つかりません")
    plt.rcParams["font.family"] = "Noto Sans CJK JP"
    plt.rcParams["axes.unicode_minus"] = False


def _read_last_row(path: Path) -> dict[str, str]:
    rows = list(csv.DictReader(open(path)))
    if not rows:
        raise SystemExit(f"データ行がありません: {path}")
    return rows[-1]


def _save_fig(fig: plt.Figure, stem: str) -> None:
    for ext in ("png", "pdf"):
        fig.savefig(B2_DIR / f"{stem}.{ext}", dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"[fig] {B2_DIR / stem}.png/.pdf")


# ---------------- 実験1: 突発障害物 ----------------


def load_exp1() -> pd.DataFrame:
    rows = []
    for path in sorted(RAW_DIR.glob("*__straight_obs__*__obs1-500-60.csv")):
        m = EXP1_NAME.match(path.stem)
        if m is None or m["method"] not in METHODS_EXP1:
            continue
        summary_path = path.with_name(path.stem + "__obstacle_summary.csv")
        if not summary_path.exists():
            raise SystemExit(f"障害物 sidecar がありません（旧計測の run が混在？）: {summary_path}")
        main = _read_last_row(path)
        s = _read_last_row(summary_path)
        rows.append(
            {
                "method": m["method"],
                "q": int(m["q"]),
                "seed": int(m["seed"]),
                "throughput_post": float(s["throughput_post_vph"]),
                "avg_speed_post": float(s["avg_speed_post_ms"]),
                "max_queue_m": float(s["max_queue_m"]),
                "hard_brake": int(s["hard_brake_events"]),
                "avoid_requests": int(s["avoid_requests"]),
                "avoid_stuck": int(s["avoid_stuck_at_end"]),
                "collisions": float(main["total_collisions"]),
                "running_end": float(main["running_vehicles"]),
            }
        )
    if not rows:
        raise SystemExit("実験1の run が見つかりません")
    return pd.DataFrame(rows)


def exp1_tables_and_figs(df: pd.DataFrame) -> None:
    agg = (
        df.groupby(["method", "q"])
        .agg(
            n=("seed", "size"),
            throughput_post=("throughput_post", "mean"),
            avg_speed_post=("avg_speed_post", "mean"),
            max_queue_m=("max_queue_m", "mean"),
            hard_brake=("hard_brake", "mean"),
            avoid_requests=("avoid_requests", "mean"),
            avoid_stuck=("avoid_stuck", "sum"),
            collisions=("collisions", "sum"),
        )
        .reset_index()
        .sort_values(["method", "q"])
    )
    agg.to_csv(B2_DIR / "exp1_obstacle_summary.csv", index=False, float_format="%.1f")
    print(f"[table] {B2_DIR / 'exp1_obstacle_summary.csv'}")

    # 論文スタイル tex: Q別 × 手法（急減速と封鎖通過スループット）
    methods = [m for m in METHODS_EXP1 if m in set(agg["method"])]
    piv_tp = agg.pivot(index="q", columns="method", values="throughput_post")
    piv_hb = agg.pivot(index="q", columns="method", values="hard_brake")
    lines = [
        "% 実験1: 突発障害物（straight, 障害物=中央車線500m/60s, f=0, seed1-3平均）",
        "% 左群=封鎖通過スループット [台/h]（t>=60s）／右群=急減速イベント数 [回/試行]（加速度<=-3m/s^2）",
        f"\\begin{{tabular}}{{r{'r' * len(methods)}{'r' * len(methods)}}}",
        "\\hline \\hline",
        " & \\multicolumn{%d}{c}{スループット [台/h]} & \\multicolumn{%d}{c}{急減速 [回/試行]} \\\\"
        % (len(methods), len(methods)),
        "$Q$ [台/h] & "
        + " & ".join(METHOD_LABEL[m] for m in methods)
        + " & "
        + " & ".join(METHOD_LABEL[m] for m in methods)
        + " \\\\",
        "\\hline",
    ]
    for q in sorted(piv_tp.index):
        tp = " & ".join(f"{piv_tp.loc[q, m]:.0f}" for m in methods)
        hb = " & ".join(f"{piv_hb.loc[q, m]:.0f}" for m in methods)
        lines.append(f"{q} & {tp} & {hb} \\\\")
    lines += ["\\hline", "\\end{tabular}"]
    (B2_DIR / "exp1_obstacle_summary.tex").write_text("\n".join(lines) + "\n")

    # 図案1a: 急減速イベント数 vs Q ／ 図案1b: 封鎖通過スループット vs Q
    for stem, col, ylabel in (
        ("fig_b2_hard_brake", "hard_brake", "急減速イベント数 [回/試行]"),
        ("fig_b2_throughput", "throughput_post", "封鎖通過スループット [台/h]"),
        ("fig_b2_queue", "max_queue_m", "最大待ち行列長 [m]"),
    ):
        fig, ax = plt.subplots(figsize=(4.5, 3.2))
        for m in methods:
            g = agg[agg["method"] == m]
            st = METHOD_STYLE[m]
            ax.plot(g["q"], g[col], label=METHOD_LABEL[m], linewidth=1.6, markersize=6, **st)
        ax.set_xlabel("総流入 $Q$ [台/h]", fontsize=12)
        ax.set_ylabel(ylabel, fontsize=12)
        ax.tick_params(labelsize=11)
        ax.set_ylim(bottom=0)
        ax.grid(True, alpha=0.25)
        ax.legend(fontsize=10)
        fig.tight_layout()
        _save_fig(fig, stem)

    # 回避LC完了位置（障害物までの残距離）のCDF（全Q・全seedプール）
    fig, ax = plt.subplots(figsize=(4.5, 3.2))
    for m in methods:
        remaining: list[float] = []
        for path in sorted(RAW_DIR.glob(f"{m}__straight_obs__*__obs1-500-60__obstacle_avoidance.csv")):
            remaining += [float(r["remaining_to_obstacle_m"]) for r in csv.DictReader(open(path))]
        if not remaining:
            continue
        xs = sorted(remaining)
        ys = [100.0 * (i + 1) / len(xs) for i in range(len(xs))]
        st = METHOD_STYLE[m]
        ax.plot(
            xs,
            ys,
            label=f"{METHOD_LABEL[m]} (n={len(xs)})",
            linewidth=1.6,
            color=st["color"],
            linestyle=st["linestyle"],
        )
    ax.set_xlabel("回避完了位置の障害物までの残距離 [m]", fontsize=12)
    ax.set_ylabel("累積割合 [%]", fontsize=12)
    ax.tick_params(labelsize=11)
    ax.set_ylim(0, 100)
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=9)
    fig.tight_layout()
    _save_fig(fig, "fig_b2_avoid_position_cdf")


# ---------------- 実験2: 活性化位置 ----------------


def load_exp2() -> pd.DataFrame:
    rows = []
    for path in sorted(MARGIN_RAW.glob("*__am*.csv")):
        m = EXP2_NAME.match(path.stem)
        if m is None or m["method"] not in METHODS_EXP2:
            continue
        if int(m["seed"]) != 1:
            continue  # 全マージン水準で seed1 に統一（am400 のみ流用の seed2-3 が存在するため揃える）
        r = _read_last_row(path)
        rows.append(
            {
                "method": m["method"],
                "env": m["env"],
                "f": float(m["f"]),
                "seed": int(m["seed"]),
                "margin": int(m["am"]),
                "req": int(r["mandatory_lc_total"]),
                "comp": int(r["mandatory_lc_completed"]),
                "collisions": float(r["total_collisions"]),
            }
        )
    if not rows:
        raise SystemExit("実験2の run が見つかりません")
    return pd.DataFrame(rows)


def exp2_tables_and_figs(df: pd.DataFrame) -> None:
    agg = (
        df.groupby(["method", "env", "margin"])
        .agg(n=("seed", "size"), req=("req", "sum"), comp=("comp", "sum"), collisions=("collisions", "sum"))
        .reset_index()
    )
    agg["rate_pct"] = agg["comp"] / agg["req"] * 100.0
    agg["incomplete"] = agg["req"] - agg["comp"]
    agg.sort_values(["method", "env", "margin"], ascending=[True, True, False]).to_csv(
        B2_DIR / "exp2_margin_summary.csv", index=False, float_format="%.2f"
    )
    print(f"[table] {B2_DIR / 'exp2_margin_summary.csv'}")

    margins = sorted(agg["margin"].unique(), reverse=True)
    lines = [
        "% 実験2: 活性化位置（猶予距離）の限界比較（weave/weave2, Q3000, f=0.4/0.6, seed1-3, 達成率[%]）",
        f"\\begin{{tabular}}{{ll{'r' * len(margins)}}}",
        "\\hline \\hline",
        "手法 & 環境 & " + " & ".join(f"{m}m" for m in margins) + " \\\\",
        "\\hline",
    ]
    for method in METHODS_EXP2:
        for env in ("weave", "weave2"):
            sel = agg[(agg["method"] == method) & (agg["env"] == env)].set_index("margin")
            if sel.empty:
                continue
            cells = " & ".join(f"{sel.loc[mg, 'rate_pct']:.1f}" if mg in sel.index else "-" for mg in margins)
            lines.append(f"{METHOD_LABEL[method]} & {ENV_LABEL[env]} & {cells} \\\\")
    lines += ["\\hline", "\\end{tabular}"]
    (B2_DIR / "exp2_margin_summary.tex").write_text("\n".join(lines) + "\n")

    # 図案2: 活性化位置 vs 達成率（手法=色・マーカー、環境=線種）
    fig, ax = plt.subplots(figsize=(4.5, 3.2))
    env_ls = {"weave": "-", "weave2": "--"}
    for method in METHODS_EXP2:
        for env in ("weave", "weave2"):
            sel = agg[(agg["method"] == method) & (agg["env"] == env)].sort_values("margin")
            if sel.empty:
                continue
            st = METHOD_STYLE[method]
            ax.plot(
                sel["margin"],
                sel["rate_pct"],
                color=st["color"],
                marker=st["marker"],
                linestyle=env_ls[env],
                linewidth=1.6,
                markersize=6,
                label=f"{METHOD_LABEL[method]}・{ENV_LABEL[env]}",
            )
    ax.set_xlabel("要求の活性化位置 [m]", fontsize=12)
    ax.set_ylabel("締切達成率 [%]", fontsize=12)
    ax.tick_params(labelsize=11)
    ax.set_ylim(0, 105)  # 図仕様: 達成率の y 軸は 0–100 を基本（拡大する場合は FINDINGS に明記）
    ax.invert_xaxis()  # 猶予が縮む方向（400→100）へ読み進める
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=9)
    fig.tight_layout()
    _save_fig(fig, "fig_b2_margin_rate")


def main() -> None:
    B2_DIR.mkdir(parents=True, exist_ok=True)
    _setup_japanese_font()
    exp1_tables_and_figs(load_exp1())
    try:
        exp2_tables_and_figs(load_exp2())
    except SystemExit as e:
        print(f"[exp2] スキップ: {e}")
    print(f"\n[make_b2_outputs] 出力先: {B2_DIR}")


if __name__ == "__main__":
    main()
