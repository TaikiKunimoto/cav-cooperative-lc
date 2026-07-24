#!/usr/bin/env python3
"""柱A（5シナリオ統一評価）の論文用表を summary_long.csv から生成する。

出力（out/tables/）:
  - pillarA_main.csv / .tex          … 主表: シナリオ5行×全条件平均の締切達成率 [%]（作動包絡内・主結果）
  - pillarA_main_fullgrid.csv / .tex … 同・全グリッド版（超過需要域込み。作動限界の明示用の参考表）
  - pillarA_byQ.csv / .tex           … Q別内訳: シナリオ×Q（f・seed 平均）。包絡外セルは † を付す

達成率は「発生した要求（必須LC＋障害物回避）のうち締切内に完了した割合」＝ Σcompleted / Σtotal
（run 平均でなく要求数で重み付け）。%・小数1桁で表記する。

.tex は論文の体裁（\\begin{tabular}{l...} ＋ 先頭 \\hline \\hline ＋ 末尾 \\hline、booktabs 不使用）の断片。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from aggregate import OPERATING_ENVELOPE_QMAX

SCRIPT_DIR = Path(__file__).resolve().parent
OUT_DIR = SCRIPT_DIR / "out"
TABLE_DIR = OUT_DIR / "tables"

# 主表の行順とラベル（論文表記）。straight_obs が障害物封鎖（B）。
SCENARIO_LABELS = {
    "diverge": "分流",
    "merge": "合流",
    "straight_obs": "障害物封鎖",
    "weave": "weave",
    "weave2": "weave2",
}
SCENARIO_ORDER = ["diverge", "merge", "straight_obs", "weave", "weave2"]


def load_proposed_long() -> pd.DataFrame:
    """summary_long.csv から提案手法（v2）の5シナリオ run を読み込む。"""
    long_path = OUT_DIR / "summary_long.csv"
    if not long_path.exists():
        raise SystemExit(f"summary_long.csv が見つかりません: {long_path}（先に aggregate.py を実行）")
    df = pd.read_csv(long_path)
    df = df[(df["method"] == "v2") & (df["scenario"].isin(SCENARIO_ORDER))].copy()
    if df.empty:
        raise SystemExit("提案手法（v2）の5シナリオ run が summary_long.csv にありません。")
    if df["req_total"].isna().any():
        bad = df[df["req_total"].isna()][["scenario", "q", "f", "seed"]]
        raise SystemExit(f"req_total 欠損の run があります（旧スキーマ CSV の混入？）:\n{bad}")
    df["in_envelope"] = df["q"] <= df["scenario"].map(OPERATING_ENVELOPE_QMAX).fillna(float("inf"))
    return df


def rate_pct(completed: float, total: float) -> float | None:
    """達成率 [%]（要求ゼロなら None）。"""
    return None if total == 0 else 100.0 * completed / total


def fmt_pct(v: float | None, mark: str = "") -> str:
    return "--" if v is None else f"{v:.1f}{mark}"


def main_table(df: pd.DataFrame) -> pd.DataFrame:
    """主表: シナリオ別の 発生要求数・完了数・達成率[%]（Q/f/seed 全条件を要求数重みで集約）。"""
    g = df.groupby("scenario")[["req_total", "req_completed"]].sum()
    g = g.reindex(SCENARIO_ORDER).dropna()
    out = pd.DataFrame(
        {
            "scenario": g.index,
            "label": [SCENARIO_LABELS[s] for s in g.index],
            "requests": g["req_total"].astype(int).to_numpy(),
            "completed": g["req_completed"].astype(int).to_numpy(),
        }
    )
    out["achievement_pct"] = [rate_pct(c, t) for c, t in zip(out["completed"], out["requests"], strict=True)]
    return out


def main_tex(tbl: pd.DataFrame, caption_note: str) -> str:
    """主表の LaTeX 断片。後でアブレーション列を足せる列構成（コメント参照）。"""
    lines = [
        f"% 柱A 主表: 5シナリオ統一評価（提案手法）。{caption_note}",
        "% 達成率 = 発生した必須LC要求（合流M/分流D/障害物回避B/織込み）のうち締切内に完了した割合。",
        "% 【アブレーション列の足し方（柱B）】列指定を {lrr} → {lrrrr} にし、ヘッダを",
        "%   シナリオ & 要求数 & 提案手法 & 優先度なし & 非協調 \\\\ に替えて各行へ2列追記する。",
        "\\begin{tabular}{lrr}",
        "\\hline \\hline",
        "シナリオ & 要求数 & 締切達成率 [\\%] \\\\",
        "\\hline",
    ]
    for _, r in tbl.iterrows():
        lines.append(f"{r['label']} & {r['requests']} & {fmt_pct(r['achievement_pct'])} \\\\")
    lines += ["\\hline", "\\end{tabular}"]
    return "\n".join(lines) + "\n"


def by_q_table(df: pd.DataFrame) -> pd.DataFrame:
    """Q別内訳: シナリオ×Q の達成率[%]（f・seed を要求数重みで集約）。包絡外は envelope=False。"""
    g = df.groupby(["scenario", "q"]).agg(
        req_total=("req_total", "sum"),
        req_completed=("req_completed", "sum"),
        in_envelope=("in_envelope", "first"),
        n_runs=("seed", "size"),
    )
    g = g.reset_index()
    g["achievement_pct"] = [rate_pct(c, t) for c, t in zip(g["req_completed"], g["req_total"], strict=True)]
    return g


def by_q_tex(byq: pd.DataFrame) -> str:
    """Q別内訳の LaTeX 断片（行=シナリオ、列=Q）。包絡外（超過需要域）セルは † を付す。"""
    qs = sorted(byq["q"].unique())
    header = "シナリオ " + "".join(f"& {int(q)} " for q in qs) + "\\\\"
    lines = [
        "% 柱A Q別内訳: シナリオ×総流入Q の締切達成率 [%]（f・seed 平均、要求数重み）。",
        "% † = 作動包絡外（超過需要域: weave Q>3000 / weave2 Q>3500。物理容量超過の作動限界）。",
        "% -- = 対象外（straight+障害物は流入時刻ユニーク上限により Q≤3500）。",
        "\\begin{tabular}{l" + "r" * len(qs) + "}",
        "\\hline \\hline",
        " & \\multicolumn{" + str(len(qs)) + "}{c}{総流入 $Q$ [台/h]} \\\\",
        header,
        "\\hline",
    ]
    for scen in SCENARIO_ORDER:
        sub = byq[byq["scenario"] == scen].set_index("q")
        cells = []
        for q in qs:
            if q not in sub.index:
                cells.append("--")
                continue
            r = sub.loc[q]
            cells.append(fmt_pct(r["achievement_pct"], mark="" if r["in_envelope"] else "$\\dagger$"))
        lines.append(f"{SCENARIO_LABELS[scen]} " + "".join(f"& {c} " for c in cells) + "\\\\")
    lines += ["\\hline", "\\end{tabular}"]
    return "\n".join(lines) + "\n"


def main() -> None:
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    df = load_proposed_long()

    # 主表（作動包絡内＝主結果）と全グリッド版（作動限界の明示用）
    env_tbl = main_table(df[df["in_envelope"]])
    env_tbl.to_csv(TABLE_DIR / "pillarA_main.csv", index=False)
    note = (
        f"作動包絡内（weave Q$\\le${OPERATING_ENVELOPE_QMAX['weave']} / "
        f"weave2 Q$\\le${OPERATING_ENVELOPE_QMAX['weave2']} / 他は全グリッド）。"
    )
    (TABLE_DIR / "pillarA_main.tex").write_text(main_tex(env_tbl, note))

    full_tbl = main_table(df)
    full_tbl.to_csv(TABLE_DIR / "pillarA_main_fullgrid.csv", index=False)
    (TABLE_DIR / "pillarA_main_fullgrid.tex").write_text(main_tex(full_tbl, "全グリッド（超過需要域込み・参考）。"))

    byq = by_q_table(df)
    byq.to_csv(TABLE_DIR / "pillarA_byQ.csv", index=False)
    (TABLE_DIR / "pillarA_byQ.tex").write_text(by_q_tex(byq))

    print(
        f"[make_tables] 出力: {TABLE_DIR}/pillarA_main.(csv|tex), pillarA_main_fullgrid.(csv|tex), pillarA_byQ.(csv|tex)"
    )
    print("\n=== 主表（作動包絡内） ===")
    print(env_tbl.to_string(index=False))
    print("\n=== 主表（全グリッド） ===")
    print(full_tbl.to_string(index=False))


if __name__ == "__main__":
    main()
