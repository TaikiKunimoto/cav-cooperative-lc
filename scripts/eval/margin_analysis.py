#!/usr/bin/env python3
"""完了余裕（margin）評価の集計・作図（柱C: 頑健性＋調停の質）。

入力: scripts/eval/out/raw/v2__{env}__Q{q}__f{f}__s{seed}__requests.csv
      （必須LC要求の個票。margin ログ実装後の run が出力する。成功要求を含む）
出力（scripts/eval/out/margin/ と out/figures/）:
  - margin_stats.csv    … シナリオ×Q×f の完了余裕統計（n・平均・5%点・95%点・最小・最大）
  - duration_stats.csv  … 同、所要時間（完了時刻 − 発生時刻）[s]
  - incomplete_stats.csv… 未完了要求数の内訳（作動包絡外の過飽和条件で発生し得る）
  - margin_scenario.csv / margin_scenario.tex … シナリオ別サマリ（論文表スタイルの tex 断片つき）
  - figures/fig_margin_cdf.(png|pdf)          … 完了余裕CDF（作動包絡内・シナリオ別4本、論文用）
  - figures/fig_margin_cdf_fullgrid.(png|pdf) … 参考: 全グリッド版

margin = deadline_pos − completion_pos [m]（締切位置の実測長補正込み。個票の値をそのまま使う）。
作動包絡（aggregate.py と同一定義）: weave は Q≤3000、weave2 は Q≤3500、diverge/merge は全域。

使い方（リポジトリ直下から）::

    uv run python scripts/eval/margin_analysis.py --seeds 1-3
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import ClassVar

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent))

from aggregate import OPERATING_ENVELOPE_QMAX  # noqa: E402  （作動包絡の定義を単一ソース化）
from run_eval import parse_seeds  # noqa: E402  （seed 範囲書式を run_eval と統一）

RAW_DIR = SCRIPT_DIR / "out" / "raw"
MARGIN_DIR = SCRIPT_DIR / "out" / "margin"
FIG_DIR = SCRIPT_DIR / "out" / "figures"

# 図仕様（共通ブロック）: 1カラム幅 8.4cm へ縮小して全文字 8pt 相当以上 → figsize≈(4.5,3.2)・fontsize≥12
FIGSIZE = (4.5, 3.2)
FONT_SIZE = 12

ENVS = ["diverge", "merge", "weave", "weave2"]
Q_LEVELS = [1500, 2000, 2500, 3000, 3500, 4000]
F_LEVELS = [0.2, 0.4, 0.6]

# 凡例のシナリオ名（プロンプト指定: 分流/合流/weave/weave2）
ENV_LABEL = {"diverge": "分流", "merge": "合流", "weave": "weave", "weave2": "weave2"}
# Okabe–Ito CVD 安全パレット（固定順）＋線種併用（色だけに頼らない）
ENV_COLOR = {"diverge": "#0072B2", "merge": "#D55E00", "weave": "#009E73", "weave2": "#CC79A7"}
ENV_LINESTYLE = {"diverge": "-", "merge": "--", "weave": "-.", "weave2": ":"}


class MarginDataset:
    """必須LC要求個票（__requests.csv 群）を読み込み、margin/所要時間を付与した tidy 表を保持する。

    期待グリッド（env×Q×f×seed）に対して個票ファイルの欠落・列不整合・完了行の欠損値を
    境界で検証し、想定外は受け取った値つきで即エラーにする（黙って除外しない）。
    """

    REQUIRED_COLS: ClassVar[set[str]] = {
        "veh_id",
        "env",
        "deadline_pos",
        "activation_time",
        "activation_pos",
        "completion_time",
        "completion_pos",
        "completed_in_time",
        "collided",
    }

    def __init__(self, df: pd.DataFrame):
        self.df = df

    @classmethod
    def load(cls, raw_dir: Path, seeds: list[int]) -> "MarginDataset":
        frames: list[pd.DataFrame] = []
        missing: list[str] = []
        for env in ENVS:
            for q in Q_LEVELS:
                for f in F_LEVELS:
                    for seed in seeds:
                        name = f"v2__{env}__Q{q}__f{f}__s{seed}"
                        path = raw_dir / f"{name}__requests.csv"
                        if not path.exists():
                            missing.append(name)
                            continue
                        d = pd.read_csv(path)
                        lack = cls.REQUIRED_COLS - set(d.columns)
                        if lack:
                            raise ValueError(f"個票の列が不足しています: {path.name} に {sorted(lack)} が無い")
                        d["scenario"], d["q"], d["f"], d["seed"] = env, q, f, seed
                        frames.append(d)
        if missing:
            raise FileNotFoundError(
                f"必須LC個票（__requests.csv）が {len(missing)} run 分ありません（margin ログ未実装の旧 run か "
                f"未実行）。先に scripts/run_eval.py を実行してください。例: {missing[:5]}"
            )
        df = pd.concat(frames, ignore_index=True)

        done = df["completed_in_time"].astype(bool)
        bad = df[done & (df["completion_pos"].isna() | df["completion_time"].isna())]
        if not bad.empty:
            raise ValueError(
                f"完了済み要求に完了時刻/位置の欠損が {len(bad)} 行あります: {bad.head(3).to_dict('records')}"
            )

        df["margin_m"] = df["deadline_pos"] - df["completion_pos"]
        df["duration_s"] = df["completion_time"] - df["activation_time"]
        df["in_envelope"] = [
            q <= OPERATING_ENVELOPE_QMAX.get(env, max(Q_LEVELS))
            for env, q in zip(df["scenario"], df["q"], strict=True)
        ]
        return cls(df)

    def completed(self, envelope_only: bool = False) -> pd.DataFrame:
        d = self.df[self.df["completed_in_time"].astype(bool)]
        return d[d["in_envelope"]] if envelope_only else d


class MarginReport:
    """負荷別統計CSV・シナリオ別サマリ（tex 断片つき）・CDF図を出力する。"""

    @staticmethod
    def _stats(d: pd.DataFrame, value_col: str) -> pd.DataFrame:
        g = d.groupby(["scenario", "q", "f"])[value_col]
        out = g.agg(
            n="size",
            mean="mean",
            p5=lambda s: s.quantile(0.05),
            p95=lambda s: s.quantile(0.95),
            min="min",
            max="max",
        ).reset_index()
        out["in_envelope"] = [
            q <= OPERATING_ENVELOPE_QMAX.get(env, max(Q_LEVELS))
            for env, q in zip(out["scenario"], out["q"], strict=True)
        ]
        return out.round({"mean": 1, "p5": 1, "p95": 1, "min": 1, "max": 1})

    @classmethod
    def write_stats(cls, ds: MarginDataset) -> None:
        MARGIN_DIR.mkdir(parents=True, exist_ok=True)
        comp = ds.completed()
        cls._stats(comp, "margin_m").to_csv(MARGIN_DIR / "margin_stats.csv", index=False)
        cls._stats(comp, "duration_s").to_csv(MARGIN_DIR / "duration_stats.csv", index=False)

        inc = ds.df[~ds.df["completed_in_time"].astype(bool)]
        inc_g = (
            inc.groupby(["scenario", "q", "f"]).size().reset_index(name="incomplete")
            if not inc.empty
            else pd.DataFrame(columns=["scenario", "q", "f", "incomplete"])
        )
        inc_g.to_csv(MARGIN_DIR / "incomplete_stats.csv", index=False)

    @staticmethod
    def write_scenario_summary(ds: MarginDataset) -> pd.DataFrame:
        """作動包絡内のシナリオ別 margin サマリを CSV と論文スタイル tex 断片で出力する。"""
        comp = ds.completed(envelope_only=True)
        g = comp.groupby("scenario")["margin_m"]
        summary = g.agg(n="size", mean="mean", p5=lambda s: s.quantile(0.05), min="min").reindex(ENVS).reset_index()
        summary = summary.round({"mean": 1, "p5": 1, "min": 1})
        summary.to_csv(MARGIN_DIR / "margin_scenario.csv", index=False)

        lines = [
            "% 完了余裕 [m] のシナリオ別統計（作動包絡内・全条件プール）。margin_scenario.csv から生成",
            "\\begin{tabular}{lrrrr}",
            "\\hline \\hline",
            "シナリオ & 要求数 & 平均 [m] & 5\\%点 [m] & 最小 [m] \\\\",
            "\\hline",
        ]
        for _, r in summary.iterrows():
            lines.append(
                f"{ENV_LABEL[r['scenario']]} & {int(r['n'])} & {r['mean']:.1f} & {r['p5']:.1f} & {r['min']:.1f} \\\\"
            )
        lines += ["\\hline", "\\end{tabular}", ""]
        (MARGIN_DIR / "margin_scenario.tex").write_text("\n".join(lines), encoding="utf-8")
        return summary

    @staticmethod
    def plot_cdf(ds: MarginDataset, envelope_only: bool, stem: str) -> None:
        plt.rcParams.update(
            {
                "font.family": "Noto Sans CJK JP",
                "font.size": FONT_SIZE,
                "axes.labelsize": FONT_SIZE,
                "legend.fontsize": FONT_SIZE - 1,
                "xtick.labelsize": FONT_SIZE - 1,
                "ytick.labelsize": FONT_SIZE - 1,
            }
        )
        comp = ds.completed(envelope_only=envelope_only)
        fig, ax = plt.subplots(figsize=FIGSIZE)
        for env in ENVS:
            m = comp.loc[comp["scenario"] == env, "margin_m"].sort_values().to_numpy()
            if len(m) == 0:
                raise ValueError(f"CDF 対象の完了要求が 0 件です: scenario={env} envelope_only={envelope_only}")
            y = [(i + 1) / len(m) * 100 for i in range(len(m))]
            ax.plot(m, y, color=ENV_COLOR[env], linestyle=ENV_LINESTYLE[env], linewidth=2.0, label=ENV_LABEL[env])
        ax.set_xlabel("完了余裕 [m]")
        ax.set_ylabel("累積割合 [%]")
        ax.set_ylim(0, 100)
        ax.set_xlim(left=0)
        ax.grid(True, linewidth=0.4, alpha=0.35)
        ax.legend()
        FIG_DIR.mkdir(parents=True, exist_ok=True)
        for ext in ("png", "pdf"):
            fig.savefig(FIG_DIR / f"{stem}.{ext}", dpi=300, bbox_inches="tight")
        plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description="完了余裕（margin）評価の集計・作図（柱C）")
    ap.add_argument("--seeds", default="1-3", help='seed 範囲（例 "1-3" / "1,3,5"。run_eval と同書式）')
    args = ap.parse_args()

    ds = MarginDataset.load(RAW_DIR, parse_seeds(args.seeds))
    MarginReport.write_stats(ds)
    summary = MarginReport.write_scenario_summary(ds)
    MarginReport.plot_cdf(ds, envelope_only=True, stem="fig_margin_cdf")
    MarginReport.plot_cdf(ds, envelope_only=False, stem="fig_margin_cdf_fullgrid")

    n_all = len(ds.df)
    n_done = int(ds.df["completed_in_time"].astype(bool).sum())
    print(f"[margin] 要求 {n_all} 件（完了 {n_done} / 未完了 {n_all - n_done}）")
    print(summary.to_string(index=False))
    print(f"[margin] 出力: {MARGIN_DIR}/ と {FIG_DIR}/fig_margin_cdf.(png|pdf)")


if __name__ == "__main__":
    main()
