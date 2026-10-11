#!/usr/bin/env python3
"""柱B（アブレーション比較）の表・図・内訳を summary_long.csv から生成する。

比較する3手法（--policy）:
  v2      … 提案手法（EDF統一調停）
  v2-none … 優先度なし（発生順＋最近傍後続、displacementなし）
  v2-off  … 非協調（SUMO 標準 LC2013 に委譲）

公平性のため、表・図はいずれも3手法が揃う seed 集合（既定 1-3）に限定して対で比較する
（提案の seed4,5 の結果は柱Aの主表側で全量を使う。ここで混ぜると n が手法間で食い違う）。

出力（out/ablation_b/）:
  - table_scenario_method{,_envelope}.csv/.tex … シナリオ×3手法の締切達成率 [%]（全グリッド／作動包絡内）
  - breakdown_method_scenario_q.csv            … 未完了・衝突の内訳（手法×シナリオ×Q。f・seed 合計）
  - stuck_ledger.csv                           … none/off の未完了個票の連結（デッドロック様立ち往生の証跡）
  - fig_b_unfinished_{weave2,weave}.png/.pdf   … 未完了要求数 [台/試行] vs 総流入 Q [台/h]（3手法）

依存: pandas / matplotlib（導入済み）。日本語ラベルはシステムの Noto Sans CJK JP を使う。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
OUT_DIR = SCRIPT_DIR / "out"
RAW_DIR = OUT_DIR / "raw"
B_DIR = OUT_DIR / "ablation_b"
LONG_CSV = OUT_DIR / "summary_long.csv"

MLC_ENVS = ["diverge", "merge", "weave", "weave2"]
ENV_LABEL_JA = {"diverge": "分流", "merge": "合流", "weave": "weave", "weave2": "weave2"}

# 手法の表示順・ラベル（論文の列順=提案手法/優先度なし/非協調 に固定。
# 非協調は事前周知(off)と遅通知(off-late)の2変種を採取。遅通知が提案と同じ情報タイミングの公平条件）
METHODS = ["v2", "v2-none", "v2-off", "v2-off-late"]
METHOD_LABEL_JA = {
    "v2": "提案手法",
    "v2-none": "優先度なし",
    "v2-off": "非協調(事前周知)",
    "v2-off-late": "非協調(遅通知)",
}

# 3手法が揃う seed 集合（提案は 1-5 を持つが、比較は同一 seed で対にする。
# 実行時間の制約で none/off は seed1 のみ採取＝2026-07-24 ユーザー判断）
PAIRED_SEEDS = [1]

# 作動包絡（aggregate.py と同一定義）: 主表の枠組みを柱Bでも踏襲する
OPERATING_ENVELOPE_QMAX = {"weave": 3000, "weave2": 3500}

# Okabe-Ito（Wong 2011, CVD-safe）から4色。色だけに頼らずマーカー・線種を併用する（図仕様）
METHOD_STYLE = {
    "v2": {"color": "#0072B2", "marker": "o", "linestyle": "-"},
    "v2-none": {"color": "#E69F00", "marker": "s", "linestyle": "--"},
    "v2-off": {"color": "#009E73", "marker": "^", "linestyle": "-."},
    "v2-off-late": {"color": "#D55E00", "marker": "D", "linestyle": ":"},
}


def _setup_japanese_font() -> None:
    """システムの Noto Sans CJK JP で日本語ラベルを描く（見つからなければ明示エラー）。"""
    from matplotlib import font_manager

    names = {f.name for f in font_manager.fontManager.ttflist}
    if "Noto Sans CJK JP" not in names:
        raise SystemExit("日本語フォント 'Noto Sans CJK JP' が見つかりません（全図英語ラベルへの切替を検討）")
    plt.rcParams["font.family"] = "Noto Sans CJK JP"
    plt.rcParams["axes.unicode_minus"] = False


def _load() -> pd.DataFrame:
    if not LONG_CSV.exists():
        raise SystemExit(f"{LONG_CSV} がありません（先に aggregate.py を実行）。")
    df = pd.read_csv(LONG_CSV)
    d = df[df["method"].isin(METHODS) & df["scenario"].isin(MLC_ENVS)].copy()
    d = d[d["seed"].astype(int).isin(PAIRED_SEEDS)]
    missing = {(m, e) for m in METHODS for e in MLC_ENVS} - set(zip(d["method"], d["scenario"], strict=False))
    if missing:
        raise SystemExit(f"手法×シナリオのデータが欠けています: {sorted(missing)}")
    d["in_envelope"] = d["q"] <= d["scenario"].map(OPERATING_ENVELOPE_QMAX).fillna(float("inf"))
    return d


def _rate_table(d: pd.DataFrame) -> pd.DataFrame:
    """シナリオ×手法の締切達成率 [%]（要求総数ベース: Σcompleted/Σtotal。run 平均でなく要求平均）。"""
    g = (
        d.groupby(["scenario", "method"])
        .agg(total=("mlc_total", "sum"), completed=("mlc_completed", "sum"), n_runs=("seed", "size"))
        .reset_index()
    )
    g["rate_pct"] = g["completed"] / g["total"] * 100.0
    return g


def _write_table(g: pd.DataFrame, stem: str, note: str) -> None:
    wide = g.pivot(index="scenario", columns="method", values="rate_pct").reindex(MLC_ENVS)[METHODS]
    totals = g.pivot(index="scenario", columns="method", values="total").reindex(MLC_ENVS)[METHODS]
    wide.to_csv(B_DIR / f"{stem}.csv", float_format="%.1f")

    # 論文スタイル: booktabs なし・\hline\hline 先頭・\hline 末尾。
    # 列構成コメント: 柱Aの主表（シナリオ行×達成率1列）に「優先度なし/非協調」列を足した形
    lines = [
        f"% {note}",
        "% 列順 = 提案手法 / 優先度なし / 非協調（柱Aの主表に列追加する構成。非協調は事前周知/遅通知の2変種）",
        f"\\begin{{tabular}}{{l{'r' * len(METHODS)}}}",
        "\\hline \\hline",
        "シナリオ & " + " & ".join(METHOD_LABEL_JA[m] for m in METHODS) + " \\\\",
        "\\hline",
    ]
    for env in MLC_ENVS:
        cells = " & ".join(f"{wide.loc[env, m]:.1f}" for m in METHODS)
        lines.append(f"{ENV_LABEL_JA[env]} & {cells} \\\\")
    lines += ["\\hline", "\\end{tabular}"]
    (B_DIR / f"{stem}.tex").write_text("\n".join(lines) + "\n")
    print(f"[table] {B_DIR / stem}.csv / .tex")
    print(wide.round(1).to_string(), "\n(要求総数)", totals.to_string(), sep="\n")


def _write_breakdown(d: pd.DataFrame) -> None:
    """未完了・衝突の内訳（手法×シナリオ×Q。f・seed 合計）。"""
    g = (
        d.groupby(["method", "scenario", "q"])
        .agg(
            n_runs=("seed", "size"),
            mlc_total=("mlc_total", "sum"),
            mlc_completed=("mlc_completed", "sum"),
            mlc_incomplete=("mlc_incomplete", "sum"),
            mlc_collided=("mlc_collided", "sum"),
            collisions=("collisions", "sum"),
            incomplete_per_run=("mlc_incomplete", "mean"),
        )
        .reset_index()
        .sort_values(["method", "scenario", "q"])
    )
    g["rate_pct"] = (g["mlc_completed"] / g["mlc_total"] * 100.0).round(2)
    p = B_DIR / "breakdown_method_scenario_q.csv"
    g.to_csv(p, index=False)
    print(f"[breakdown] {p}")


def _write_stuck_ledger() -> None:
    """none/off の失敗個票（__failures.csv）を連結し、立ち往生（TIMEOUT_STUCK）の証跡を1枚にする。"""
    frames: list[pd.DataFrame] = []
    for path in sorted(RAW_DIR.glob("v2-*__failures.csv")):
        t = pd.read_csv(path)
        t.insert(0, "run", path.name.replace("__failures.csv", ""))
        frames.append(t)
    if not frames:
        print("[stuck] none/off の失敗個票なし")
        return
    ledger = pd.concat(frames, ignore_index=True)
    p = B_DIR / "stuck_ledger.csv"
    ledger.to_csv(p, index=False)
    n_stuck = int((ledger["classification"] == "TIMEOUT_STUCK").sum())
    print(f"[stuck] {p}  rows={len(ledger)} (TIMEOUT_STUCK={n_stuck})")


def _fig_unfinished_vs_q(d: pd.DataFrame, env: str) -> None:
    """未完了要求数 [台/試行] vs 総流入 Q [台/h]（3手法、f・seed 平均）。論文仕様（300dpi PNG＋PDF・無タイトル）。"""
    de = d[d["scenario"] == env]
    fig, ax = plt.subplots(figsize=(4.5, 3.2))
    for m in METHODS:
        g = de[de["method"] == m].groupby("q")["mlc_incomplete"].mean().reset_index().sort_values("q")
        st = METHOD_STYLE[m]
        ax.plot(
            g["q"],
            g["mlc_incomplete"],
            color=st["color"],
            marker=st["marker"],
            linestyle=st["linestyle"],
            linewidth=1.6,
            markersize=6,
            label=METHOD_LABEL_JA[m],
        )
    ax.set_xlabel("総流入 $Q$ [台/h]", fontsize=12)
    ax.set_ylabel("未完了要求数 [台/試行]", fontsize=12)
    ax.tick_params(labelsize=11)
    ax.set_ylim(bottom=0)
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=11)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        p = B_DIR / f"fig_b_unfinished_{env}.{ext}"
        fig.savefig(p, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"[fig] {B_DIR / f'fig_b_unfinished_{env}'}.png/.pdf")


def main() -> None:
    B_DIR.mkdir(parents=True, exist_ok=True)
    _setup_japanese_font()
    d = _load()

    _write_table(
        _rate_table(d),
        "table_scenario_method",
        f"全グリッド（Q1500-4000 × f0.2-0.6 × seed{PAIRED_SEEDS}）の締切達成率 [%]",
    )
    _write_table(
        _rate_table(d[d["in_envelope"]]),
        "table_scenario_method_envelope",
        f"作動包絡内（weave Q≤{OPERATING_ENVELOPE_QMAX['weave']} / weave2 Q≤{OPERATING_ENVELOPE_QMAX['weave2']}"
        f" / 他は全グリッド）× seed{PAIRED_SEEDS} の締切達成率 [%]",
    )
    _write_breakdown(d)
    _write_stuck_ledger()
    _fig_unfinished_vs_q(d, "weave2")
    _fig_unfinished_vs_q(d, "weave")
    print(f"\n[make_ablation_b] 出力先: {B_DIR}")


if __name__ == "__main__":
    main()
